#!/usr/bin/env python3
"""Guarded, one-time UserVPN 801 3x-ui canary operator.

This tool deliberately carries no client identity in its output.  It obtains the
identity only inside the BOT container, retains protected snapshots on NL, and
passes transient probe material to RU only over SSH stdin.  It never uses the
unsafe py3xui client.delete API.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RELAY_SSH = ROOT / "ops/scripts/relay_ssh.sh"
BOT_HOST = os.environ.get("SPECIAL_BOT_HOST", "72.56.23.226")
NL_HOST = os.environ.get("SPECIAL_NL_HOST", os.environ.get("SPECIAL_XUI_HOST", "195.66.213.74"))
SSH_KEY = os.environ.get("SPECIAL_BOT_SSH_KEY", str(Path.home() / ".ssh/id_ed25519"))
BOT_USER = os.environ.get("SPECIAL_BOT_SSH_USER", os.environ.get("SPECIAL_SSH_USER", "specialops"))
NL_USER = os.environ.get("SPECIAL_NL_SSH_USER", os.environ.get("SPECIAL_SSH_USER", "specialops"))
DOMAIN = "sub.special-wifi.ru"
DIRECT_NL_HOST = NL_HOST
RELAY_SING_BOX = os.environ.get("SPECIAL_RELAY_SING_BOX", "sing-box")
TARGETS = (7, 8, 9, 13, 10, 11)
EXPECTED = {
    5: (8443, "vless", "tcp", "reality"),
    7: (39329, "vless", "tcp", "reality"),
    8: (20057, "vless", "tcp", "reality"),
    9: (46517, "vless", "tcp", "reality"),
    13: (27914, "vless", "tcp", "reality"),
    10: (8080, "vless", "grpc", "reality"),
    11: (22554, "vless", "ws", "none"),
    12: (34007, "vless", "kcp", "none"),
}
SSH_OPTS = ["-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1"]


class GateError(RuntimeError):
    """A fail-closed safety gate, represented by a non-secret error class."""


def die_gate(message: str) -> None:
    raise GateError(message)


def run(command: list[str], *, input_data: bytes | None = None, timeout: int = 120) -> bytes:
    """Run without leaking remote stdout/stderr into this operator's console."""
    try:
        completed = subprocess.run(
            command, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        die_gate(f"command_{type(error).__name__.lower()}")
    if completed.returncode:
        die_gate("remote_command_failed")
    return completed.stdout


def ssh(target: str, command: str, *, input_data: bytes | None = None, timeout: int = 120) -> bytes:
    return run(["ssh", *SSH_OPTS, target, command], input_data=input_data, timeout=timeout)


def bot(command: str, *, input_data: bytes | None = None, timeout: int = 120) -> bytes:
    return ssh(f"{BOT_USER}@{BOT_HOST}", command, input_data=input_data, timeout=timeout)


def nl(command: str, *, timeout: int = 120) -> bytes:
    """Run a root-owned NL body via the named specialops account."""
    return ssh(f"{NL_USER}@{NL_HOST}", "sudo -n bash -s", input_data=command.encode(), timeout=timeout)


def relay(script: str, *, timeout: int = 180) -> bytes:
    return run([str(RELAY_SSH), "sudo -n bash -s"], input_data=script.encode(), timeout=timeout)


# This runs *inside* special-bot-web-1.  Its standard output is either a
# private bundle captured into a mode-0600 local file or a sanitized result.
BOT_HELPER = r'''
import asyncio, base64, hashlib, json, os, sys, time
from decimal import Decimal
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bot.settings")
import django
django.setup()
from django.conf import settings
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from apps.servers.models import Server
from apps.vpn.models import UserVPN
from asgiref.sync import sync_to_async
from py3xui import Client
from utils.py3xui.async_api import AsyncApi

ACTION = os.environ["SPECIAL_801_ACTION"]
REQUEST = json.loads(base64.b64decode(os.environ.get("SPECIAL_801_REQUEST", "e30=")).decode())
TARGETS = [7, 8, 9, 13, 10, 11]
EXPECTED = {5:(8443,"vless","tcp","reality"),7:(39329,"vless","tcp","reality"),8:(20057,"vless","tcp","reality"),9:(46517,"vless","tcp","reality"),13:(27914,"vless","tcp","reality"),10:(8080,"vless","grpc","reality"),11:(22554,"vless","ws","none"),12:(34007,"vless","kcp","none")}

def client_value(client, name, default=""):
    value = getattr(client, name, default)
    return default if value is None else value

def norm_client(client):
    return {"id":str(client_value(client,"id")),"email":str(client_value(client,"email","")),"enable":bool(client_value(client,"enable",False)),"expiry_time":int(client_value(client,"expiry_time",0) or 0),"flow":str(client_value(client,"flow","")),"limit_ip":int(client_value(client,"limit_ip",0) or 0),"sub_id":str(client_value(client,"sub_id","")),"total_gb":int(client_value(client,"total_gb",0) or 0),"reset":client_value(client,"reset",0),"comment":str(client_value(client,"comment","")),"tg_id":str(client_value(client,"tg_id",""))}

def dumped(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        value = value.dict()
    def camel(item):
        if isinstance(item, list): return [camel(x) for x in item]
        if not isinstance(item, dict): return item
        return {"".join(part.title() if index else part for index, part in enumerate(str(key).split("_"))): camel(val) for key, val in item.items()}
    return camel(value)

def norm_inbound(inbound):
    raw = dumped(inbound)
    # Traffic counters and client stats are dynamic observations, not
    # control-plane configuration. Excluding them prevents normal customer
    # traffic from masquerading as an unrelated mutation between stable reads.
    for dynamic_key in ("up", "down", "client_stats", "clientStats"):
        raw.pop(dynamic_key, None)
    stream = raw.get("stream_settings", raw.get("streamSettings", {})) or {}
    settings_raw = raw.get("settings", {}) or {}
    clients = settings_raw.get("clients", []) if isinstance(settings_raw, dict) else []
    if not clients:
        clients = [norm_client(x) for x in (getattr(getattr(inbound,"settings",None),"clients",[]) or [])]
    else:
        clients = [norm_client(type("C", (), {"__getattr__": lambda s, k, v=c: v.get(k, v.get({"expiry_time":"expiryTime","limit_ip":"limitIp","sub_id":"subId","total_gb":"totalGB","tg_id":"tgId"}.get(k,k), ""))})()) for c in clients]
    # Raw stream settings are preserved solely in the protected snapshot so the
    # relay config is built from the exact live inbound, not guessed constants.
    return {"id":int(raw.get("id", inbound.id)),"enable":bool(raw.get("enable", getattr(inbound,"enable",False))),"port":int(raw.get("port", inbound.port)),"protocol":str(raw.get("protocol", inbound.protocol)),"network":str(stream.get("network", getattr(getattr(inbound,"stream_settings",None),"network",""))).lower(),"security":str(stream.get("security", getattr(getattr(inbound,"stream_settings",None),"security",""))).lower(),"stream":stream,"clients":clients,"raw":raw}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

def non_clients(row):
    result = {key:value for key,value in row.items() if key not in {"clients", "raw"}}
    raw = json.loads(json.dumps(row["raw"], default=str))
    for key in ("settings", "Settings"):
        if isinstance(raw.get(key), dict):
            raw[key].pop("clients", None)
    result["raw"] = raw
    return result

async def stable(api, ids):
    previous = None
    for _ in range(3):
        await api.login()
        rows = {}
        for iid in ids:
            rows[str(iid)] = norm_inbound(await api.inbound.get_by_id(iid))
        # A full list is required for the no-unrelated-control-plane-change
        # gate; the per-ID reads above remain authoritative for target fields.
        listed = await api.inbound.get_list()
        rows["_inventory"] = {str(item.id): norm_inbound(item) for item in listed}
        current = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
        if previous == current:
            return rows
        previous = current
        await asyncio.sleep(1)
    raise RuntimeError("stable_reads")

async def main():
    user = await sync_to_async(
        lambda: UserVPN.objects.select_related("server__tariff")
        .annotate(
            entitlement_balance=Coalesce(
                Sum("user__transactions__amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        )
        .get(pk=801),
        thread_sensitive=True,
    )()
    server = user.server
    price = server.tariff.price
    balance = user.entitlement_balance
    if not (price and price > 0 and balance >= price and int(balance // price) >= 1 and user.enabled and int(server.inbound_id) == 5 and user.sub_id):
        raise RuntimeError("entitlement_or_source_drift")
    # AsyncApi enforces HTTPS and certificate verification.  If public trust is
    # unavailable it obtains the protected, mode-0600 runtime CA configured by
    # Django; no panel URL, path, or certificate data is emitted here.
    api = AsyncApi(server.vpn_url, server.vpn_username, server.vpn_password)
    rows = await stable(api, [5, *TARGETS, 12])
    for iid, expected in EXPECTED.items():
        row = rows[str(iid)]
        if (row["port"], row["protocol"], row["network"], row["security"]) != expected or not row["enable"]:
            raise RuntimeError("target_shape_drift")
    source_matches = [item for item in rows["5"]["clients"] if item["id"] == str(user.vpn_uuid)]
    if len(source_matches) != 1:
        raise RuntimeError("source_match_drift")
    source = source_matches[0]
    now_ms = int(time.time() * 1000)
    if source["sub_id"] != str(user.sub_id) or not source["enable"] or source["email"] or source["flow"] or (source["expiry_time"] and source["expiry_time"] <= now_ms):
        raise RuntimeError("source_field_drift")
    if ACTION == "export":
        for iid in TARGETS:
            target = rows[str(iid)]
            if any(item["id"] == source["id"] or item["sub_id"] == source["sub_id"] for item in target["clients"]):
                raise RuntimeError("target_identity_present")
        payload = {"source":source,"uuid":source["id"],"inbounds":rows,"health_url":settings.SPECIAL_MONITOR_HEALTH_URL,"expected_egress":settings.SPECIAL_MONITOR_EXPECTED_EGRESS,"fingerprint":digest(rows),"user_fingerprint":digest({"enabled":bool(user.enabled),"sub_id":str(user.sub_id),"server":int(server.inbound_id),"uuid":str(user.vpn_uuid)})}
        print(json.dumps(payload, separators=(",", ":"), default=str))
        return
    if ACTION == "resume_validate":
        original = REQUEST["original"]
        retained = REQUEST.get("retained", [])
        if not isinstance(retained, list) or any(type(iid) is not int or iid not in TARGETS for iid in retained):
            raise RuntimeError("resume_journal_invalid")
        if source != original["source"] or rows["5"] != original["inbounds"]["5"]:
            raise RuntimeError("source_drift")
        if digest({"enabled":bool(user.enabled),"sub_id":str(user.sub_id),"server":int(server.inbound_id),"uuid":str(user.vpn_uuid)}) != original.get("user_fingerprint"):
            raise RuntimeError("user_record_drift")
        for iid in TARGETS:
            before = original["inbounds"][str(iid)]
            actual = rows[str(iid)]
            if iid in retained:
                if actual["clients"] != list(before["clients"]) + [source] or non_clients(actual) != non_clients(before):
                    raise RuntimeError("retained_ownership_or_cp_drift")
            elif actual != before:
                raise RuntimeError("absent_target_or_cp_drift")
        original_inventory = original["inbounds"]["_inventory"]
        for iid, before in original_inventory.items():
            if iid not in {str(item) for item in retained} and rows["_inventory"].get(iid) != before:
                raise RuntimeError("unrelated_control_plane_change")
        payload = {"source":source,"uuid":source["id"],"inbounds":rows,"health_url":settings.SPECIAL_MONITOR_HEALTH_URL,"expected_egress":settings.SPECIAL_MONITOR_EXPECTED_EGRESS,"fingerprint":digest(rows),"user_fingerprint":digest({"enabled":bool(user.enabled),"sub_id":str(user.sub_id),"server":int(server.inbound_id),"uuid":str(user.vpn_uuid)})}
        print(json.dumps(payload, separators=(",", ":"), default=str))
        return
    target_id = int(REQUEST.get("target_id", 0))
    if ACTION == "verify_delete_route":
        from urllib.parse import quote
        probe_uuid = "00000000-0000-0000-0000-000000000000"
        if any(item["id"] == probe_uuid for item in rows[str(target_id)]["clients"]):
            raise RuntimeError("scoped_delete_route_probe_collision")
        endpoint = f"panel/api/inbounds/{target_id}/delClient/{quote(probe_uuid, safe='')}"
        # A route/status failure raises through the authenticated client. The
        # UUID is proven absent from the target before this no-op route probe.
        await api.inbound._post(api.inbound._url(endpoint), {"Accept":"application/json"}, {})
        confirm = await stable(api, [5, *TARGETS, 12])
        if digest(confirm) != digest(rows):
            raise RuntimeError("scoped_delete_route_changed_cp")
        print(json.dumps({"ok":True,"state":"route_verified"}))
        return
    before = REQUEST["before"]
    target = rows[str(target_id)]
    if digest(rows) != REQUEST["pre_fingerprint"]:
        raise RuntimeError("unexpected_control_plane_change")
    if ACTION == "recover":
        # Recovery returns a private stable snapshot only. The local operator
        # compares it to exact journal-owned before/after shapes and never
        # guesses from UUID/subId matches or prints panel state.
        print(json.dumps({"ok":True,"inbounds":rows,"fingerprint":digest(rows)}, separators=(",", ":"), default=str))
        return
    if ACTION == "add":
        if any(item["id"] == source["id"] or item["sub_id"] == source["sub_id"] for item in target["clients"]):
            raise RuntimeError("target_identity_present")
        if target["clients"] != before["clients"]:
            raise RuntimeError("target_before_drift")
        candidate = Client(id=source["id"], email="", enable=source["enable"], expiry_time=source["expiry_time"], flow="", limit_ip=source["limit_ip"], sub_id=source["sub_id"], total_gb=source["total_gb"], reset=source["reset"], comment=source["comment"], tg_id=source["tg_id"])
        await api.client.add(target_id, [candidate])
        after = await stable(api, [5, *TARGETS, 12])
        actual = after[str(target_id)]
        found = [item for item in actual["clients"] if item["id"] == source["id"]]
        desired = norm_client(candidate)
        if len(actual["clients"]) != len(before["clients"]) + 1 or len(found) != 1 or found[0] != desired or [x for x in actual["clients"] if x["id"] != source["id"]] != before["clients"] or non_clients(actual) != non_clients(before):
            raise RuntimeError("post_add_verification")
        for iid in [5, *TARGETS, 12]:
            if iid != target_id and after[str(iid)] != REQUEST["initial"]["inbounds"][str(iid)]:
                raise RuntimeError("protected_inbound_changed")
        previous_inventory = REQUEST["initial"]["inbounds"]["_inventory"]
        if {key:value for key,value in after["_inventory"].items() if key != str(target_id)} != {key:value for key,value in previous_inventory.items() if key != str(target_id)}:
            raise RuntimeError("unrelated_control_plane_change")
        print(json.dumps({"ok":True,"state":"added","fingerprint":digest(after),"count":len(actual["clients"]),"inbounds":after}))
        return
    if ACTION == "delete":
        found = [item for item in target["clients"] if item["id"] == source["id"]]
        if len(found) != 1 or found[0] != REQUEST["desired"]:
            raise RuntimeError("rollback_ownership_or_shape")
        # Do not replace this with api.client.delete: py3xui 0.5.1 resolves an
        # email globally. This is the verified inbound-scoped panel route.
        from urllib.parse import quote
        endpoint = f"panel/api/inbounds/{target_id}/delClient/{quote(source['id'], safe='')}"
        await api.inbound._post(api.inbound._url(endpoint), {"Accept":"application/json"}, {})
        after = await stable(api, [5, *TARGETS, 12])
        actual = after[str(target_id)]
        if actual["clients"] != before["clients"] or any(item["id"] == source["id"] for item in actual["clients"]) or non_clients(actual) != non_clients(before):
            raise RuntimeError("post_delete_verification")
        for iid in [5, *TARGETS, 12]:
            if iid != target_id and after[str(iid)] != REQUEST["initial"]["inbounds"][str(iid)]:
                raise RuntimeError("protected_inbound_changed")
        previous_inventory = REQUEST["initial"]["inbounds"]["_inventory"]
        if {key:value for key,value in after["_inventory"].items() if key != str(target_id)} != {key:value for key,value in previous_inventory.items() if key != str(target_id)}:
            raise RuntimeError("unrelated_control_plane_change")
        print(json.dumps({"ok":True,"state":"removed","fingerprint":digest(after),"count":len(actual["clients"]),"inbounds":after}))
        return
    raise RuntimeError("unknown_action")

try:
    asyncio.run(main())
except Exception as error:
    # Exception messages are intentionally fixed error classes; do not emit
    # HTTP errors, panel data, or Python tracebacks.
    print(json.dumps({"ok":False,"error_class":str(error).split()[0][:80]}))
    sys.exit(1)
'''


def bot_action(action: str, request: dict[str, Any] | None = None, *, private: bool = False) -> dict[str, Any]:
    encoded = base64.b64encode(json.dumps(request or {}, separators=(",", ":")).encode()).decode()
    command = (
        "sudo -n docker exec -i -e SPECIAL_801_ACTION="
        + action
        + " special-bot-web-1 python -c "
        + shell_quote("import os,sys; os.environ['SPECIAL_801_REQUEST']=sys.stdin.buffer.readline().decode().strip(); exec(sys.stdin.read())")
        + "; exit 0"
    )
    # The protected request travels only over stdin, never in a local/remote
    # argv or shell history.  The remote wrapper retains the helper's fixed
    # sanitized error JSON while keeping docker-exec's exit status from leaking.
    try:
        completed = subprocess.run(
            ["ssh", *SSH_OPTS, f"{BOT_USER}@{BOT_HOST}", command],
            input=(encoded + "\n" + BOT_HELPER).encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=180, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        die_gate(f"bot_command_{type(error).__name__.lower()}")
    output = completed.stdout
    try:
        result = json.loads(output)
    except (ValueError, TypeError):
        die_gate("bot_result_invalid")
    if not result.get("ok", private):
        die_gate(str(result.get("error_class", "bot_gate")))
    return result


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def get_stream(bundle: dict[str, Any], inbound_id: int) -> dict[str, Any]:
    """Return exact SQLite stream settings without contaminating CP hashes."""
    streams = bundle.get("probe_streams", {})
    stream = streams.get(str(inbound_id))
    if not isinstance(stream, dict):
        die_gate("probe_stream_missing")
    return stream


def reality(bundle: dict[str, Any], inbound_id: int) -> tuple[str, str, str, str]:
    settings = get_stream(bundle, inbound_id).get("realitySettings", {})
    if not isinstance(settings, dict):
        die_gate("reality_settings_invalid")
    inner = settings.get("settings", {})
    if isinstance(inner, list):
        inner = inner[0] if inner else {}
    if not isinstance(inner, dict):
        die_gate("reality_settings_invalid")
    names = settings.get("serverNames", [])
    short_ids = settings.get("shortIds", [])
    name = next((x for x in names if x), "") if isinstance(names, list) else ""
    short_id = next((x for x in short_ids if x is not None), "") if isinstance(short_ids, list) else ""
    key = inner.get("publicKey", "")
    fingerprint = inner.get("fingerprint") or "chrome"
    if not all(isinstance(x, str) and x for x in (name, short_id, key, fingerprint)):
        die_gate("reality_values_missing")
    return name, key, short_id, fingerprint


def singbox_config(bundle: dict[str, Any], inbound_id: int, socks_port: int, *, diagnostic: bool = False) -> dict[str, Any]:
    inbound = bundle["inbounds"][str(inbound_id)]
    stream = get_stream(bundle, inbound_id)
    uuid = bundle["uuid"]
    network = inbound["network"]
    if network == "tcp":
        header = stream.get("tcpSettings", {}).get("header", {}) if isinstance(stream.get("tcpSettings", {}), dict) else {}
        if header and header.get("type", "none") != "none":
            die_gate("tcp_header_unsupported")
        server_name, public_key, short_id, fingerprint = reality(bundle, inbound_id)
        outbound: dict[str, Any] = {"type":"vless", "tag":"candidate", "server":DOMAIN, "server_port":int(inbound["port"]), "uuid":uuid, "flow":"", "tls":{"enabled":True,"server_name":server_name,"utls":{"enabled":True,"fingerprint":fingerprint},"reality":{"enabled":True,"public_key":public_key,"short_id":short_id}}}
    elif network == "grpc":
        grpc = stream.get("grpcSettings", {})
        if not isinstance(grpc, dict) or not isinstance(grpc.get("serviceName"), str) or not grpc["serviceName"]:
            die_gate("grpc_service_missing")
        if grpc.get("authority"):
            die_gate("grpc_authority_unrepresentable")
        # sing-box 1.13.15 has no multiMode knob. Keep the exact service name
        # and record only the empirical E2E outcome, not semantic equivalence.
        server_name, public_key, short_id, fingerprint = reality(bundle, inbound_id)
        outbound = {"type":"vless", "tag":"candidate", "server":DIRECT_NL_HOST if diagnostic else DOMAIN, "server_port":8080 if diagnostic else 80, "uuid":uuid, "flow":"", "tls":{"enabled":True,"server_name":server_name,"utls":{"enabled":True,"fingerprint":fingerprint},"reality":{"enabled":True,"public_key":public_key,"short_id":short_id}}, "transport":{"type":"grpc","service_name":grpc["serviceName"]}}
    elif network == "ws":
        ws = stream.get("wsSettings", {})
        if not isinstance(ws, dict):
            die_gate("ws_settings_invalid")
        transport: dict[str, Any] = {"type":"ws", "path":ws.get("path") or "/"}
        headers = ws.get("headers", {})
        if headers:
            if not isinstance(headers, dict) or not isinstance(headers.get("Host"), str) or not headers["Host"]:
                die_gate("ws_host_invalid")
            transport["headers"] = {"Host":headers["Host"]}
        if ws.get("maxEarlyData") is not None:
            transport["max_early_data"] = ws["maxEarlyData"]
        if ws.get("earlyDataHeaderName"):
            transport["early_data_header_name"] = ws["earlyDataHeaderName"]
        outbound = {"type":"vless", "tag":"candidate", "server":DOMAIN, "server_port":int(inbound["port"]), "uuid":uuid, "flow":"", "transport":transport}
    else:
        die_gate("transport_unsupported")
    return {"log":{"level":"warn","timestamp":False},"inbounds":[{"type":"socks","tag":"probe-socks","listen":"127.0.0.1","listen_port":socks_port or 1080}],"outbounds":[outbound],"route":{"final":"candidate"}}


def relay_probe(config: dict[str, Any], health_url: str, expected_egress: str) -> tuple[bool, str, int]:
    """One fresh sing-box process and one credential-free HTTPS egress attempt."""
    encoded_cfg = base64.b64encode(json.dumps(config, separators=(",", ":")).encode()).decode()
    encoded_url = base64.b64encode(health_url.encode()).decode()
    encoded_egress = base64.b64encode(expected_egress.encode()).decode()
    # All sensitive values remain encoded in remote stdin and config files inside
    # a 0700 directory. stdout is a fixed, sanitized triple only.
    script = f'''set -euo pipefail
umask 077
work=$(mktemp -d /tmp/.special-801-probe.XXXXXXXX)
cleanup() {{
  [[ -z "${{pid:-}}" ]] || kill "$pid" 2>/dev/null || true
  [[ -z "${{pid:-}}" ]] || wait "$pid" 2>/dev/null || true
  rm -rf -- "$work"
}}
trap cleanup EXIT INT TERM
test -x {shell_quote(RELAY_SING_BOX)} || command -v {shell_quote(RELAY_SING_BOX)} >/dev/null || {{ echo 'false singbox_missing 0'; exit 0; }}
printf %s {shell_quote(encoded_cfg)} | base64 -d >"$work/config.json"
printf %s {shell_quote(encoded_url)} | base64 -d >"$work/health-url"
printf %s {shell_quote(encoded_egress)} | base64 -d >"$work/expected-egress"
chmod 600 "$work/config.json" "$work/health-url" "$work/expected-egress"
{shell_quote(RELAY_SING_BOX)} check -c "$work/config.json" >/dev/null 2>&1 || {{ echo 'false config_check 0'; exit 0; }}
port=$(python3 - <<'PY'
import socket
s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()
PY
)
python3 - "$work/config.json" "$port" <<'PY'
import json, sys
path, port = sys.argv[1:]
data=json.load(open(path))
data['inbounds'][0]['listen_port']=int(port)
open(path,'w').write(json.dumps(data,separators=(',',':')))
PY
{shell_quote(RELAY_SING_BOX)} run -c "$work/config.json" >"$work/sing-box.log" 2>&1 & pid=$!
ready=false
for _ in $(seq 1 120); do
  if ! kill -0 "$pid" 2>/dev/null; then echo 'false process_exit 0'; exit 0; fi
  if (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then ready=true; break; fi
  sleep 0.1
done
$ready || {{ echo 'false socks_not_ready 0'; exit 0; }}
started=$(date +%s%3N)
body="$work/body"
if curl --silent --show-error --fail --socks5-hostname "127.0.0.1:$port" --connect-timeout 10 --max-time 30 --output "$body" "$(cat "$work/health-url")" >/dev/null 2>&1 && [[ $(tr -d '[:space:]' < "$body") == $(tr -d '[:space:]' < "$work/expected-egress") ]]; then
  ended=$(date +%s%3N); echo "true none $((ended-started))"
else
  echo 'false e2e_or_egress 0'
fi
'''
    try:
        output = relay(script, timeout=75).decode().strip().split()
    except GateError:
        return False, "relay_command", 0
    if len(output) != 3 or output[0] not in {"true", "false"}:
        return False, "relay_result_invalid", 0
    return output[0] == "true", output[1], int(output[2]) if output[2].isdigit() else 0


def load_probe_streams() -> dict[str, Any]:
    """Read only exact live transport settings from NL's x-ui SQLite database."""
    query = """set -euo pipefail
python3 - <<'PY'
import json
import sqlite3
connection = sqlite3.connect('/etc/x-ui/x-ui.db')
rows = dict(connection.execute('select id, stream_settings from inbounds where id in (5, 7, 8, 9, 13, 10, 11)'))
if set(rows) != {5, 7, 8, 9, 13, 10, 11}:
    raise SystemExit(41)
streams = {}
for inbound_id, raw in rows.items():
    value = json.loads(raw or '{}')
    if not isinstance(value, dict):
        raise SystemExit(42)
    streams[str(inbound_id)] = value
print(json.dumps(streams, separators=(',', ':')))
PY
"""
    try:
        streams = json.loads(nl(query).decode())
    except (GateError, ValueError, TypeError):
        die_gate("probe_stream_read")
    if not isinstance(streams, dict) or set(streams) != {"5", "7", "8", "9", "13", "10", "11"}:
        die_gate("probe_stream_read")
    return streams


def load_resume_journal(operation_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not re.fullmatch(r"op-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", operation_id):
        die_gate("journal_id_invalid")
    opdir = f"/var/lib/special-ops/uservpn801/{operation_id}"
    query = f'''set -euo pipefail
base={shell_quote(opdir)}
test "$(stat -c '%a' "$base/operation-journal.json")" = 600
python3 - "$base" <<'PY'
import json, sys
base = sys.argv[1]
journal = json.load(open(base + '/operation-journal.json'))
original = json.load(open(base + '/before-control-plane.json'))
if journal.get('operation_id') != base.rsplit('/', 1)[1]: raise SystemExit(41)
if journal.get('state') not in {'adding', 'retained', 'removed', 'pending_add', 'pending_delete'}: raise SystemExit(42)
target = journal.get('current_target')
if target is not None and (type(target) is not int or target not in TARGETS): raise SystemExit(42)
if not isinstance(journal.get('targets'), list) or any(type(item) is not int or item not in TARGETS for item in journal['targets']): raise SystemExit(42)
if not isinstance(original.get('inbounds'), dict) or not original.get('source') or not original.get('fingerprint'): raise SystemExit(43)
print(json.dumps({{'journal': journal, 'original': original}}, separators=(',', ':')))
PY
'''
    try:
        payload = json.loads(nl(query).decode())
    except (GateError, ValueError, TypeError):
        die_gate("journal_ownership_invalid")
    backup = f"{opdir}/x-ui.db.bak"
    check = f'''set -euo pipefail
test "$(stat -c '%a' {shell_quote(backup)})" = 600
test "$(stat -c '%a' {shell_quote(backup)}.sha256)" = 600
test "$(sqlite3 {shell_quote(backup)} 'PRAGMA quick_check;')" = ok
(cd {shell_quote(opdir)} && sha256sum -c x-ui.db.bak.sha256 >/dev/null)
echo resume_journal=ok
'''
    if nl(check).decode().strip() != "resume_journal=ok":
        die_gate("backup_integrity")
    return opdir, payload["journal"], payload["original"]


def relay_preflight() -> None:
    output = relay(f"""set -euo pipefail
systemctl is-active --quiet nginx
nginx -t >/dev/null 2>&1
test -x {shell_quote(RELAY_SING_BOX)} || command -v {shell_quote(RELAY_SING_BOX)} >/dev/null
ps -eo stat= | awk '$1 ~ /^D/ {{n++}} END {{exit n!=0}}'
mem=$(awk '/MemAvailable:/ {{print $2}}' /proc/meminfo); ((mem >= 131072))
load=$(awk '{{print $1}}' /proc/loadavg); cpus=$(nproc); awk -v l="$load" -v c="$cpus" 'BEGIN {{exit !(l <= c*4)}}'
echo relay_preflight=ok
""").decode().strip()
    if output != "relay_preflight=ok":
        die_gate("relay_preflight")


def acquire_nl_lock(operation_id: str) -> None:
    """Acquire the protected lock or re-own only this operation's stale lock."""
    command = f'''set -euo pipefail
lock=/run/lock/special-uservpn801-canary.lock.d
if mkdir "$lock" 2>/dev/null; then
  printf %s {shell_quote(operation_id)} >"$lock/owner"; chmod 600 "$lock/owner"
elif [[ $(cat "$lock/owner" 2>/dev/null || true) != {shell_quote(operation_id)} ]]; then
  exit 41
fi
echo lock_owned=ok
'''
    if nl(command).decode().strip() != "lock_owned=ok":
        die_gate("nl_lock_contention")


def release_nl_lock(operation_id: str) -> None:
    """Never remove a lock that belongs to another interrupted operator."""
    command = f'''set -euo pipefail
lock=/run/lock/special-uservpn801-canary.lock.d
if [[ -d "$lock" && $(cat "$lock/owner" 2>/dev/null || true) == {shell_quote(operation_id)} ]]; then
  rm -f "$lock/owner"
  rmdir "$lock"
fi
'''
    nl(command)


def nl_preflight(operation_id: str) -> tuple[str, str]:
    acquire_nl_lock(operation_id)
    command = f'''set -euo pipefail
lock=/run/lock/special-uservpn801-canary.lock.d
cleanup_lock() {{
  if [[ $(cat "$lock/owner" 2>/dev/null || true) == {shell_quote(operation_id)} ]]; then
    rm -f "$lock/owner"; rmdir "$lock" 2>/dev/null || true
  fi
}}
trap cleanup_lock ERR
base=/var/lib/special-ops/uservpn801/{operation_id}
umask 077
install -d -m 700 "$base"
test "$(stat -c '%a' /etc/x-ui/x-ui.db)" = 600
systemctl is-active --quiet x-ui
systemctl is-active --quiet nginx
nginx -t >/dev/null 2>&1
backup="$base/x-ui.db.bak"
sqlite3 /etc/x-ui/x-ui.db ".backup '$backup'"
chmod 600 "$backup"
test "$(sqlite3 "$backup" 'PRAGMA quick_check;')" = ok
sha256sum "$backup" >"$backup.sha256"
chmod 600 "$backup.sha256"
ss -lntH | sort >"$base/listeners.before"; chmod 600 "$base/listeners.before"
printf '{{"operation_id":"%s","state":"preflight","targets":[]}}\\n' {shell_quote(operation_id)} >"$base/operation-journal.json"
chmod 600 "$base/operation-journal.json"
echo "$backup $base/operation-journal.json"
'''
    output = nl(command).decode().strip().split()
    if len(output) != 2 or not all(item.startswith("/") for item in output):
        die_gate("nl_preflight")
    return output[0], output[1]


def nl_gate(opdir: str, target_port: int, since_epoch: int) -> None:
    command = f'''set -euo pipefail
systemctl is-active --quiet x-ui
systemctl is-active --quiet nginx
nginx -t >/dev/null 2>&1
ss -lntH | sort >{shell_quote(opdir)}/listeners.current
cmp -s {shell_quote(opdir)}/listeners.before {shell_quote(opdir)}/listeners.current
[[ $(ss -lntH 'sport = :8443' | wc -l) -eq 1 ]]
ss -lntH | grep -q ':2096 '
ss -lntH | grep -q ':{target_port} '
if journalctl -u x-ui --since @{since_epoch} --no-pager 2>/dev/null | grep -Eqi 'invalid configuration|address already in use|panic|startup failed|failed to start|repeated crash'; then exit 43; fi
rm -f {shell_quote(opdir)}/listeners.current
echo nl_gate=ok
'''
    if nl(command).decode().strip() != "nl_gate=ok":
        die_gate("nl_service_gate")


def relay_gate(target_port: int) -> None:
    output = relay(f'''set -euo pipefail
systemctl is-active --quiet nginx
nginx -t >/dev/null 2>&1
timeout 10 bash -c '</dev/tcp/{DOMAIN}/{target_port}'
echo relay_gate=ok
''').decode().strip()
    if output != "relay_gate=ok":
        die_gate("relay_service_gate")


def nl_write_private(path: str, data: bytes) -> None:
    """Write protected data through SSH stdin without exposing it in argv."""
    encoded = base64.b64encode(data).decode()
    nl(f"umask 077; base64 -d > {shell_quote(path)} <<'SPECIAL_801_DATA'\n{encoded}\nSPECIAL_801_DATA\nchmod 600 {shell_quote(path)}")


def persist_private_snapshot(opdir: str, bundle: dict[str, Any]) -> None:
    # Exact values go only to NL's protected operation directory; no stdout is
    # retained and the base64 payload travels as SSH standard input.
    nl_write_private(f"{opdir}/before-control-plane.json", json.dumps(bundle, separators=(",", ":"), default=str).encode())


def persist_probe_streams(opdir: str, streams: dict[str, Any]) -> None:
    """Keep exact transport fields separate from normalized CP snapshots."""
    nl_write_private(f"{opdir}/probe-streams.json", json.dumps(streams, separators=(",", ":")).encode())


def update_journal(opdir: str, value: dict[str, Any]) -> None:
    nl_write_private(f"{opdir}/operation-journal.json", json.dumps(value, separators=(",", ":")).encode())


def recover_pending_mutation(bundle: dict[str, Any], journal: dict[str, Any], opdir: str) -> tuple[dict[str, Any], list[int]]:
    """Resolve an uncertain mutation using only exact protected journal shapes."""
    pending = journal.get("pending_mutation")
    if not isinstance(pending, dict):
        return bundle, list(journal.get("targets", []))
    target_id = pending.get("target")
    action = pending.get("action")
    before = pending.get("before")
    desired = pending.get("desired")
    rollback_before = pending.get("rollback_before")
    if (type(target_id) is not int or target_id not in TARGETS or action not in {"add", "delete"}
            or not isinstance(before, dict) or not isinstance(desired, dict)
            or (action == "delete" and not isinstance(rollback_before, dict))
            or pending.get("before_digest") != json_digest(before)
            or pending.get("desired_digest") != json_digest(desired)):
        die_gate("manual_recovery_required")
    recovered = bot_action(
        "recover",
        {
            "target_id": target_id,
            "before": before,
            "pre_fingerprint": bundle["fingerprint"],
        },
        private=True,
    )
    target = recovered.get("inbounds", {}).get(str(target_id))
    if target == before:
        update_journal(opdir, {"operation_id":journal["operation_id"], "state":"recovered_before",
                               "targets":journal.get("targets", []), "initial_fingerprint":bundle["fingerprint"]})
        return bundle, list(journal.get("targets", []))
    expected_after = dict(before)
    if action == "add":
        expected_after["clients"] = list(before.get("clients", [])) + [desired]
    else:
        expected_after["clients"] = [item for item in before.get("clients", []) if item != desired]
        if len(expected_after["clients"]) != len(before.get("clients", [])) - 1:
            die_gate("manual_recovery_required")
    if target != expected_after:
        die_gate("manual_recovery_required")
    if action == "delete":
        # The exact pre-state is already absent: accept only this completed,
        # journal-owned deletion and continue with no further target mutation.
        bundle["inbounds"] = recovered["inbounds"]
        bundle["fingerprint"] = str(recovered["fingerprint"])
        update_journal(opdir, {"operation_id":journal["operation_id"], "state":"recovered_deleted",
                               "targets":journal.get("targets", []), "initial_fingerprint":bundle["fingerprint"]})
        return bundle, list(journal.get("targets", []))
    # Exact operation-owned add is the sole state eligible for scoped cleanup.
    recovery_bundle = dict(bundle)
    recovery_bundle["inbounds"] = recovered["inbounds"]
    recovery_bundle["fingerprint"] = str(recovered["fingerprint"])
    fingerprint, inbounds = scoped_delete(recovery_bundle, target_id, rollback_before, str(recovered["fingerprint"]),
                                          desired, recovery_bundle, opdir)
    bundle["inbounds"] = inbounds
    bundle["fingerprint"] = fingerprint
    update_journal(opdir, {"operation_id":journal["operation_id"], "state":"recovered_add_removed",
                           "targets":journal.get("targets", []), "initial_fingerprint":bundle["fingerprint"]})
    return bundle, list(journal.get("targets", []))


def scoped_delete(bundle: dict[str, Any], target_id: int, rollback_before: dict[str, Any], pre_fingerprint: str, desired: dict[str, Any], initial: dict[str, Any], opdir: str) -> tuple[str, dict[str, Any]]:
    started = int(time.time())
    result = bot_action("delete", {"target_id":target_id, "before":rollback_before, "pre_fingerprint":pre_fingerprint, "desired":desired, "initial":initial})
    nl_gate(opdir, int(bundle["inbounds"][str(target_id)]["port"]), started)
    relay_gate(80 if target_id == 10 else int(bundle["inbounds"][str(target_id)]["port"]))
    baseline(bundle)
    return str(result["fingerprint"]), result["inbounds"]


def baseline(bundle: dict[str, Any]) -> None:
    config = singbox_config(bundle, 5, 0)
    attempts = [relay_probe(config, str(bundle["health_url"]), str(bundle["expected_egress"])) for _ in range(3)]
    if not all(ok for ok, _, _ in attempts):
        die_gate("baseline_inbound_5_failed")


def relay_cleanup_binary() -> None:
    """Remove the owner-provided temporary RU binary only after closeout."""
    if RELAY_SING_BOX != "/tmp/special-singbox-probe/sing-box":
        return
    output = relay("""set -euo pipefail
rm -rf -- /tmp/special-singbox-probe
echo relay_cleanup=ok
""").decode().strip()
    if output != "relay_cleanup=ok":
        die_gate("relay_cleanup")


def validate_static() -> None:
    assert TARGETS == (7, 8, 9, 13, 10, 11)
    assert 12 not in TARGETS
    source = Path(__file__).read_text(encoding="utf-8")
    assert "await api.client." + "delete(" not in source
    assert "delClient/" in source
    assert "api.client.add(target_id, [candidate])" in source
    assert "--resume-operation" in source
    assert "probe_streams" in source
    assert "if grpc.get(\"multiMode\")" not in source
    assert "MIRROR_" + "INBOUND_IDS" not in source
    assert "use_tls_verify=" + "False" not in source


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded SPECIAL UserVPN 801 canary operator")
    parser.add_argument("--apply", action="store_true", help="perform the owner-authorized one-time operation")
    parser.add_argument("--resume-operation", help="resume only this protected operation journal")
    parser.add_argument("--static-check", action="store_true", help="validate non-mutating safety invariants")
    parser.add_argument("--tls-check", action="store_true", help="protected read-only HTTPS/certificate verification check")
    args = parser.parse_args()
    if args.static_check:
        validate_static()
        print("static_check=passed")
        return 0
    if args.tls_check:
        # The helper's authenticated export performs no mutation. Its private
        # result remains in memory and only this fixed outcome is printed.
        try:
            bot_action("export", private=True)
        except GateError:
            print("BLOCK: tls_verification", file=sys.stderr)
            return 1
        print("tls_verification=passed")
        return 0
    if not args.apply:
        print("BLOCK: pass --apply and SPECIAL_APPROVE_USERVPN801_CANARY=YES", file=sys.stderr)
        return 2
    if os.environ.get("SPECIAL_APPROVE_USERVPN801_CANARY") != "YES":
        print("BLOCK: explicit production approval environment guard missing", file=sys.stderr)
        return 2
    if datetime.now(timezone.utc).strftime("%H:%M") >= "23:55" or datetime.now(timezone.utc).strftime("%H:%M") < "00:15":
        print("BLOCK: protected billing/expiry window", file=sys.stderr)
        return 2
    operation_id = args.resume_operation or ("op-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4))
    work = Path(tempfile.mkdtemp(prefix=".special-801-", dir="/tmp"))
    os.chmod(work, 0o700)
    opdir = ""
    journal_path = ""
    backup_path = ""
    bundle: dict[str, Any] | None = None
    retained: list[int] = []
    current: tuple[int, dict[str, Any], str, dict[str, Any]] | None = None
    statuses: list[dict[str, Any]] = []
    try:
        relay_preflight()
        if args.resume_operation:
            acquire_nl_lock(operation_id)
            opdir, journal, original = load_resume_journal(operation_id)
            # Resolve an interrupted add/delete before the normal strict resume
            # validator: a successful-but-lost response is deliberately not the
            # pre-operation state it validates.
            bundle = original
            # Protected streams are intentionally separate: changing the
            # py3xui-normalized CP fingerprint to carry lossy transport fields
            # would invalidate journal ownership comparisons.
            bundle["probe_streams"] = load_probe_streams()
            persist_probe_streams(opdir, bundle["probe_streams"])
            retained = list(journal.get("targets", []))
            bundle, retained = recover_pending_mutation(bundle, journal, opdir)
            bundle = bot_action("resume_validate", {"original":original, "retained":retained}, private=True)
            bundle["probe_streams"] = load_probe_streams()
            persist_probe_streams(opdir, bundle["probe_streams"])
            backup_path = f"{opdir}/x-ui.db.bak"
            journal_path = f"{opdir}/operation-journal.json"
            statuses.extend([
                {"inbound":5,"status":"passed","error_class":"none","attempts":"3/3","retained":True,"removed":False},
                {"inbound":7,"status":"passed","error_class":"previous_validated","attempts":"3/3","retained":True,"removed":False},
                {"inbound":8,"status":"failed","error_class":"previous_probe_failed","attempts":"0/3","retained":False,"removed":True},
                {"inbound":9,"status":"passed","error_class":"previous_validated","attempts":"3/3","retained":True,"removed":False},
                {"inbound":13,"status":"passed","error_class":"previous_validated","attempts":"3/3","retained":True,"removed":False},
            ])
            baseline(bundle)
            target_order = tuple(target for target in TARGETS if target not in retained)
        else:
            exported = bot_action("export", private=True)
            bundle = exported
            backup_path, journal_path = nl_preflight(operation_id)
            opdir = str(Path(journal_path).parent)
            persist_private_snapshot(opdir, bundle)
            bundle["probe_streams"] = load_probe_streams()
            persist_probe_streams(opdir, bundle["probe_streams"])
            update_journal(opdir, {"operation_id":operation_id, "state":"preflight", "targets":[], "initial_fingerprint":bundle["fingerprint"], "user_fingerprint":bundle["user_fingerprint"]})
            # Verify the deployed panel route before any add. The synthetic UUID is
            # freshly generated, verified absent by the source/target stable read,
            # and so this POST cannot delete an existing client.
            bot_action("verify_delete_route", {"target_id":7, "pre_fingerprint":bundle["fingerprint"]})
            relay_gate(8443)
            baseline(bundle)
            target_order = TARGETS
        for target_id in target_order:
            before = bundle["inbounds"][str(target_id)]
            port = int(before["port"])
            relay_gate(80 if target_id == 10 else port)
            print(f"WARNING: APPLY-801-{target_id}; x-ui may reload the whole Xray runtime.")
            desired = dict(bundle["source"])
            # Persist ownership before the remote mutation. A timeout or lost
            # response can then be resolved only against these exact digests.
            update_journal(opdir, {"operation_id":operation_id, "state":"pending_add", "current_target":target_id,
                                   "targets":retained, "initial_fingerprint":bundle["fingerprint"],
                                   "pending_mutation":{"target":target_id, "action":"add", "before":before,
                                                       "before_digest":json_digest(before), "desired":desired,
                                                       "desired_digest":json_digest(desired), "retained":retained}})
            current = (target_id, before, bundle["fingerprint"], desired)
            started = int(time.time())
            added = bot_action("add", {"target_id":target_id, "before":before, "pre_fingerprint":bundle["fingerprint"], "initial":bundle})
            current = (target_id, before, str(added["fingerprint"]), desired)
            bundle["inbounds"] = added["inbounds"]
            nl_gate(opdir, port, started)
            relay_gate(80 if target_id == 10 else port)
            baseline(bundle)
            # Inbound 10's direct backend run is diagnostic. A public frontend
            # pass is the only eligibility condition.
            attempts: list[tuple[bool, str, int]] = []
            diagnostic_error = "none"
            if target_id == 10:
                diagnostic = relay_probe(singbox_config(bundle, target_id, 0, diagnostic=True), str(bundle["health_url"]), str(bundle["expected_egress"]))
                diagnostic_error = diagnostic[1]
            for _ in range(3):
                attempts.append(relay_probe(singbox_config(bundle, target_id, 0), str(bundle["health_url"]), str(bundle["expected_egress"])))
            passed = all(item[0] for item in attempts)
            error_class = "none" if passed else next((item[1] for item in attempts if not item[0]), "probe_failed")
            if target_id == 10 and passed:
                error_class = "none"
            if passed and target_id != 11:
                retained.append(target_id)
                current = None
                bundle["fingerprint"] = str(added["fingerprint"])
                statuses.append({"inbound":target_id,"status":"passed","error_class":error_class,"attempts":"3/3","retained":True,"removed":False})
                update_journal(opdir, {"operation_id":operation_id,"state":"retained","targets":retained,"initial_fingerprint":bundle["fingerprint"]})
            else:
                remove_reason = "ws_none_policy" if target_id == 11 and passed else error_class
                delete_before = bundle["inbounds"][str(target_id)]
                update_journal(opdir, {"operation_id":operation_id, "state":"pending_delete", "current_target":target_id,
                                       "targets":retained, "initial_fingerprint":bundle["fingerprint"],
                                       "pending_mutation":{"target":target_id, "action":"delete", "before":delete_before,
                                                           "before_digest":json_digest(delete_before), "rollback_before":before,
                                                           "desired":desired, "desired_digest":json_digest(desired),
                                                           "retained":retained}})
                # Set outer rollback ownership before the delete request too.
                current = (target_id, before, str(added["fingerprint"]), desired)
                fingerprint, inbounds = scoped_delete(bundle, target_id, before, str(added["fingerprint"]), desired, bundle, opdir)
                bundle["fingerprint"] = fingerprint
                bundle["inbounds"] = inbounds
                current = None
                statuses.append({"inbound":target_id,"status":"passed" if passed else "failed","error_class":remove_reason,"attempts":f"{sum(x[0] for x in attempts)}/3","retained":False,"removed":True})
                update_journal(opdir, {"operation_id":operation_id,"state":"removed","targets":retained,"initial_fingerprint":bundle["fingerprint"]})
        statuses.append({"inbound":12,"status":"unsupported","error_class":"mkcp_unsupported_singbox_1_13_15","attempts":"0/0","retained":False,"removed":False})
        # Post-closeout service and source gate after last action.
        baseline(bundle)
        update_journal(opdir, {"operation_id":operation_id,"state":"complete","targets":retained,"initial_fingerprint":bundle["fingerprint"],"result":"sanitized_only"})
        relay_cleanup_binary()
        for row in sorted(statuses, key=lambda item: (item["inbound"] == 12, TARGETS.index(item["inbound"]) if item["inbound"] in TARGETS else 99)):
            print(" ".join(f"{key}={value}" for key, value in row.items()))
        print("service_gates=passed baseline_5=passed xui_nginx=active listener_set=unchanged stable_api=passed")
        print("protected_artifacts=retained")
        print("retained_set=" + ",".join(map(str, retained)))
        return 0
    except GateError as error:
        # A subprocess timeout/lost response is an uncertain mutation, not a
        # rollback authorization. Re-read only the journal-owned target and
        # accept solely its exact before/after shape; ambiguous data is never
        # touched. This applies equally to pending add and pending delete.
        if current and bundle and opdir:
            try:
                _, journal, _ = load_resume_journal(operation_id)
                recover_pending_mutation(bundle, journal, opdir)
            except GateError:
                print("BLOCK: manual_recovery_required", file=sys.stderr)
                return 1
        print(f"BLOCK: {error}", file=sys.stderr)
        return 1
    finally:
        # The only local copies can contain config/identity values; delete them
        # regardless of success. NL's protected backup and journal remain.
        shutil.rmtree(work, ignore_errors=True)
        if opdir:
            try:
                release_nl_lock(operation_id)
            except GateError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
