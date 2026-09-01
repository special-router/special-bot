"""Authentication for the narrow router provisioning API."""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


@dataclass(frozen=True)
class RouterProvisioningPrincipal:
    """Minimal DRF principal for one infrastructure service."""

    name: str = 'router-provisioning'
    is_authenticated: bool = True


class RouterProvisioningAuthentication(BaseAuthentication):
    """Authenticate a dedicated bearer without exposing Django admin auth."""

    keyword = 'Bearer'

    def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        scheme, separator, supplied = header.partition(' ')
        expected = str(getattr(settings, 'ROUTER_PROVISIONING_API_TOKEN', ''))
        valid = (
            separator
            and scheme == self.keyword
            and bool(expected)
            and bool(supplied)
            and hmac.compare_digest(supplied, expected)
        )
        if not valid:
            raise AuthenticationFailed('Invalid router provisioning credential.')
        return RouterProvisioningPrincipal(), None

    def authenticate_header(self, request):
        return self.keyword
