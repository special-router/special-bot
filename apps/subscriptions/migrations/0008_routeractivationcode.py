import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('subscriptions', '0007_subscriptionaccesstoken')]
    operations = [migrations.CreateModel(
        name='RouterActivationCode',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('code_hash', models.CharField(max_length=64, unique=True, validators=[
                django.core.validators.RegexValidator('^[0-9a-f]{64}$', 'Expected a lowercase SHA-256 digest.')])),
            ('code_hint', models.CharField(db_index=True, max_length=12)),
            ('expires_at', models.DateTimeField()),
            ('consumed_at', models.DateTimeField(blank=True, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('subscription', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                               related_name='router_activation_codes', to='vpn.uservpn')),
        ],
        options={'constraints': [models.CheckConstraint(
            condition=models.Q(('expires_at__gt', models.F('created_at'))),
            name='router_activation_expiry_after_creation')]},
    )]
