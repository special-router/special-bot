"""Logging redaction for bearer subscriptions and control-plane URLs."""
import logging
import re


_SUBSCRIPTION_PATH = re.compile(r'(/sub/)[^\s?"\']+')
_ROUTER_CONFIG_PATH = re.compile(
    r'(/api/v1/vpn/box/)[0-9a-fA-F-]+(/config/?)'
)
# Warning records from httpx/urllib3/py3xui commonly contain absolute URLs.
# Redact authority and path, not just known panel prefixes, because that prefix
# itself is privileged and must never be configured into this filter.
_CONTROL_PLANE_URL = re.compile(r'(?i)https?://[^\s"\'<>]+')


class SubscriptionPathRedactionFilter(logging.Filter):
    """Remove bearer material and absolute control-plane URLs before output."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: _redact(value) for key, value in record.args.items()}
            else:
                record.args = tuple(_redact(value) for value in record.args)
        return True


def _redact(value):
    if isinstance(value, str):
        value = _SUBSCRIPTION_PATH.sub(r'\1[REDACTED]', value)
        value = _ROUTER_CONFIG_PATH.sub(r'\1[REDACTED]\2', value)
        return _CONTROL_PLANE_URL.sub('[REDACTED]', value)
    return value
