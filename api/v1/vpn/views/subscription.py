from django.http import Http404, HttpResponse
from django.views import View

from apps.vpn.models import UserVPN
from apps.vpn.services.subscription_builder import build_subscription_payload


class VPNSubscriptionView(View):
    def get(self, request, vpn_uuid):
        try:
            user_vpn = UserVPN.objects.get(vpn_uuid=vpn_uuid, enabled=True)
        except UserVPN.DoesNotExist as exc:
            raise Http404 from exc

        try:
            payload = build_subscription_payload(user_vpn)
        except ValueError as exc:
            raise Http404 from exc

        response = HttpResponse(payload, content_type='text/plain; charset=utf-8')
        response['profile-update-interval'] = '24'
        return response
