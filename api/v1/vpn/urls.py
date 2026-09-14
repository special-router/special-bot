from django.urls import path

from api.v1.vpn.views.box import VPNBoxConfigView
from api.v1.vpn.views.router_provision import RouterProvisioningView
from api.v1.vpn.views.router_config import RouterActivationView, RouterConfigView
from api.v1.vpn.views.user_vpn import UserVPNListView


urlpatterns = [
    path('', UserVPNListView.as_view(), name='user-vpn-list'),
    path('box/<uuid:vpn_uuid>/config/', VPNBoxConfigView.as_view(), name='vpn-box-config'),
    path('router/provision/', RouterProvisioningView.as_view(), name='router-provision'),
    path('router/activate/', RouterActivationView.as_view(), name='router-activate'),
    path('router/config/', RouterConfigView.as_view(), name='router-config'),
]
