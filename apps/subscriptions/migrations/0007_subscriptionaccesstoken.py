import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0006_prune_stale_mirror_endpoint_liveness'),
        ('vpn', '0005_uservpn_device_limit'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubscriptionAccessToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(max_length=64, unique=True, validators=[django.core.validators.RegexValidator('^[0-9a-f]{64}$', 'Expected a lowercase SHA-256 digest.')], verbose_name='SHA-256 access-token digest')),
                ('token_hint', models.CharField(db_index=True, max_length=12, verbose_name='Non-secret digest prefix')),
                ('active_from', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Active from')),
                ('expires_at', models.DateTimeField(blank=True, null=True, verbose_name='Expires at')),
                ('revoked_at', models.DateTimeField(blank=True, null=True, verbose_name='Revoked at')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True, verbose_name='Last successful use')),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='access_tokens', to='vpn.uservpn')),
            ],
            options={
                'ordering': ('subscription_id', '-created_at'),
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('expires_at__isnull', True), ('expires_at__gt', models.F('active_from')), _connector='OR'), name='subscription_token_expiry_after_start'),
                    models.CheckConstraint(condition=models.Q(('token_hash__regex', '^[0-9a-f]{64}$')), name='subscription_token_hash_is_sha256'),
                ],
            },
        ),
    ]
