# Hermes Bot Group Chats: Beelink handoff

Date: 2026-08-29

This document is the authoritative handoff for continuing the Hermes Bot Group Chat work from a different computer. It is intentionally self-contained and contains no credentials, private hostnames, production conversation text, or private network details.

## Read first

- Product and production plan: https://github.com/NousResearch/hermes-agent/issues/97681
- Durable authority/replay PR: https://github.com/NousResearch/hermes-agent/pull/97712
- Same-gateway runner PR: https://github.com/NousResearch/hermes-agent/pull/97744
- Accepted-turn lifecycle PR: https://github.com/NousResearch/hermes-agent/pull/94697
- Direct hosted-room design issue: https://github.com/NousResearch/hermes-agent/issues/95163
- Bot Mode issue-class tracker: https://github.com/NousResearch/hermes-agent/issues/94726
- Gateway/dashboard Group Chat issue: https://github.com/NousResearch/hermes-agent/issues/89995

## User goal and non-negotiables

The goal is a real open-source multi-Bot Group Chat, not merely a profile roster or a Desktop-only UI:

1. Bots on one or several independently owned gateways must keep working together after Desktop closes.
2. A user must be able to reconnect from another Hermes client, see the same ordered history and status, send a follow-up, retry, download a result, or Stop.
3. Normal creation remains simple. Hermes automatically chooses one of three continuity modes: independent cross-gateway, one gateway, or Desktop-required compatibility.
4. User-facing language is **Group Chat**, not internal “room” vocabulary. Hide networking and topology from the green path.
5. Files are required parity: user attachments and Bot-generated files must work on one gateway, across gateways, and after Desktop closes.
6. Connected messaging adapters must eventually list, inspect, send to, retry, and Stop Group Chats. Do not limit the design to Signal or Telegram.
7. Human messages must always have a responder. Explicit mentions narrow the set; later Bot-to-Bot work uses typed named handoffs to avoid reply loops.
8. Time, reply, retry, hop, token, storage, and resource budgets must prevent silent runaway Group Chats and warn the owner.
9. Optional offline Bots should not block healthy participants. Required or explicitly mentioned Bots pause with actionable Retry or Continue-without choices.
10. Preserve contributor authorship, avoid duplicate PRs, scan current upstream first, and keep changes split into reviewable/cherry-pickable layers.
11. Move PRs out of draft when their stated CI/UAT gate is satisfied. Keep issue #97681 and its visuals current as work progresses.
12. Do not touch production gateways or user data from the Beelink until the user separately grants access. There is no Beelink SSH authorization in this handoff.

## Why the direction is distinctive

Grok Bot demonstrates the commercial demand for Bots that collaborate in one thread and keep working after the laptop closes, but continuity belongs to a proprietary hosted service. OpenClaw has durable single-gateway sessions, beta Swarm fan-out, and beta pairwise encrypted Reef delivery, but no authoritative shared Group Chat spanning independently owned gateways.

The Hermes opportunity is one durable Group Chat coordinating Bots that retain execution, tools, credentials, and private state on user-controlled laptops, homelabs, and VPS gateways.

Useful prior-art ideas already added to the plan without changing the topology:

- explicit replay gap/reset behavior;
- observed-owner generation and log-position fencing;
- one durable dispatch identity with bounded recovery age/attempts;
- message-id/content replay binding and signed receipts;
- optional application-layer E2EE above authenticated transport;
- compact participant progress with enforceable room-wide budgets.

## History: what happened and why

Nine earlier public drafts were closed after a large Bot Mode rebuild made the stack stale and too difficult to review as-is. They were not discarded. Their code, reviews, and UAT evidence were consolidated into a complete integration branch, then rebuilt as smaller current-architecture layers.

Closed source/field receipts:

- #95314: durable authority and replay;
- #95622: same-gateway runner;
- #95965: peer-run prerequisite composition;
- #95966: scoped cross-gateway RoomLink;
- #95967: Desktop automatic continuity UX;
- #96274: messaging control;
- #96760/#96761: durable files and Desktop/messaging surfaces;
- #96789: cross-gateway files;
- #96919: Bot-generated file handoff.

The complete field-tested integration remains available as branch `wip/roomlink-field-integration-20260829` at commit `5bda072c0ffc4c32f4329acb36e545f9db7b7a8a`. It is a reference and field receipt, not a merge candidate.

