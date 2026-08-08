import json

from django.core.management.base import BaseCommand

from apps.monitoring.models import MonitorState


class Command(BaseCommand):
    help = 'Read-only aggregate SPECIAL monitoring status.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        states = list(MonitorState.objects.order_by('layer'))
        for state in states:
            row = {
                'layer': state.layer,
                'last_ok': state.last_ok,
                'alert': state.alert,
                'consecutive_failures': state.consecutive_failures,
                'error_class': state.error_class or None,
                'checked_at': state.checked_at.isoformat(),
            }
            if options['as_json']:
                self.stdout.write(json.dumps(row, sort_keys=True))
            else:
                self.stdout.write(
                    ' '.join(
                        (
                            f"layer={row['layer']}",
                            f"ok={str(row['last_ok']).lower()}",
                            f"alert={str(row['alert']).lower()}",
                            f"failures={row['consecutive_failures']}",
                            f"error_class={row['error_class'] or 'none'}",
                        )
                    )
                )
