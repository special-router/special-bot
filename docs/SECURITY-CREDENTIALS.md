# Security and credential policy

This policy replaces any historical credential registry. Secret values,
passwords, login codes, bearer URLs, private keys, client UUIDs and Telegram
identifiers do not belong in the repository, documentation, issue trackers,
chat transcripts, shell history, or ordinary logs.

## Handling rules

- Store values only in approved ignored secret storage with restrictive file
  permissions; version-control only key names and non-secret examples.
- Refer to variables by environment-key name. Never echo, serialize, screenshot
  or paste their values into commands or debugging output.
- Use approved key-based access and verified host identity. Do not use
  password-bearing `curl` commands, `sshpass`, or inline credential arguments.
  The RU relay host is the single documented exception, through
  `ops/scripts/relay_ssh.sh`; see
  [RUNBOOK.md](RUNBOOK.md#ru-relay-administrative-access). It does not extend to
  BOT or NL, and it is an open item, not a pattern to copy.
- Treat a subscription URL as bearer access data: private delivery only, never
  admin display, metrics, traces, JSONL, or docs.
- Monitoring and audits report sanitized aggregates and coarse error classes,
  never client identifiers or connection material.
- Protected backups and temporary probe material require restricted access and
  secure cleanup through approved operations procedures.

## Rotation and incident response

Prior tracing and historical documentation exposure create credential-rotation
risk. Inventory potentially exposed access material, rotate it through the
approved owner-controlled process, invalidate superseded material where safe,
and verify service access without exposing replacement values. Do not copy old
transcript commands or historical secret-bearing artifacts into new documents.

If sensitive material appears in output, stop sharing it, restrict the artifact,
notify the credential owner through the approved channel, and follow the
rotation process. Logs must remain free of sensitive values even during
incident investigation.