## Current GitHub branches and exact heads

All branches are on https://github.com/dokterdok/hermes-agent.

| Layer | Branch | Exact head | Public state |
| --- | --- | --- | --- |
| 1 authority/replay | `feat/bot-mode-hosted-room-authority-20260829` | `eea34e63d0c4b1505cb32b4e287fb9b3f384f5bd` | PR #97712, ready |
| 2 same-gateway runner | `feat/bot-mode-same-gateway-continuity-20260829` | `09f58fafa94fee562bc03b00d6c04ec03e9c2f15` | PR #97744, ready |
| 3 cross-gateway backend | `feat/bot-mode-cross-gateway-roomlink-20260829` | `4a53a0978cc4518c0a3644a059d62de7c9603a72` | WIP branch, no PR yet |
| complete field build | `wip/roomlink-field-integration-20260829` | `5bda072c0ffc4c32f4329acb36e545f9db7b7a8a` | reference only |
| handoff docs | `handoff/bot-group-chats-beelink-20260829` | handoff root `487d7d8ca0`; use the remote branch tip | no PR |

The cross-gateway branch includes the parent Layer 1 and Layer 2 commits. Do not independently rebase only Layer 3 if that would rewrite or duplicate the ready parent heads. If upstream changes conflict, update the stack in order: #97712, then #97744, then Layer 3.

## Layer 1 status: ready

PR #97712 is inert (`driver: false`) and changes no Desktop UX. It defines durable identity, ordered events, server-owned authority, authority epochs, typed actor/event admission, idempotent append/replay, bounded storage/listing, and disband tombstones.

Evidence:

- 41 focused tests passed;
- Ruff and diff-check passed;
- all selected GitHub checks passed;
- no Desktop, worker, peer, files, or messaging code in the diff.

## Layer 2 status: ready

PR #97744 adds the same-gateway runner only. It includes durable tasks, leases, Stop fencing, deferred members, explicit Bot handoffs, bounded checkpoints, fair scheduling, gateway supervision, and fail-closed mutating RPCs.

Evidence:

- 195 focused tests passed;
- exact-head isolated same-gateway canary passed:
  `SINGLE_GATEWAY_UAT_OK replies=2 restart_recovered=1 ordered_history=1`;
- all selected GitHub checks passed;
- no Desktop, peer/API, RoomLink, files, artifacts, messaging, or relay code in the diff.

## Layer 3 status: code complete locally, WIP branch pushed

Branch `feat/bot-mode-cross-gateway-roomlink-20260829` stacks on #97744 and contains:

- source-authored durable `/v1/runs` idempotency;
- async peer run/status commands;
- credential-safe redirect handling;
- registry-owning profile routing;
- durable run recovery and bounded pruning;
- target-issued, room/member/profile/epoch-scoped grants;
- live capability and execution-policy verification;
- direct authenticated text transport;
- durable route/run receipts, exact Stop, bounded retry/backoff, restart recovery, revocation, stale reservation fencing, authority fencing, and superseded-grant invalidation;
- legacy roster adoption for new routing metadata;
- resumed runtime-ID preservation during explicit retry;
- text-only target execution-policy binding for model, provider, reasoning, tools, approvals, and turn limit.

Deliberately excluded: Desktop, files/attachments/artifacts, messaging adapters, classic-room mailbox, general relay UI, mesh routing, and automatic authority failover.

Evidence at the exact WIP tree before commit compaction:

- complete focused peer/API/RoomLink boundary: **418 passed, 1 skipped**;
- execution-policy sub-boundaries: 232 + 37 passed;
- Ruff and `git diff --check` passed;
- headless in-process two-gateway HTTP canary passed;
- negative scope check found no Desktop/file/messaging paths in the Layer 3 diff.

The final compacted tree is byte-identical to the tested pre-squash backup. It keeps five contributor-authored commits plus three maintainer-owned composition/hardening commits.

## Authorship to preserve

The Layer 3 branch preserves the source commits composed in closed #95965:

- #88408 by @pesho-vsn: durable scoped `/v1/runs` idempotency;
- #94336 by @giaiant: async peer run/status commands and docs;
- #88819 by @pierrenode: credential-safe redirect handling;
- #93952 by @liuhao1024: registry-owning profile route for peer delivery.

Contributor email mapping files are part of the branch. If any source PR lands first, drop the patch-identical commit during rebase; do not re-author it.

