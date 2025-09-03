from __future__ import annotations

import random
import secrets
from typing import List

from django.core.management.base import BaseCommand

from apps.servers.models import Server


def generate_ipv4_address() -> str:
    """Generate a documentation-range IPv4 address suitable for tests.

    Uses one of the IANA-reserved blocks (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)
    to avoid any accidental real-world IPs.
    """
    base_blocks = ["192.0.2", "198.51.100", "203.0.113"]
    base = random.choice(base_blocks)
    last_octet = random.randint(1, 254)
    return f"{base}.{last_octet}"


def generate_password(length: int = 16) -> str:
    # Token with URL-safe alphabet; slice to requested length
    return secrets.token_urlsafe(24)[:length]


class Command(BaseCommand):
    help = "Create test Server records. By default creates 10 entries."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=10,
            help="How many Server records to create (default: 10)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing Server records before seeding",
        )

    def handle(self, *args, **options) -> None:
        count: int = options["count"]
        clear: bool = options["clear"]

        if clear:
            deleted, _ = Server.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing Server records"))

        servers_to_create: List[Server] = []

        for idx in range(1, count + 1):
            ip_address = generate_ipv4_address()
            ssh_username = f"ssh_user_{idx}"
            ssh_password = generate_password()
            vpn_username = f"vpn_user_{idx}"
            vpn_password = generate_password()
            vpn_key = generate_password(24)
            vpn_url = f"https://{ip_address}:2053"

            servers_to_create.append(
                Server(
                    name=f"Test Server {idx}",
                    ip_address=ip_address,
                    ssh_username=ssh_username,
                    ssh_password=ssh_password,
                    vpn_username=vpn_username,
                    vpn_password=vpn_password,
                    vpn_key=vpn_key,
                    vpn_url=vpn_url,
                )
            )

        created = Server.objects.bulk_create(servers_to_create)
        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} Server records"))




