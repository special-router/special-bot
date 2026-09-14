"""Immutable last-known-good delivery snapshots for external providers.

The public subscription request path reads only already published artifacts.
Network fetches and provider parsing run in the isolated ingestion worker.
Artifacts contain provider bearer lines, so the directory and every file stay
owner-only and no filename, digest, URL, or line is logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


_PROVIDER_ID = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
_ARTIFACT_NAME = re.compile(r'^[a-z][a-z0-9_-]{0,31}-[0-9a-f]{64}\.json$')
_MAX_ARTIFACT_BYTES = 1024 * 1024
_SCOPE_NAME = 'scope.json'


@dataclass(frozen=True)
class SnapshotStatus:
    configured_sources: int
    ready_sources: int
    oldest_age_seconds: int | None

    @property
    def ready(self) -> bool:
        return self.configured_sources > 0 and self.ready_sources == self.configured_sources


def enabled_provider_ids() -> tuple[str, ...]:
    manifest = getattr(settings, 'SUBSCRIPTION_BACKUP_PROVIDER_MANIFEST', [])
    if not isinstance(manifest, list):
        return ()
    provider_ids = []
    for item in manifest:
        if not isinstance(item, dict) or item.get('enabled') is not True:
            continue
        provider_id = item.get('id')
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            return ()
        provider_ids.append(provider_id)
    return tuple(provider_ids) if len(provider_ids) == len(set(provider_ids)) else ()


def _publish_scope(provider_ids: tuple[str, ...]) -> None:
    root = _snapshot_root(create=True)
    if root is None or not provider_ids:
        raise RuntimeError('provider_snapshot_directory_invalid')
    payload = _canonical_bytes({'version': 1, 'provider_ids': list(provider_ids)})
    temporary = root / f'.scope-{os.getpid()}.tmp'
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as scope_file:
            scope_file.write(payload)
            scope_file.flush()
            os.fsync(scope_file.fileno())
        os.replace(temporary, root / _SCOPE_NAME)
        root.joinpath(_SCOPE_NAME).chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def snapshot_provider_ids() -> tuple[str, ...]:
    """Read the non-secret source scope published into the snapshot volume."""
    root = _snapshot_root()
    if root is not None:
        path = root / _SCOPE_NAME
        try:
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
                return ()
            payload = path.read_bytes()
            if len(payload) > 4096:
                return ()
            document = json.loads(payload)
            provider_ids = document.get('provider_ids')
            if (document.get('version') == 1 and isinstance(provider_ids, list)
                    and provider_ids and len(provider_ids) == len(set(provider_ids))
                    and all(isinstance(value, str) and _PROVIDER_ID.fullmatch(value)
                            for value in provider_ids)):
                return tuple(provider_ids)
        except (OSError, ValueError, TypeError):
            return ()
    # The ingestion worker has the source secret and needs a fail-closed status
    # before the first scope exists. Public services do not have this fallback.
    return enabled_provider_ids()


def _snapshot_root(*, create: bool = False) -> Path | None:
    value = getattr(settings, 'SUBSCRIPTION_PROVIDER_SNAPSHOT_DIR', '')
    if not isinstance(value, str) or not value.startswith('/') or '\x00' in value:
        return None
    root = Path(value)
    try:
        if create:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root.chmod(0o700)
        metadata = root.lstat()
    except OSError:
        return None
    if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        return None
    return root


def _canonical_bytes(document: dict) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        existing = path.read_bytes()
        if existing != payload:
            raise RuntimeError('provider_snapshot_collision')
        return
    with os.fdopen(descriptor, 'wb') as artifact:
        artifact.write(payload)
        artifact.flush()
        os.fsync(artifact.fileno())
    path.chmod(0o600)


def _publish(provider_id: str, lines: list[str]) -> int:
    root = _snapshot_root(create=True)
    if root is None or not _PROVIDER_ID.fullmatch(provider_id):
        raise RuntimeError('provider_snapshot_directory_invalid')
    if (
        not isinstance(lines, list)
        or not lines
        or len(lines) > 2048
        or any(not isinstance(line, str) or not line or len(line.encode('utf-8')) > 4096 for line in lines)
    ):
        raise RuntimeError('provider_snapshot_payload_invalid')
    document = {
        'version': 1,
        'provider_id': provider_id,
        'lines': lines,
    }
    payload = _canonical_bytes(document)
    if len(payload) > _MAX_ARTIFACT_BYTES:
        raise RuntimeError('provider_snapshot_payload_too_large')
    digest = hashlib.sha256(payload).hexdigest()
    artifact_name = f'{provider_id}-{digest}.json'
    _write_exclusive(root / artifact_name, payload)
    pointer = _canonical_bytes({
        'version': 1,
        'provider_id': provider_id,
        'artifact': artifact_name,
        'digest': digest,
        'published_at': int(time.time()),
    })
    temporary = root / f'.current-{provider_id}-{os.getpid()}.tmp'
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as manifest_file:
            manifest_file.write(pointer)
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        os.replace(temporary, root / f'current-{provider_id}.json')
        root.joinpath(f'current-{provider_id}.json').chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return len(lines)


def refresh_provider_snapshots() -> dict[str, dict[str, object]]:
    """Refresh every provider independently; a failure retains its old pointer."""
    if getattr(settings, 'SUBSCRIPTION_PROVIDER_SNAPSHOTS_ENABLED', False) is not True:
        return {}
    from apps.subscriptions import views

    provider_ids = enabled_provider_ids()
    urls = getattr(settings, 'SUBSCRIPTION_BACKUP_UPSTREAM_URLS', [])
    if not provider_ids or not isinstance(urls, list):
        raise RuntimeError('provider_snapshot_sources_invalid')
    sources = views._backup_sources(urls)
    if len(sources) != len(provider_ids):
        raise RuntimeError('provider_snapshot_sources_invalid')
    allowed = getattr(settings, 'SUBSCRIPTION_BACKUP_ALLOWED_LINE_SHA256', None)
    if allowed is not None and not views._valid_line_sha256_allowlist(allowed):
        raise RuntimeError('provider_snapshot_allowlist_invalid')

    results: dict[str, dict[str, object]] = {}
    for provider_id, (url, user_agent) in zip(provider_ids, sources):
        try:
            headers, payload = views._fetch_upstream_payload(
                url, user_agent=user_agent or views._upstream_user_agent())
            lines = views._sanitize_upstream_payload(payload, headers)
            if allowed is not None:
                lines = [
                    line for line in lines
                    if hashlib.sha256(line.encode('utf-8')).hexdigest() in allowed
                ]
            count = _publish(provider_id, lines)
            results[provider_id] = {'ok': True, 'lines': count}
        except Exception as error:
            # Provider URLs and payloads can surface inside exception text.
            results[provider_id] = {'ok': False, 'error_class': type(error).__name__}
    now = int(time.time())
    if all(item.get('ok') or _read(provider_id, now=now) is not None
           for provider_id, item in results.items()):
        _publish_scope(provider_ids)
    return results


def _read(provider_id: str, *, now: int) -> tuple[list[str], int] | None:
    root = _snapshot_root()
    if root is None or not _PROVIDER_ID.fullmatch(provider_id):
        return None
    pointer_path = root / f'current-{provider_id}.json'
    try:
        metadata = pointer_path.lstat()
        if pointer_path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            return None
        pointer_raw = pointer_path.read_bytes()
        if len(pointer_raw) > 4096:
            return None
        pointer = json.loads(pointer_raw)
        artifact_name = pointer.get('artifact')
        digest = pointer.get('digest')
        published_at = pointer.get('published_at')
        if (
            pointer.get('version') != 1
            or pointer.get('provider_id') != provider_id
            or not isinstance(artifact_name, str)
            or not _ARTIFACT_NAME.fullmatch(artifact_name)
            or not isinstance(digest, str)
            or len(digest) != 64
            or not isinstance(published_at, int)
        ):
            return None
        max_age = int(getattr(settings, 'SUBSCRIPTION_PROVIDER_SNAPSHOT_MAX_AGE_SECONDS', 86400))
        if max_age < 60 or max_age > 604800 or published_at > now + 60 or now - published_at > max_age:
            return None
        artifact_path = root / artifact_name
        artifact_meta = artifact_path.lstat()
        if artifact_path.is_symlink() or not stat.S_ISREG(artifact_meta.st_mode) or stat.S_IMODE(artifact_meta.st_mode) & 0o077:
            return None
        if artifact_meta.st_size < 1 or artifact_meta.st_size > _MAX_ARTIFACT_BYTES:
            return None
        payload = artifact_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            return None
        document = json.loads(payload)
        lines = document.get('lines')
        if (
            document.get('version') != 1
            or document.get('provider_id') != provider_id
            or not isinstance(lines, list)
            or not lines
            or len(lines) > 2048
            or any(not isinstance(line, str) or not line or len(line.encode('utf-8')) > 4096 for line in lines)
        ):
            return None
        return lines, max(0, now - published_at)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def provider_snapshot_lines() -> dict[str, list[str]]:
    """Return only verified current artifacts; never contact a provider."""
    if getattr(settings, 'SUBSCRIPTION_PROVIDER_SNAPSHOTS_ENABLED', False) is not True:
        return {}
    now = int(time.time())
    result = {}
    for provider_id in snapshot_provider_ids():
        loaded = _read(provider_id, now=now)
        if loaded is not None:
            result[provider_id] = loaded[0]
    return result


def provider_snapshot_status() -> SnapshotStatus:
    provider_ids = snapshot_provider_ids()
    now = int(time.time())
    ages = []
    for provider_id in provider_ids:
        loaded = _read(provider_id, now=now)
        if loaded is not None:
            ages.append(loaded[1])
    return SnapshotStatus(
        configured_sources=len(provider_ids),
        ready_sources=len(ages),
        oldest_age_seconds=max(ages) if ages else None,
    )
