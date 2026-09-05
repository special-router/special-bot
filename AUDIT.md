# Canary relay audit

- Scope: one confirmed `UserVPN` record only; no handle or personal name is
  stored here.
- Mechanism: a default-off endpoint setting plus an exact, fail-closed
  `UserVPN.id` allowlist. The renderer accepts only `[801]` for this canary.
- Global `Server.client_vpn_host` remains empty, so existing subscriptions are
  not changed through shared database state.
- Endpoint configuration contains public host/port metadata only. Subscription
  URLs, client UUIDs, Reality material and panel credentials are excluded.
- Acceptance: the canary document includes one `белые списки` relay endpoint;
  an ordinary subscription does not; protocol testing must prove real internet
  egress through the relay before commit and deploy are declared complete.