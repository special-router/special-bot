# Historical project notes

This file is intentionally short and is **not** a current source of truth.

- 2026-08-07: the legacy route was restored without changing existing client
  configurations. The current audited aggregate is 66 entitled, 87
  control-plane and 21 compatibility clients.
- 2026-08-08: the domain-backed 3x-ui subscription transport and one internal
  canary were verified. Customer-facing subscription delivery remains disabled.
- 2026-08-08: monitoring implementation was moved into the tracked bot
  repository in commit `12c8d00` and incorporated into `main`.
- 2026-08-08: `main` was published to the tracked remote. Deployment did not
  occur: approved key-based access to the bot host was unavailable. The
  48-hour soak was waived by owner decision and was never run.

Detailed historical incident logs, chat snapshots, credential-bearing command
examples and research artifacts are intentionally not copied into the
repository. Use approved private operational records when historical detail is
required.
