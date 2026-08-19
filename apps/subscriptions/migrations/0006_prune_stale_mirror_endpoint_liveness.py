import datetime

from django.db import migrations
from django.utils import timezone


# Deliberately the same 86400 seconds as the default of
# SUBSCRIPTION_BACKUP_LIVENESS_PRUNE_AFTER_SECONDS (bot/settings.py), not a
# read of that setting: a migration must not depend on a live setting whose
# value can change independently of the migration graph.
PRUNE_AFTER_SECONDS = 86400


def prune_stale_verdicts(apps, schema_editor):
    MirrorEndpointLiveness = apps.get_model('subscriptions', 'MirrorEndpointLiveness')
    horizon = timezone.now() - datetime.timedelta(seconds=PRUNE_AFTER_SECONDS)
    MirrorEndpointLiveness.objects.filter(checked_at__lt=horizon).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0005_mirrorendpointliveness_probed_from'),
    ]

    operations = [
        migrations.RunPython(prune_stale_verdicts, reverse_code=migrations.RunPython.noop),
    ]
