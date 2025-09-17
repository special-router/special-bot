import uuid

from django.db import models

from apps.vpn.querysets import UserVPNQuerySet


class UserVPN(models.Model):
    objects = UserVPNQuerySet.as_manager()

    user = models.ForeignKey(
        'users.TelegramUser',
        on_delete=models.PROTECT,
        related_name='vpn',
    )

    server = models.ForeignKey(
        'servers.Server',
        on_delete=models.PROTECT,
        related_name='vpn',
    )

    vpn_key = models.TextField(
        'VPN key',
        blank=True,
    )

    vpn_uuid = models.UUIDField(
        'Client UUID',
        default=uuid.uuid4,
    )

    enabled = models.BooleanField(
        'Enabled',
        default=True,
    )

    class Meta:
        verbose_name = 'User VPN'
        verbose_name_plural = 'User VPNs'

        unique_together = ['user', 'server']

    def __str__(self):
        return f"{self.user.username} {self.user.telegram_id} - {self.server.name}"
