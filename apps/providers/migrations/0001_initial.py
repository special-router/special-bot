import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('vpn', '0005_uservpn_device_limit'),
    ]

    operations = [
        migrations.CreateModel(
            name='Provider',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=32, unique=True, verbose_name='Stable provider id')),
                ('name', models.CharField(max_length=128, verbose_name='Provider name')),
                ('adapter', models.SlugField(max_length=64, verbose_name='Registered adapter id')),
                ('credential_mode', models.CharField(choices=[('shared', 'Shared'), ('per_user', 'Per user')], default='shared', max_length=16, verbose_name='Credential isolation')),
                ('enabled', models.BooleanField(default=False, verbose_name='Enabled')),
                ('per_user_lifecycle', models.BooleanField(default=False, verbose_name='Supports per-user lifecycle')),
                ('usage_tracking', models.BooleanField(default=False, verbose_name='Supports quota and expiry sync')),
                ('resale_confirmed_at', models.DateTimeField(blank=True, null=True, verbose_name='Resale rights confirmed')),
                ('priority', models.PositiveSmallIntegerField(default=100, verbose_name='Selection priority')),
                ('capabilities', models.JSONField(blank=True, default=dict, verbose_name='Safe capability metadata')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ('priority', 'slug'),
                'constraints': [models.CheckConstraint(condition=models.Q(('priority__lte', 1000)), name='provider_priority_lte_1000')],
            },
        ),
        migrations.CreateModel(
            name='ProviderInventoryVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('revision', models.PositiveBigIntegerField(verbose_name='Provider-local revision')),
                ('source_digest', models.CharField(max_length=64, validators=[django.core.validators.RegexValidator('^[0-9a-f]{64}$', 'Expected a lowercase SHA-256 digest.')], verbose_name='Safe source-set digest')),
                ('payload_digest', models.CharField(max_length=64, validators=[django.core.validators.RegexValidator('^[0-9a-f]{64}$', 'Expected a lowercase SHA-256 digest.')], verbose_name='Normalized payload digest')),
                ('state', models.CharField(choices=[('quarantined', 'Quarantined'), ('canary', 'Canary'), ('active', 'Active'), ('superseded', 'Superseded'), ('rejected', 'Rejected')], default='quarantined', max_length=16, verbose_name='Publication state')),
                ('endpoint_count', models.PositiveIntegerField(default=0, verbose_name='Normalized endpoint count')),
                ('fetched_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Fetched at')),
                ('validated_at', models.DateTimeField(blank=True, null=True, verbose_name='Validated at')),
                ('published_at', models.DateTimeField(blank=True, null=True, verbose_name='Published at')),
                ('expires_at', models.DateTimeField(blank=True, null=True, verbose_name='Inventory expiry')),
                ('error_class', models.CharField(blank=True, default='', max_length=64, verbose_name='Safe rejection class')),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_versions', to='providers.provider')),
            ],
            options={
                'ordering': ('provider_id', '-revision'),
                'constraints': [
                    models.UniqueConstraint(fields=('provider', 'revision'), name='unique_provider_inventory_revision'),
                    models.UniqueConstraint(condition=models.Q(('state', 'active')), fields=('provider',), name='unique_active_provider_inventory'),
                    models.UniqueConstraint(condition=models.Q(('state', 'canary')), fields=('provider',), name='unique_canary_provider_inventory'),
                    models.CheckConstraint(condition=~models.Q(('state__in', ('active', 'canary'))) | (models.Q(('endpoint_count__gt', 0)) & models.Q(('published_at__isnull', False))), name='active_provider_inventory_is_published'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ProviderEndpoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_id', models.CharField(max_length=128, verbose_name='Stable provider endpoint id')),
                ('protocol', models.CharField(choices=[('vless', 'VLESS'), ('hysteria2', 'Hysteria2'), ('trojan', 'Trojan'), ('shadowsocks', 'Shadowsocks'), ('wireguard', 'WireGuard')], max_length=16, verbose_name='VPN protocol')),
                ('transport', models.CharField(max_length=16, verbose_name='Transport')),
                ('security', models.CharField(max_length=16, verbose_name='Security mode')),
                ('host', models.CharField(max_length=253, verbose_name='Data-plane host')),
                ('port', models.PositiveIntegerField(verbose_name='Data-plane port')),
                ('region', models.CharField(blank=True, default='', max_length=32, verbose_name='Region')),
                ('server_name', models.CharField(blank=True, default='', max_length=253, verbose_name='TLS server name')),
                ('path', models.CharField(blank=True, default='', max_length=512, verbose_name='HTTP path')),
                ('service_name', models.CharField(blank=True, default='', max_length=255, verbose_name='gRPC service name')),
                ('public_key', models.CharField(blank=True, default='', max_length=128, verbose_name='Reality public key')),
                ('short_id', models.CharField(blank=True, default='', max_length=32, verbose_name='Reality short id')),
                ('fingerprint', models.CharField(blank=True, default='', max_length=32, verbose_name='TLS fingerprint')),
                ('flow', models.CharField(blank=True, default='', max_length=64, verbose_name='VLESS flow')),
                ('host_header', models.CharField(blank=True, default='', max_length=253, verbose_name='HTTP host header')),
                ('transport_mode', models.CharField(blank=True, default='', max_length=32, verbose_name='Transport mode')),
                ('alpn', models.CharField(blank=True, default='', max_length=128, verbose_name='TLS ALPN')),
                ('inventory', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='endpoints', to='providers.providerinventoryversion')),
            ],
            options={
                'ordering': ('inventory_id', 'external_id'),
                'constraints': [
                    models.UniqueConstraint(fields=('inventory', 'external_id'), name='unique_endpoint_per_inventory'),
                    models.CheckConstraint(condition=models.Q(('port__gte', 1), ('port__lte', 65535)), name='provider_endpoint_valid_port'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ProviderLease',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_id', models.CharField(blank=True, default='', max_length=255, verbose_name='Provider-side lease id')),
                ('secret_reference', models.UUIDField(blank=True, null=True, verbose_name='External secret reference')),
                ('credential_revision', models.PositiveIntegerField(default=1, verbose_name='Credential revision')),
                ('state', models.CharField(choices=[('provisioning', 'Provisioning'), ('active', 'Active'), ('suspended', 'Suspended'), ('revoked', 'Revoked'), ('failed', 'Failed')], default='provisioning', max_length=16, verbose_name='Lifecycle state')),
                ('expires_at', models.DateTimeField(blank=True, null=True, verbose_name='Provider expiry')),
                ('quota_limit_bytes', models.PositiveBigIntegerField(blank=True, null=True, verbose_name='Provider quota')),
                ('quota_used_bytes', models.PositiveBigIntegerField(blank=True, null=True, verbose_name='Provider usage')),
                ('last_synced_at', models.DateTimeField(blank=True, null=True, verbose_name='Last provider sync')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='leases', to='providers.provider')),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='provider_leases', to='vpn.uservpn')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('provider', 'subscription'), name='unique_provider_lease_per_subscription'),
                    models.UniqueConstraint(condition=~models.Q(('external_id', '')), fields=('provider', 'external_id'), name='unique_external_provider_lease'),
                    models.CheckConstraint(condition=~models.Q(('state', 'active')) | (~models.Q(('external_id', '')) & models.Q(('secret_reference__isnull', False))), name='active_provider_lease_has_references'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ProviderProbeResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('credential_revision', models.PositiveIntegerField(verbose_name='Credential revision')),
                ('vantage', models.CharField(max_length=64, verbose_name='Probe vantage')),
                ('state', models.CharField(choices=[('healthy', 'Healthy'), ('unhealthy', 'Unhealthy')], max_length=16, verbose_name='Probe state')),
                ('observed_egress', models.GenericIPAddressField(blank=True, null=True, verbose_name='Observed tunnel egress')),
                ('latency_ms', models.PositiveIntegerField(blank=True, null=True, verbose_name='End-to-end latency')),
                ('error_class', models.CharField(blank=True, default='', max_length=64, verbose_name='Safe error class')),
                ('checked_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Checked at')),
                ('endpoint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='probe_results', to='providers.providerendpoint')),
                ('lease', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='probe_results', to='providers.providerlease')),
            ],
            options={
                'ordering': ('endpoint_id', 'vantage', '-checked_at'),
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('lease__isnull', True)), fields=('endpoint', 'credential_revision', 'vantage'), name='unique_shared_provider_probe_verdict'),
                    models.UniqueConstraint(condition=models.Q(('lease__isnull', False)), fields=('endpoint', 'lease', 'credential_revision', 'vantage'), name='unique_leased_provider_probe_verdict'),
                ],
            },
        ),
        migrations.CreateModel(
            name='CompiledSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('client_family', models.CharField(max_length=32, verbose_name='Client renderer family')),
                ('revision', models.PositiveBigIntegerField(verbose_name='Subscription-local revision')),
                ('state', models.CharField(choices=[('active', 'Active'), ('superseded', 'Superseded'), ('revoked', 'Revoked')], default='active', max_length=16, verbose_name='Publication state')),
                ('storage_key', models.CharField(max_length=512, unique=True, verbose_name='Immutable object reference')),
                ('content_digest', models.CharField(max_length=64, validators=[django.core.validators.RegexValidator('^[0-9a-f]{64}$', 'Expected a lowercase SHA-256 digest.')], verbose_name='Compiled content digest')),
                ('inventory_digest', models.CharField(max_length=64, validators=[django.core.validators.RegexValidator('^[0-9a-f]{64}$', 'Expected a lowercase SHA-256 digest.')], verbose_name='Input inventory digest')),
                ('generated_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Generated at')),
                ('published_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Published at')),
                ('expires_at', models.DateTimeField(verbose_name='Effective access expiry')),
                ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='compiled_snapshots', to='vpn.uservpn')),
            ],
            options={
                'ordering': ('subscription_id', 'client_family', '-revision'),
                'constraints': [
                    models.UniqueConstraint(fields=('subscription', 'client_family', 'revision'), name='unique_compiled_snapshot_revision'),
                    models.UniqueConstraint(condition=models.Q(('state', 'active')), fields=('subscription', 'client_family'), name='unique_active_compiled_snapshot'),
                    models.CheckConstraint(condition=models.Q(('expires_at__gt', models.F('generated_at'))), name='compiled_snapshot_expiry_after_generation'),
                ],
            },
        ),
    ]
