from django.core.management.base import BaseCommand

from apps.telegram_bot.models import Broadcast


class Command(BaseCommand):
    help = 'Print the aggregate recipient count for a broadcast audience without sending anything.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--audience',
            choices=[choice[0] for choice in Broadcast.AUDIENCE_CHOICES],
            default=Broadcast.AUDIENCE_SUBSCRIPTION_READY,
        )

    def handle(self, *args, **options):
        broadcast = Broadcast(audience=options['audience'])
        self.stdout.write(str(broadcast.recipient_queryset().count()))
