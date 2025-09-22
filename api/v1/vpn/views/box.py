import asyncio
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView

from apps.vpn.models import UserVPN
from apps.servers.vpn_client import APIVPNClient


class VPNBoxConfigView(GenericAPIView):
    permission_classes = [AllowAny]
    queryset = UserVPN.objects.all().with_related_server()
    lookup_field = 'vpn_uuid'
    lookup_url_kwarg = 'vpn_uuid'

    def get(self, request, *args, **kwargs):
        user_vpn = self.get_object()
        client = APIVPNClient(user_vpn.server)

        config = asyncio.run(client.get_raw_inbound_config(user_vpn))

        return Response(config)


