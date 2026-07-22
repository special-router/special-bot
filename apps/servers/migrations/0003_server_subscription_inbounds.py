# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('servers', '0002_server_multi_inbounds'),
    ]

    operations = [
        migrations.AddField(
            model_name='server',
            name='subs_inbound_id_grpc',
            field=models.IntegerField(blank=True, default=0, verbose_name='Inbound ID подписки (gRPC)'),
        ),
        migrations.AddField(
            model_name='server',
            name='subs_inbound_id_http',
            field=models.IntegerField(blank=True, default=0, verbose_name='Inbound ID подписки (HTTP)'),
        ),
        migrations.AddField(
            model_name='server',
            name='subs_inbound_id_udp',
            field=models.IntegerField(blank=True, default=0, verbose_name='Inbound ID подписки (UDP)'),
        ),
        migrations.AddField(
            model_name='server',
            name='is_subscription_server',
            field=models.BooleanField(default=False, verbose_name='Сервер для подписок'),
        ),
    ]
