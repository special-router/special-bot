from django.urls import path

from api.v1.vpn.views.user_vpn import UserVPNListView
from api.v1.vpn.views.box import VPNBoxConfigView


urlpatterns = [
    path('', UserVPNListView.as_view(), name='user-vpn-list'),
    path('box/<uuid:vpn_uuid>/config/', VPNBoxConfigView.as_view(), name='vpn-box-config'),
]


