# SPECIAL Bot

Канонический репозиторий приложения и операционной документации **SPECIAL Bot**.
Это отдельный VPN-сервис: материалы других VPN-проектов сюда не переносятся и
не используются как operational source of truth.

**Начинать отсюда: [`CLAUDE.md`](CLAUDE.md)** — точка входа для человека и для
агента. Дальше — [`docs/CONTEXT-MAP.md`](docs/CONTEXT-MAP.md): что именно
затрагивает задача вашего типа.

Старые snapshots, credential-bearing команды, bearer URLs и сырые research-
артефакты в Git не сохраняются.

## Что где

| Документ | Отвечает на вопрос |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Три хоста, одна команда проверки, правила, которые нельзя нарушать |
| [docs/CONTEXT-MAP.md](docs/CONTEXT-MAP.md) | Файлы, флаги, проверка и риски по типу задачи |
| [docs/FLAGS.md](docs/FLAGS.md) | Все настройки: значение по умолчанию и production-значение |
| [docs/OPEN-ITEMS.md](docs/OPEN-ITEMS.md) | Что не доделано и что именно этому мешает |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Как реально идёт трафик и кто чем владеет |
| [docs/STATUS.md](docs/STATUS.md) | Что живёт в production сейчас |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Как выкатывать и что при этом никогда не перезапускается |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Безопасные проверки, gates мутаций, ротация секретов |
| [docs/MONITORING.md](docs/MONITORING.md) | Слои L0/L1/L2/Host и что каждый доказывает |
| [docs/SECURITY-CREDENTIALS.md](docs/SECURITY-CREDENTIALS.md) | Политика обращения с секретами |
| [docs/SCALE-ARCHITECTURE.md](docs/SCALE-ARCHITECTURE.md) | Целевое состояние, не текущее |
| [docs/COMPATIBILITY-MIGRATION.md](docs/COMPATIBILITY-MIGRATION.md) | Клиенты без подтверждённого владельца |
| [docs/CLIENT-GUIDE.md](docs/CLIENT-GUIDE.md) | Инструкция для пользователя, публиковать можно |
| [docs/MIRROR-INBOUNDS-SPEC.md](docs/MIRROR-INBOUNDS-SPEC.md) | Внешние backup-провайдеры и внутренний canary |
| [docs/INBOUND-DIAGNOSTICS-SPEC.md](docs/INBOUND-DIAGNOSTICS-SPEC.md) | Пер-UUID диагностика — дизайн, не реализовано |
| [docs/TIMEWEB-BOT-SSH-RECOVERY.md](docs/TIMEWEB-BOT-SSH-RECOVERY.md) | Когда сам SSH на BOT недоступен |
| [docs/HISTORY.md](docs/HISTORY.md) | Хронология, не источник текущего состояния |
| [`ops/scripts/`](ops/scripts/) | Guarded-скрипты оператора |

`ops/scripts/validate_repository.py` проверяет документацию механически: битые
ссылки, устаревшие маркеры, паттерны секретов и соответствие между настройками в
`bot/settings.py` и таблицами в `docs/FLAGS.md`. Он входит в
`ops/scripts/verify_scale_closeout.sh`.
