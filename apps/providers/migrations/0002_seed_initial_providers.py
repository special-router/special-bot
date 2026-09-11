from django.db import migrations


def seed_initial_providers(apps, schema_editor):
    Provider = apps.get_model('providers', 'Provider')
    for slug, name in (('a-service', 'A-Service'), ('vpnstar', 'VPNStar')):
        Provider.objects.get_or_create(
            slug=slug,
            defaults={
                'name': name,
                'adapter': slug,
                'credential_mode': 'shared',
                'enabled': False,
                'per_user_lifecycle': False,
                'usage_tracking': False,
                'priority': 100,
                'capabilities': {'allowed_endpoint_hosts': []},
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('providers', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_initial_providers, migrations.RunPython.noop),
    ]
