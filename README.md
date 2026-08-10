# SPECIAL router documentation

Каноническая документация по текущему состоянию SPECIAL VPN. Короткая обезличенная история находится в [`docs/HISTORY.md`](docs/HISTORY.md).
Старые operational snapshots, credential-bearing команды и сырые research-материалы
в репозиторий не перенесены.

**Current status (2026-08-10):** legacy route, RU relay mirror and domain
subscription transport are live; **subscription delivery is enabled** and all
entitled users have a `subId`; the bot UI issues subscription URLs as the
primary path with direct `vless://` retained as fallback. A dedicated
non-working `📊 Подписка` inbound shows the remaining days first in happ.
Production monitoring is deployed (L0/L1 healthy, L2 pending canary recheck).
The 48-hour soak is waived rather than passed. There is no mass migration of
compatibility-only clients.

- [Current status](docs/STATUS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Safe operations runbook](docs/RUNBOOK.md)
- [Subscription migration](docs/SUBSCRIPTION-MIGRATION.md)
- [Monitoring](docs/MONITORING.md)
- [Security and credential policy](docs/SECURITY-CREDENTIALS.md)
- [Roadmap](docs/ROADMAP.md)
- [Historical notes](docs/HISTORY.md)
