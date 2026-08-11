"""Logging redaction for bearer subscription paths."""
import logging
import re


_SUBSCRIPTION_PATH = re.compile(r'(/sub/)[^\s?"\']+')


class SubscriptionPathRedactionFilter(logging.Filter):
    """Remove bearer material from request log records before console output."""

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
        return _SUBSCRIPTION_PATH.sub(r'\1[REDACTED]', value)
    return value
