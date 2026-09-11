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

## Global RU relay rollout — 2026-09-10

- Scope: enabled the existing owned RU relay host in Remnawave for every active
  subscription; no provider or cross-project endpoint was added.
- Root cause found during the guarded rollout: the canary renderer appended its
  legacy relay link after the panel had already supplied the same endpoint,
  producing two lines for one account. The first rollout was automatically
  rolled back before completion.
- Fix: endpoint deduplication now compares host and port at the final append
  boundary. A regression test covers a panel relay whose URI bytes differ from
  the legacy canary URI.
- Deployment: the running production image received only the one-file runtime
  fix and was persisted as a dedicated image, avoiding every unrelated dirty
  checkout change. The previous image and host-state backup remain available.
- Delivery verification: all 40 enabled subscriptions with a subscription id
  returned panel endpoints, and every one contained exactly one RU relay line.
  Both the canary and an ordinary rendered document returned HTTP 200 with one
  relay endpoint.
- Protocol verification: L2 passed for TCP, XHTTP and gRPC, and the RU relay
  completed a real VLESS egress test. All seven production containers remained
  running with zero restarts after the web restart.
- Rollback guard: the timed rollback was cancelled only after validation;
  host-state and image rollback artifacts are present.