## Exact next steps on Beelink

1. Clone or update the fork and upstream refs:

   ```bash
   git clone https://github.com/dokterdok/hermes-agent.git
   cd hermes-agent
   git remote add upstream https://github.com/NousResearch/hermes-agent.git
   git fetch --all --prune
   ```

2. Read this document directly from the handoff branch if not checked out:

   ```bash
   git show origin/handoff/bot-group-chats-beelink-20260829:docs/handoffs/HERMES_BOT_GROUP_CHATS_BEELINK_HANDOFF_2026-08-29.md
   ```

3. Check out Layer 3 and verify the exact transfer boundary:

   ```bash
   git switch feat/bot-mode-cross-gateway-roomlink-20260829
   git rev-parse HEAD
   git status --short
   ```

   Expected head: `4a53a0978cc4518c0a3644a059d62de7c9603a72`; expected status: clean.

4. Fetch current upstream and check for real overlap before rebasing. At handoff, upstream had advanced beyond the parent base but a file-overlap audit found no Layer 3 overlap. If the stack still merges cleanly, do not churn the ready parent PRs merely to change their base SHA.

5. Re-run the focused Layer 3 gate only if code or base changes. The exact command is in the section below.

6. Run the headless two-gateway canary and then a real text-only same-network and separate-network UAT before moving Layer 3 out of draft.

7. Create Layer 3 as a **draft** PR using `docs/handoffs/HERMES_CROSS_GATEWAY_PR_BODY_DRAFT_2026-08-29.md`. Keep it stacked on #97712/#97744 and make the review tail explicit.

8. Update issue #97681 immediately with the new PR number, current tests/UAT, active-work comment, and regenerated delivery-stack visual. Keep prose unwrapped (`prettier --prose-wrap never`) to avoid odd GitHub line breaks.

9. Only after Layer 3 is published and stable, start the TypeScript Desktop layer. Do not deploy or restart private gateways without fresh user authorization.

## Layer 3 focused gate

Use the worktree's own environment so imports cannot resolve to another editable checkout.

```bash
PYTHONPATH="$PWD" .venv/bin/python -m pytest -q \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py \
  tests/gateway/test_api_server_runs_extraction.py \
  tests/gateway/test_api_server_room_dispatch.py \
  tests/gateway/test_api_server_room_grants.py \
  tests/gateway/test_hosted_room_links.py \
  tests/gateway/test_hosted_room_peer.py \
  tests/gateway/test_hosted_rooms.py \
  tests/gateway/test_hosted_room_driver.py \
  tests/gateway/test_hosted_room_discussion.py \
  tests/gateway/test_hosted_room_execution_policy.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/tools/test_bot_mode_dm.py \
  tests/tui_gateway/test_groups_methods.py \
  tests/tui_gateway/test_hosted_room_driver_runtime.py \
  tests/tui_gateway/test_hosted_room_peer_http.py \
  tests/tui_gateway/test_hosted_room_peer_transport.py \
  tests/tui_gateway/test_hosted_room_service.py \
  tests/tui_gateway/test_hosted_room_prompt_fence.py \
  tests/tui_gateway/test_hosted_room_two_gateway_scoped.py \
  --disable-warnings --maxfail=1

PYTHONPATH="$PWD" .venv/bin/python -m pytest -q \
  tests/tui_gateway/test_hosted_room_two_gateway_scoped.py::test_in_process_scoped_transport_contract_finishes_headlessly

.venv/bin/python -m ruff check $(git diff --name-only 09f58fafa9..HEAD -- '*.py')
git diff --check 09f58fafa9..HEAD
```

## Layer 4 Desktop extraction plan

Merged #96726 deleted the old 16k-line `plugin.js`. Never cherry-pick the old JavaScript implementation from #95967. Port only the typed TypeScript owners.

Primary source commits from the field integration:

- `24612f96e6`: hosted capability selection, replay/outbox, runtime, room model, creation, send/Stop, lifecycle/types/i18n;
- `d54350af08`: multi-gateway planning and automatic creation;
- `0686ed5b00`, `2a558875bc`, `82d0e22a2e`, `eccdc649e6`: replay/outbox/recovery hardening;
- `6c7958241e`: honest fallback copy;
- `7eb70ccc3a`: cached timestamp normalization.

Do not copy final files wholesale because the integration branch also contains Layer 5 files and Layer 6 messaging.

