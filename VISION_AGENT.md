# Agent and Harness Vision

| Field | Value |
| --- | --- |
| Status | Active contract |
| Owner | Martin Hausleitner |
| Applies to | ACP/ACPX, Browser Use, Orca, Codex, Claude, Cursor, Grok, OpenCode, future adapters |
| Related docs | [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), [TESTING.md](TESTING.md) |

## Agent Goal

Agents should be useful operators inside an owned browser-control plane. They can inspect, plan, request approval, run bounded browser actions, emit typed outputs, and produce evidence. They do not become the control plane, bypass the Manager, or inherit ambient human credentials.

## Harness-Neutral Contract

Every harness must enter through the same principles:

- one Manager-created task run;
- one explicit human or agent identity;
- one profile lease at a time;
- scoped capabilities for allowed resources and origins;
- typed outputs instead of unstructured transcript-only claims;
- cancellation, timeout, heartbeat, and terminal-state reporting;
- redaction before output reaches API, logs, UI, MCP, CLI, screenshots, or evidence.

## Adapter Expectations

| Adapter class | Allowed role | Not allowed |
| --- | --- | --- |
| ACP/ACPX | Session adapter for approved coding agents | Product state, backup, policy, raw secret transport |
| Browser Use | Browser worker behind Manager lease | Direct account vault, global CDP, unscoped screenshots |
| Orca | Host bridge where present | Independent browser owner or second task database |
| Codex, Claude, Cursor, Grok, OpenCode | Agent providers selected per run | Ambient administrator, raw shell to production, hidden persistent authority |
| Workflow engine | Durable orchestration layer after benchmark gate | Replacement for Manager projects, RBAC, audit, approvals, or release state |

## Agent Output Shape

Agents should report work as typed, bounded events:

- `action`: intended operation and target class;
- `observation`: what was seen, with redaction applied;
- `screenshot`: authenticated artifact reference, not inline secret-bearing image data;
- `approval`: requested human decision and scope;
- `extracted_data`: bounded structured data;
- `metric`: timing, retries, resource use, or health;
- `summary`: terminal result with evidence references;
- `error`: stable code, redacted message, and recovery state.

## Human Control

Humans must be able to:

- see which agent owns a run;
- see which profile, project, origin, and capability are leased;
- approve or deny risky actions;
- cancel and observe cleanup;
- inspect evidence after reload;
- distinguish live execution from metadata-only UI.

## Known Gaps

- ACPX is pinned and locally contract-tested, but live managed-browser VCVM E2E and adapter-specific provider auth are still pending.
- Future adapters need descriptors, executable allowlists, version pins, contract tests, security review, and UI availability semantics before exposure.
- Typed-output schemas and evidence retention must remain versioned as adapters broaden.

## Inspirations

Inspired structurally by [Block Buzz VISION_AGENT.md](https://github.com/block/buzz/blob/main/VISION_AGENT.md), without adopting Buzz-specific room, relay, or Nostr identity mechanics.
