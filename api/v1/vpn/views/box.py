import asyncio

from django.http import Http404
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.servers.vpn_client import vpn_client_for
from apps.vpn.models import UserVPN


class VPNBoxConfigView(GenericAPIView):
    """Return one router Xray outbound, addressed by its customer UUID.

    The UUID is the credential: it grants access to this one configuration, not
    to Remnawave or to any other customer. Unknown and Django-disabled records
    deliberately produce the same 404 response.
    """
    permission_classes = [AllowAny]
    queryset = UserVPN.objects.all().with_related_server()
    lookup_field = 'vpn_uuid'
    lookup_url_kwarg = 'vpn_uuid'

    def get(self, request, *args, **kwargs):
        try:
            user_vpn = self.queryset.get(vpn_uuid=kwargs['vpn_uuid'], enabled=True)
        except UserVPN.DoesNotExist:
            raise Http404 from None
        client = vpn_client_for(user_vpn.server)

        config = asyncio.run(client.get_raw_inbound_config(user_vpn))
        return Response(config)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        response['Pragma'] = 'no-cache'
        return response
