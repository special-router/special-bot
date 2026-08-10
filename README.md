# SPECIAL router documentation

Канонический репозиторий приложения и операционной документации **SPECIAL Bot**.
Это отдельный VPN-сервис: материалы других VPN-проектов сюда не переносятся и
не используются как operational source of truth.

Текущее production-состояние ведётся только в [`docs/STATUS.md`](docs/STATUS.md).
Короткая обезличенная история находится в [`docs/HISTORY.md`](docs/HISTORY.md).
Старые snapshots, credential-bearing команды, bearer URLs и сырые research-
артефакты в Git не сохраняются.

- [Current status](docs/STATUS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Safe operations runbook](docs/RUNBOOK.md)
- [Timeweb BOT SSH recovery](docs/TIMEWEB-BOT-SSH-RECOVERY.md)
- [Subscription migration](docs/SUBSCRIPTION-MIGRATION.md)
- [Compatibility-client migration](docs/COMPATIBILITY-MIGRATION.md)
- [Scale-ready architecture](docs/SCALE-ARCHITECTURE.md)
- [Client subscription guide](docs/CLIENT-GUIDE.md)
- [Monitoring](docs/MONITORING.md)
- [Security and credential policy](docs/SECURITY-CREDENTIALS.md)
- [Roadmap](docs/ROADMAP.md)
- [Historical notes](docs/HISTORY.md)
- Guarded operator scripts: [`ops/scripts/`](ops/scripts/)
- Local scale-readiness implementation/CI verifier: `ops/scripts/verify_scale_closeout.sh`
  (it does not claim production origin/paging/migration readiness)
