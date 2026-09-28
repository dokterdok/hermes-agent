# Fault injection for the adversarial matrix

Use this with `artifacts/ADVERSARIAL_MATRIX.md`. Pins, ownership, and which rows stay OPEN are in that file's controller section. Sleeps, poll loops, and "wait until status looks settled" are not oracles. A supplier proof on `6f2aeb…` is not a pass of assembly target `cb8d692…`.

## Barrier

Hold the effect on a `threading.Event` (or an equivalent one-shot gate) inside the task-owned process, at the boundary the row names.

1. Arm the gate before the call under test.
2. Let the call run until it blocks on the gate. A second connection must already be able to read the commit the row says is durable, or must already observe that the commit is absent.
3. Apply the fault (kill, revoke, switch room, plant the sentinel).
4. Release the gate only if the row says the reply is delivered after the fault. For crash rows, do not release; kill instead.

The gate lives in the test harness and is reached through a hook the candidate already has (the attachment store, the idempotency reserve, the peer HTTP admit function). Adding a product feature to make the gate easier is out of scope for this review.

## Crash and restart

- Signal only the pid recorded when the harness spawned that process (`SIGKILL`).
- Restart is a new process, same disposable `HERMES_HOME`, no reused objects.
- Read SQLite and files from a new connection after the kill. WAL: checkpoint or open with the WAL present so the durable commit is what a real restart would see.
- `RunIdempotencyStore.durable is False` means the reservation was process memory. That cut fails R2 and R11.

## What is the oracle

Rows count table rows, digests, file bytes, pids, and `gateway_state.json`. They do not count:

- the function's return value or a `saved: true` / `accepted: true` flag
- `PeerRunsHTTPClient._runs` or any other process-local map
- `localStorage` keys `hermes.desktop.canonicalGroupSends.v1` and `hermes.desktop.preparedSubmissions.v1`
- a notification callback or a manually updated terminal row

## Desktop close

Closing the disposable Electron app may stop the serve child it spawned (`before-quit` → `backendShutdown`). The room authority process in R10 and R13 is a different pid. Record both pids before the close.

## Bound the run

One task-owned process, one disposable home, a deadline on the barrier wait. If the gate never trips, the row is blocked, not passed.
