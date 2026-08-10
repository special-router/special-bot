# Historical project notes

This file is intentionally short and **not** a current source of truth. Each
entry records the state at that date; later entries may supersede it. Current
production state belongs in [STATUS.md](STATUS.md).

- **2026-08-07:** the legacy route was restored without changing installed
  client configurations. Aggregate reconciliation preserved entitled and
  compatibility clients.
- **2026-08-08 (superseded):** domain-backed subscription transport and one
  protected canary were verified while customer-facing delivery was still
  disabled. Monitoring implementation was moved into the tracked bot
  repository. The 48-hour soak was waived by owner decision and never run.
- **2026-08-10:** subscription-first bot delivery was enabled. A custom Django
  proxy replaced 3x-ui plain subscription output so every subscription carries
  its own `UserVPN` UUID. Direct VLESS remained the rollback path.
- **2026-08-10:** production web/Celery/beat/monitoring were consolidated on one
  reproducible image; Celery workers moved to `--pool=solo`; a persistent 1 GiB
  swapfile and NL-only BOT origin firewall policy were enabled. L0/L1/L2 were
  healthy at closeout.
- **2026-08-10:** SPECIAL-only recovery/deploy/backfill/rotation/verifier scripts
  were moved into this repository under `ops/scripts/`. The separate
  `vpn-ops` workspace remains a different VPN service and is not a SPECIAL Bot
  operational source.
- **2026-08-10:** The ED25519 public key was installed and verified for key-only
  SSH access on BOT and NL MAIN from a client configured with
  `PasswordAuthentication=no`. Server-side password authentication/root login
  were not disabled; that remains a separately gated hardening window. The
  guarded production verifier passed at revision `d13a978` with entitlement
  missing zero, healthy Host/L0/L1/L2 state, external BOT subscription-origin
  access blocked, and redundancy/legacy-retirement gates still false.

Detailed incident transcripts, bearer URLs, client UUIDs, credentials, raw
environment values and temporary research artifacts are intentionally excluded
from Git. Use approved private operational records when historical detail is
required.
