from rest_framework import serializers

from apps.vpn.models import UserVPN


class UserVPNSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    user_telegram_id = serializers.IntegerField(source='user.telegram_id', read_only=True)
    server_name = serializers.CharField(source='server.name', read_only=True)

    class Meta:
        model = UserVPN
        fields = [
            'id',
            'user',
            'user_username',
            'user_telegram_id',
            'server',
            'server_name',
            'vpn_key',
            'vpn_uuid',
            'enabled',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']



