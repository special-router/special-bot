import logging
from typing import Any
from urllib.parse import urlsplit

from py3xui import AsyncApi
from py3xui.async_api import AsyncDatabaseApi, AsyncServerApi

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
        # The panel is served over TLS behind nginx with a publicly trusted
        # certificate. Refuse to talk to it any other way: a silent downgrade
        # would put panel credentials on the wire in clear text.
        parsed = urlsplit(host)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError('xui_https_required')
        if not use_tls_verify:
            raise ValueError('xui_tls_verification_required')

        self.logger = logger or logging.getLogger(__name__)

        # Every client write goes through this one class so no call site has to
        # remember the attribution label.  Imported here rather than at module
        # level: it reaches Django models, and this module is transport.
        from apps.servers.client_labels import LabelledClientApi

        self.client = LabelledClientApi(host, username, password, use_tls_verify, custom_certificate_path, logger)
        self.inbound = AsyncInboundApi(host, username, password, use_tls_verify, custom_certificate_path, logger)
        self.database = AsyncDatabaseApi(host, username, password, use_tls_verify, custom_certificate_path, logger)
        self.server = AsyncServerApi(host, username, password, use_tls_verify, custom_certificate_path, logger)
        self._session: str | None = None
        self._cookie_name: str | None = None
