# Compatibility-client migration

Compatibility-only clients are active transport identities that do not have a
verified SPECIAL Bot owner. They are preserved during migration and are never
claimed from remarks, traffic, IP addresses or Telegram guesses.

## Ownership workflow

1. The customer contacts support through the approved private channel.
2. Support verifies account ownership and the currently installed client
   privately. Credentials are not copied into tickets or chat history.
3. Support chooses one explicit compatibility identity and one explicit
   `UserVPN` record. A second operator checks the mapping.
4. The mapping is recorded in protected operational storage; Git and monitoring
   retain aggregate state only.
5. The existing UUID is preserved when technically possible. A subscription
   URL is delivered privately, and the direct key remains rollback.
6. Repeated protected client evidence confirms subscription operation before
   marking the mapping migrated.

## Batch gates

- dry-run inventory before every batch;
- one-to-one mapping, no duplicate owner or identity;
- entitled balance at apply time;
- no inbound deletion, UUID rotation or Reality change;
- bounded batches with legacy/control-plane and L2 checks after each batch;
- stop on any entitled-missing count or customer regression.

## Retirement gates

A legacy transport or direct-key support path may be retired only when:

- every compatibility identity is mapped or explicitly approved for deletion;
- every entitled customer has working subscription evidence;
- no unresolved direct-only support cases remain for the rollback period;
- independent origins and paging are operational;
- rollback configuration and database backups are current;
- the owner approves the exact inbounds/assets being removed.

Stopped legacy application containers/images are a separate cleanup decision.
Shared PostgreSQL and Redis are live dependencies and are never included in
legacy-app cleanup.
