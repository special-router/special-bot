from django.urls import path

from api.v1.vpn.views.box import VPNBoxConfigView
from api.v1.vpn.views.subscription import VPNSubscriptionView
from api.v1.vpn.views.user_vpn import UserVPNListView


urlpatterns = [
    path('', UserVPNListView.as_view(), name='user-vpn-list'),
    path('box/<uuid:vpn_uuid>/config/', VPNBoxConfigView.as_view(), name='vpn-box-config'),
    path('sub/<uuid:vpn_uuid>/', VPNSubscriptionView.as_view(), name='vpn-subscription'),
]
