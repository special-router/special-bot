from __future__ import annotations

from urllib.parse import urljoin

from django.conf import settings
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.subscriptions.router import build_router_config
from apps.subscriptions.tokens import exchange_router_activation_code, resolve_access_token


class ActivationRequest(serializers.Serializer):
    code = serializers.CharField(min_length=20, max_length=44)


def _config_url(request) -> str:
    path = '/api/v1/vpn/router/config/'
    base = str(getattr(settings, 'ROUTER_PROVISIONING_PUBLIC_BASE_URL', '')).strip()
    return urljoin(base.rstrip('/') + '/', path.lstrip('/')) if base else request.build_absolute_uri(path)


class RouterActivationView(GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = ActivationRequest

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exchanged = exchange_router_activation_code(serializer.validated_data['code'])
        if exchanged is None:
            return Response({'detail': 'Invalid or expired activation code.'}, status=status.HTTP_404_NOT_FOUND)
        token, _record = exchanged
        response = Response({'device_token': token, 'config_url': _config_url(request)})
        response['Cache-Control'] = 'private, no-store'
        return response


class RouterConfigView(GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        header = request.headers.get('Authorization', '')
        token = header[7:] if header.startswith('Bearer ') else ''
        record = resolve_access_token(token, touch=True)
        if record is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        config = build_router_config() if getattr(settings, 'ROUTER_PROVIDER_SNAPSHOT_ENABLED', False) else None
        if config is None:
            return Response({'detail': 'Configuration temporarily unavailable.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = Response(config)
        response['Cache-Control'] = 'private, no-store'
        return response
