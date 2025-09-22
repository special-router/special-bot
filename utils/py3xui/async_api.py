import logging
from typing import Any

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
        self.logger = logger or logging.getLogger(__name__)

        self.client = AsyncClientApi(
            host, username, password, use_tls_verify, custom_certificate_path, logger
        )
        self.inbound = AsyncInboundApi(
            host, username, password, use_tls_verify, custom_certificate_path, logger
        )
        self.database = AsyncDatabaseApi(
            host, username, password, use_tls_verify, custom_certificate_path, logger
        )
        self.server = AsyncServerApi(
            host, username, password, use_tls_verify, custom_certificate_path, logger
        )
        self._session: str | None = None
        self._cookie_name: str | None = None