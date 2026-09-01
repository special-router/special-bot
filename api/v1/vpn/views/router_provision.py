"""Operator-only provisioning for universal router images."""
from __future__ import annotations

import logging
from urllib.parse import urljoin

from asgiref.sync import async_to_sync
from django.conf import settings
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.v1.vpn.authentication import RouterProvisioningAuthentication
from apps.servers.models import Server
from apps.users.models import TelegramUser
from apps.vpn.models import UserVPN
from apps.vpn.services.add_vpn_to_user import add_vpn_to_user


logger = logging.getLogger(__name__)


class RouterProvisioningRequest(serializers.Serializer):
    telegram_id = serializers.IntegerField(min_value=1)
    server_id = serializers.IntegerField(min_value=1, required=False)


class RouterProvisioningView(GenericAPIView):
    """Find or activate one customer's router credential through Django.

    The service caller is an operator, not a customer. It may provision only a
    Telegram identity that already exists in the bot. No panel credential,
    subscription id or multi-customer inventory is returned.
    """

    authentication_classes = [RouterProvisioningAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = RouterProvisioningRequest

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        telegram_id = serializer.validated_data['telegram_id']
        server_id = serializer.validated_data.get('server_id')

        user = TelegramUser.objects.filter(telegram_id=telegram_id).order_by('id').first()
        if user is None:
            return Response({'detail': 'Customer not found.'}, status=status.HTTP_404_NOT_FOUND)
        existing = UserVPN.objects.filter(user=user).select_related('server').order_by('id')
        if server_id is not None:
            existing = existing.filter(server_id=server_id)
        matches = list(existing[:2])
        if server_id is None and len(matches) > 1:
            return Response(
                {'detail': 'server_id is required for a customer with multiple VPNs.'},
                status=status.HTTP_409_CONFLICT,
            )
        user_vpn = matches[0] if matches else None
        created = False

        if user_vpn is None:
            server = self._server(server_id)
            if server is None:
                return Response({'detail': 'Server not found.'}, status=status.HTTP_404_NOT_FOUND)
            try:
                user_vpn = async_to_sync(add_vpn_to_user)(user, server)
            except Exception as error:
                logger.error(
                    'router_provision failed user_id=%s server_id=%s reason=%s',
                    user.id, server.id, type(error).__name__,
                )
                return Response(
                    {'detail': 'Provisioning backend unavailable.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            created = True
        elif not user_vpn.enabled:
            try:
                user_vpn = async_to_sync(add_vpn_to_user)(user, user_vpn.server)
            except Exception as error:
                logger.error(
                    'router_provision failed user_id=%s server_id=%s reason=%s',
                    user.id, user_vpn.server_id, type(error).__name__,
                )
                return Response(
                    {'detail': 'Provisioning backend unavailable.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        logger.info(
            'router_provision succeeded user_id=%s server_id=%s created=%s',
            user.id, user_vpn.server_id, created,
        )
        response = Response({
            'vpn_uuid': str(user_vpn.vpn_uuid),
            'config_url': self._config_url(request, user_vpn),
            'created': created,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        response['Cache-Control'] = 'private, no-store'
        response['Pragma'] = 'no-cache'
        return response

    @staticmethod
    def _server(server_id):
        servers = Server.objects.with_related_tariffs().order_by('id')
        if server_id is not None:
            return servers.filter(id=server_id).first()
        return servers.first()

    @staticmethod
    def _config_url(request, user_vpn):
        path = f'/api/v1/vpn/box/{user_vpn.vpn_uuid}/config/'
        base = str(getattr(settings, 'ROUTER_PROVISIONING_PUBLIC_BASE_URL', '')).strip()
        if base:
            return urljoin(base.rstrip('/') + '/', path.lstrip('/'))
        return request.build_absolute_uri(path)
