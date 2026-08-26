# Client subscription guide

This document is safe to publish. Never replace placeholders with a real
subscription URL: each URL is bearer access data and must be delivered only by
the bot to its entitled owner.

## Recommended setup

1. Install a maintained client that supports standard base64 VLESS
   subscriptions. The current reference client is happ; compatible V2Ray/Xray
   clients may also work.
2. In the SPECIAL Bot, open the key/profile screen and copy the subscription
   URL privately.
3. In the client choose **Add subscription from URL**, paste it, then update the
   subscription.
4. The list should contain a non-working status item followed by working
   endpoints. The status item shows remaining subscription days and must not be
   selected as a route.
5. Enable automatic subscription refresh in the client if supported. Endpoint
   selection remains client-side until the bot publishes a measured selection
   policy.

## Troubleshooting

- If import fails, update the client and retry once. Do not post the URL in a
  public chat or screenshot.
- If one endpoint fails, select another working endpoint from the same
  subscription. Do not edit UUID, Reality, SNI or other fields manually.
- If every endpoint fails, contact support with: client name/version, ISP,
  country/region, approximate UTC time and coarse error text. Do not send the
  subscription URL, direct key, UUID or screenshots containing them.
- Existing users may temporarily use their previously issued direct VLESS key
  as rollback. New support flows should prefer subscription delivery; direct
  keys are not the target architecture.

## Router clients

Happ/OpenWrt/PassWall2/router automation is not declared production-ready until
there is a maintained integration repository, target hardware and a responsible
owner. Manual import of the same standard subscription may be tested only on
explicit canary devices.

The maintained compatibility API for an owned router is
`GET /api/v1/vpn/box/<vpn_uuid>/config/`. The UUID is a per-customer bearer
credential: pass it only in private provisioning, never logs or support
tickets. The endpoint returns one Xray `outbounds` document assembled from the
active control-plane settings; callers do not receive Remnawave credentials.
Unknown and Django-disabled UUIDs return the same 404, and every response is
`no-store`. This endpoint does not replace the customer subscription portfolio:
it is the narrow compatibility surface for router firmware that already stores
the customer's UUID.


New Remnawave clients receive a cryptographically random 32-character
subscription id in Django before the panel create call; the same value is sent
as `shortUuid` and checked against the panel response. Historical records with
an empty `sub_id` are repaired with
`python manage.py repair_remnawave_sub_ids` (dry-run) and explicit `--apply`.
The command copies an existing panel `shortUuid` only after matching the VLESS
identity and Telegram owner and proving uniqueness. `--notify` queues a private,
auditable notification only for rows actually repaired; it never puts a URL or
credential in the message.