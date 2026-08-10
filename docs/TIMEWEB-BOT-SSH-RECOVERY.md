# Timeweb BOT SSH banner recovery

Target: the SPECIAL Bot production host in Timeweb Cloud. Resolve the approved
hostname/address from the protected operator environment; do not copy it into
new tickets or documents. Use only the provider VNC/serial console as root. Do
not change host keys, enable password authentication, reboot, alter firewall
rules or stop PostgreSQL/Redis.

## 1. Read-only diagnosis

```bash
set -u
date -u
uptime
free -h
df -h /
ss -lntp '( sport = :22 )'
systemctl is-active ssh || systemctl is-active sshd
sshd -t
sshd -T | grep -Ei '^(maxstartups|maxsessions|logingracetime|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|permitrootlogin) '
printf 'established_ssh='; ss -Hnt state established '( sport = :22 )' | wc -l
printf 'syn_recv_ssh='; ss -Hnt state syn-recv '( sport = :22 )' | wc -l
printf 'sshd_processes='; pgrep -a sshd | wc -l
journalctl -u ssh -n 100 --no-pager 2>/dev/null || journalctl -u sshd -n 100 --no-pager
journalctl -k -n 100 --no-pager | grep -Ei 'conntrack|oom|out of memory|blocked for more than|I/O error' || true
printf '%s\n' 'pressure:'
for file in /proc/pressure/cpu /proc/pressure/memory /proc/pressure/io; do
  printf '%s ' "${file##*/}"
  cat "$file"
done
printf '%s\n' 'top_rss:'
ps -eo pid=,ppid=,stat=,rss=,comm= --sort=-rss | head -20
printf '%s\n' 'd_state:'
ps -eo pid=,ppid=,stat=,wchan:32=,comm= | awk '$3 ~ /^D/ {print}'
printf 'docker_socket_waiters='; ss -xap 2>/dev/null | grep -c '/var/run/docker.sock' || true
printf 'docker_api='; timeout 15 docker info --format '{{.ServerVersion}}' || echo unavailable
```

Record only counts, load, service state and coarse error class. Do not copy
public keys, environment contents, command lines containing secrets or full IP
lists into chat/tickets.

## 2. Minimal recovery

Proceed only if `sshd -t` succeeds. Keep the provider console open throughout.

```bash
unit=ssh
systemctl list-unit-files ssh.service >/dev/null 2>&1 || unit=sshd
systemctl reload "$unit"
sleep 3
systemctl is-active "$unit"
ss -lntp '( sport = :22 )'
```

From the operator workstation, verify the existing pinned key and host key:

```bash
ssh -i ~/.ssh/id_ed25519 \
  -o BatchMode=yes \
  -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no \
  -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=10 \
  root@"$SPECIAL_BOT_HOST" 'echo KEY_ONLY_OK'
```

If reload succeeds but no banner appears, inspect load/OOM/conntrack, memory
and I/O pressure, D-state wait channels and stuck pre-auth children in the
console. Do not kill all SSH or Docker processes while diagnosis is incomplete.
Do not run `drop_caches`, add/remove swap, change sysctls, restart Docker, stop
containers or reboot as an exploratory step.

The migration preflight requires all of the following before any mutation:

- load average over one minute no more than `4 × nproc`;
- at least 128 MiB `MemAvailable`;
- zero D-state tasks;
- `docker info` responds within 15 seconds;
- PostgreSQL and Redis remain running;
- only mode-0600 `.environment`/dated environment backups are untracked.

A process kill, service restart, swap change or VM reboot requires a separate
explicit decision after identifying the pressure source and confirming
PostgreSQL/Redis state plus rollback.

## 3. Migration continuation

After key-only SSH succeeds, run from the canonical SPECIAL Bot checkout:

```bash
cd "$SPECIAL_BOT_CHECKOUT"
./ops/scripts/preflight_special_subscription.sh
```

Do not run deployment, x-ui rotation or subId backfill unless this preflight
passes completely.