Explicitly restore behavior present in closed #95967 but missing from the final integration:

- 30-second unsupported-gateway reprobe cache (`043dc722dc`);
- queued-versus-working state (`8453adc6fc`);
- visible-reply-only counts (`9a55ea16fc`);
- fallback-identity replay dedupe (`a6875fa727`);
- idle room poll fingerprint cache (`b3e7dec074`);
- uncertain Retry confirmation if Layer 3 retains `groups.retry`.

Current overlaps to inspect before editing: #97577, #97468, #93903, and #91389. UX risks: #97740, #94863, and #89995.

## UX copy requirements

The current creation text is implementation leakage. Replace it in Layer 4.

- Title: **New group chat**
- Description: **Choose 2–6 bots. Hermes will keep the group working after Desktop closes when their connections support it.**
- Empty: **No bots yet. Create a bot first.**
- Fallback: **Couldn't start background work on Mac mini. Keep Desktop open while this group is working.**
- Cross-host setting: **Continues when Desktop is closed** / **Bots keep working together across their connected devices.**
- Desktop setting: **Keep Desktop open while this group is working.** / **Work pauses if Desktop closes.**
- Offline Stop: **Stop requested. It will stop when Mac mini is online.**
- Retry: **The earlier attempt may have finished. Retrying could repeat actions.**

Use short statuses: `Sending…`, `Working`, `Waiting for Research Bot`, `Needs attention`, `Stopping…`, `Stopped`, `Syncing recent activity…`.

## Layer 4 visual/UAT matrix

- Creation: same-host, cross-host, mixed-version, unavailable host; no selector; one fallback notice.
- Continuity: independent, one-host, and Desktop-required settings; light/dark, narrow/wide, EN/JA/ZH/ZH-Hant.
- Close/reopen: send, close Desktop immediately, settle on gateways, reopen this and a second Desktop; same room ID and no duplicate rows.
- Recovery: gateway restart, sequence gap, transient outage, persisted outbox, uncertain create cleanup, storage failure, old gateway upgraded while Desktop stays open.
- Stop/Retry: online/offline Stop, Stop during turn, terminal acknowledgement, restart while stopping, uncertain Retry warning; history retained.
- Settings: open from room and roster, idle rename, block rename while working, online/offline disband, classic behavior unchanged.

## Issue and community coordination

Issue #97681 is the live index. It currently contains:

- the product promise and three continuity modes;
- messaging-control example;
- safety/failure contract;
- Grok Bot/OpenClaw comparison;
- six-layer visual and status table;
- completed test/UAT evidence;
- exact build recipe;
- visible attribution;
- active-work coordination comment.

Awareness comments were added to #95163, #94726, and #89995. Do not spam more threads. Add a new reference only where Layer 3 directly changes the contract or prevents duplicate work.

## Pitfalls already encountered

- Do not resurrect deleted `plugin.js`; use the post-#96726 TypeScript modules.
- Do not publish duplicate PRs. Search open/closed PRs and current main first.
- Use exact worktree imports (`PYTHONPATH=$PWD`) during UAT. An earlier canary accidentally resolved another editable checkout and was discarded.
- Do not infer success from container health; prove protocol behavior and exact reported revision.
- Do not reflash private gateways after every rebase. Rebuild/deploy only at a meaningful UAT gate and only with authorization.
- Do not expose internal “room,” authority, route, grant, or topology vocabulary in normal UI copy.
- Do not add files or messaging to Layer 3. Keep the six-layer boundaries reviewable.
- #94697 and #97744 are semantically compatible but currently overlap `tui_gateway/methods_prompt.py`; test #94697 independently until one lands, then rebase the later branch. Field testers should not resolve that overlap manually.
- The repo moves quickly. Pin symbols to a named commit, fetch upstream before acting, and re-check related PRs/issues immediately before publication.

## Production and access state

The complete field integration commit `5bda072c0f` remains deployed on the private test gateways and the previously packaged Desktop test app. Do not roll it back or replace it from Beelink without explicit authorization and an active-job check.

No SSH keys, API keys, OAuth material, hostnames, private IPs, or production content are included in this handoff. The user explicitly said the Beelink has no direct SSH access. GitHub is the transfer mechanism.

## Pause boundary

This Mac-side task is intentionally paused after pushing all branches and this handoff. Do not assume a local process or subagent is still running. Continue from the fork and record any new branch/PR heads back into #97681.
