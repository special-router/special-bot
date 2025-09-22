from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from apps.vpn.models import UserVPN
from api.v1.vpn.serializers.user_vpn import UserVPNSerializer


class UserVPNListView(generics.ListAPIView):
    queryset = UserVPN.objects.select_related('user', 'server').all()
    serializer_class = UserVPNSerializer
    permission_classes = [IsAuthenticated]


