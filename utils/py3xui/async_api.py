import logging
from typing import Any
from urllib.parse import urlsplit

from py3xui import AsyncApi
from py3xui.async_api import AsyncClientApi, AsyncDatabaseApi, AsyncServerApi

from utils.py3xui.async_api_inbound import AsyncInboundApi


class AsyncApi(AsyncApi):
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        use_tls_verify: bool = True,
        custom_certificate_path: str | None = None,
        logger: Any | None = None,
    ):  # pylint: disable=R0913, R0917
        parsed = urlsplit(host)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError('xui_https_required')
        if not use_tls_verify:
            raise ValueError('xui_tls_verification_required')
        if custom_certificate_path is None:
            # Import lazily so standalone tooling can provide its own trusted
            # path without requiring Django settings initialization.
            try:
                from django.conf import settings
                if getattr(settings, 'XUI_PANEL_CA_FILE_INVALID', False):
                    raise ValueError('xui_ca_file_invalid')
                custom_certificate_path = getattr(settings, 'XUI_PANEL_CA_CERTIFICATE_PATH', None)
            except ValueError:
                raise
            except Exception:
                pass
        self.logger = logger or logging.getLogger(__name__)

        self.client = AsyncClientApi(host, username, password, use_tls_verify, custom_certificate_path, self.logger)
        self.inbound = AsyncInboundApi(host, username, password, use_tls_verify, custom_certificate_path, self.logger)
        self.database = AsyncDatabaseApi(host, username, password, use_tls_verify, custom_certificate_path, self.logger)
        self.server = AsyncServerApi(host, username, password, use_tls_verify, custom_certificate_path, self.logger)
        self._session: str | None = None
        self._cookie_name: str | None = None
