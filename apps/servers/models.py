from django.db import models

from apps.servers.querysets import ServerQuerySet


class TariffServer(models.Model):
    name = models.CharField(
        'Наименование тарифа',
        max_length=255,
    )

    price = models.DecimalField(
        'Цена, руб',
        max_digits=10,
        decimal_places=2,
    )

    class Meta:
        verbose_name = 'Информация о тарифе'
        verbose_name_plural = 'Информация о тарифах'

    def __str__(self):
        return self.name


class Server(models.Model):
    # country = models.ForeignKey(
    #     'Country',
    #     on_delete=models.PROTECT,
    #     related_name='servers',
    # )

    objects = ServerQuerySet.as_manager()

    name = models.CharField(
        'Наименование сервера',
        max_length=255,
        blank=True,
    )

    ip_address = models.GenericIPAddressField(
        'IP сервера для ssh подключения',
    )

    ssh_username = models.CharField(
        'SSH логин',
        max_length=255,
    )

    ssh_password = models.CharField(
        'SSH пароль',
        max_length=255,
    )

    vpn_username = models.CharField(
        'Username 3x-ui',
        max_length=255,
    )

    vpn_password = models.CharField(
        'Password 3x-ui',
        max_length=255,
    )

    vpn_key = models.CharField(
        'Key 3x-ui',
        max_length=255,
    )

    vpn_url = models.CharField(
        'URL 3x-ui',
        max_length=255,
        blank=True,
    )

    client_vpn_host = models.CharField(
        'VPN хост, к которому будут подключаться клиенты',
        max_length=255,
        blank=True,
    )

    updated_at = models.DateTimeField(
        'Время обновления записи',
        auto_now=True,
    )

    created_at = models.DateTimeField(
        'Время создания записи',
        auto_now_add=True,
    )

    tariff = models.ForeignKey(
        'TariffServer',
        on_delete=models.PROTECT,
        related_name='servers',
    )

    inbound_id = models.IntegerField(
        'Inbound ID',
        default=1,
    )

    inbound_id_grpc = models.IntegerField(
        'Inbound ID (gRPC)',
        default=1,
    )

    inbound_id_http = models.IntegerField(
        'Inbound ID (HTTP)',
        default=1,
    )

    inbound_id_udp = models.IntegerField(
        'Inbound ID (UDP)',
        default=1,
    )

    subs_inbound_id_grpc = models.IntegerField(
        'Inbound ID подписки (gRPC)',
        default=0,
        blank=True,
    )

    subs_inbound_id_http = models.IntegerField(
        'Inbound ID подписки (HTTP)',
        default=0,
        blank=True,
    )

    subs_inbound_id_udp = models.IntegerField(
        'Inbound ID подписки (UDP)',
        default=0,
        blank=True,
    )

    is_subscription_server = models.BooleanField(
        'Сервер для подписок',
        default=False,
    )

    class Meta:
        verbose_name = 'Информация о сервере'
        verbose_name_plural = 'Информация о серверах'

    def __str__(self):
        return self.name

    def get_vpn_inbound_ids(self) -> list[tuple[str, int]]:
        return [
            ('gRPC', self.inbound_id_grpc),
            ('HTTP', self.inbound_id_http),
            ('UDP', self.inbound_id_udp),
        ]
