## The user problem

#97744 lets a Group Chat continue on one gateway after Desktop closes. A Group
Chat containing Bots from two gateways still needs a safe way for the home
gateway to dispatch an exact member turn, recover it after response loss, and
observe Stop or completion without using Desktop as a courier.

This draft adds that backend link. It is the direct, text-first transport layer
under the later Desktop continuity UX.

> [!IMPORTANT]
> This PR is stacked on #97712 and #97744. Review only the commits after the
> current #97744 head. It does not enable a new Desktop creation path by itself.

## What this layer provides

- Durable `/v1/runs` admission and status using one idempotency identity and
  immutable request fingerprint.
- Asynchronous peer commands that recover the same admitted run after response
  loss or process restart.
- A target-issued RoomLink grant scoped to the exact room, home and authority
  epoch, member, target install, target profile, permissions, execution policy,
  and expiry.
- Live capability verification before first dispatch.
- Direct authenticated transport with credential-safe redirect handling.
- Durable route and run receipts, bounded status polling, replay-safe dispatch,
  and exact-run Stop.
- Fail-closed mixed-version behavior: unsupported peers stay unavailable rather
  than weakening the grant or silently running a different Bot.

## Reliability behavior

- A peer proven unreachable before admission keeps the exact turn queued for a
  bounded retry.
- Timeout, reset, or 5xx after submission remains uncertain and reuses the same
  durable dispatch identity; it is never sent through a second route.
- Completion may win a Stop race, but Stop remains pending until the exact
  execution generation is terminal.
- Home restart reloads the route and receipt without resubmitting speculative
  work.
- Message identity is bound to immutable content; conflicting replay fails.
- Superseded authority, stale reservations, revoked grants, and late receipts
  cannot commit new room state.

## Deliberate boundaries

- Direct link only; no mesh, relay, or automatic authority failover.
- Cross-gateway text turns only.
- No Desktop green path, files, Bot artifacts, or messaging commands.
- Non-loopback peers require authenticated HTTPS.
- Application-layer E2EE remains a later transport choice; this PR does not
  introduce a new crypto system.

Those capabilities remain separate layers in #97681.

## Source composition

- Closed #95965 supplied the reviewed durable peer-run prerequisites and this
  recut preserves their source commits: #88408 by @pesho-vsn, #94336 by
  @giaiant, #88819 by @pierrenode, and #93952 by @liuhao1024.
- Closed #95966 supplied the scoped RoomLink transport and failure contract.
- This current-main recut carries forward only the relevant final P1/P2 repairs
  from the field-tested integration build.

## Validation

Exact head, focused counts, two-gateway text canary, and GitHub CI will be added
before this draft leaves draft status.

The prior field build already passed same-network and separate-network
two-gateway UAT, restart recovery, Stop, scoped route revocation, and offline
queue/reconnect. This recut repeats the text-only scenarios that match its
narrower claim.

## Related work

- #97681: six-layer production and field-testing plan
- #97712: durable authority and replay foundation
- #97744: same-gateway runner parent
- #95163: gateway-hosted Group Chat design issue
- #91911: identity, delivery, approval, and cancellation direction
- #92931: complementary Desktop relay work, not authority for this link

## Type of change

- [ ] Bug fix
- [x] New feature
- [x] Security boundary
- [x] Tests
- [ ] Refactor
