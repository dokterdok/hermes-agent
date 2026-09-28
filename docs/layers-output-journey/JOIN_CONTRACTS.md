# Explicit integration contracts

`plan.json` is the authoritative inventory of immutable public commits, Git
blobs, source spans, and assembled digests. Its `join` entries are literal,
reviewable integration code, not hidden conflict resolutions. This note
explains why the joins exist without embedding any lower-owner module.

| Composed path | Preserved contract |
| --- | --- |
| `gateway/hosted_room_artifacts.py` | Combine the published owner-context keyword and authority resolver with the existing artifact path and optional already-open database connection. |
| `gateway/hosted_room_authority.py` | Keep both published authority-selection parameters in the common constructor. |
| `gateway/hosted_room_replicas.py` | Keep the public replica-audit authorization callback parameter. The existing audit implementation comes from a pinned public source span. |
| `gateway/hosted_rooms.py` | Use the published Retention store schema initializer before invoking its existing recovery sweep. |
| `gateway/session_hosted_rpc.py` | Preserve both Input-custody and Output-owner submit arguments. Use the committed Input handle's payload when present, otherwise the existing normalizer. Carry the existing route and Output authorization callbacks through the Runtime writer callback so neither displaces the other. Preserve the existing winning admission-row lookup, callback context, lifecycle initialization, registration, and post-registration cleanup ordering. |
| `gateway/session_hosted_service.py` | Bind the published committed-payload validator and existing admission row to Output admission. Add the existing Output constructor imports and perform cleanup replay only after exact service registration and transport setup. |
| `gateway/session_hosted_transport.py` | Carry the existing owner-output context and captured admission authorizer across the loop-safe submit boundary. |
| `hermes_state.py` | Preserve both Input-custody and Runtime write-authorization keyword arguments at the facade. |
| `hermes_state_runtime.py` | Enter the existing admission-store validation path when either custody or a write authorizer is present. This preserves the current Output writer check rather than silently bypassing it on a non-custody submission. |
| `tests/gateway/test_session_hosted_rpc.py` | Combine the already-published test imports for the composed fixture. No assertion is weakened. |
| `tui_gateway/hosted_room_driver.py` | Preserve the published Output binding import and the captured stop task in the shared driver. |

The Input handle, payload normalizer, admission validator, Runtime live
connections, Output lifecycle, Route context/authorization, and Retention
schema/audit methods are supplied by the public owners. These fragments connect
those contracts; they do not provide a substitute database, identity authority,
cleanup implementation, or private completion provider.

The source-only reviews and the successful canonical selection cover their
stated scopes. They do not confer blanket approval on the wider integrated
repository, native execution, arbitrary future plans, or protected lower-owner
changes. If a future provider changes, update the exact public pin and affected
contract explicitly rather than following its moving branch tip.
