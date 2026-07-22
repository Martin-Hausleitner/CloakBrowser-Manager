# Profile Health, Proxy And BrowserScan Design

Status: approved objective translated into an implementation contract on 22 July 2026.

## Purpose

Give each browser profile a small, honest, read-only health summary after its first successful launch. The summary must answer three separate questions without leaking secrets or pretending that one signal proves another:

1. Did the launched browser reach the public network through its configured runtime path?
2. Are the browser's observable runtime properties consistent with the saved profile configuration?
3. Did an external public diagnostic surface expose a safe, machine-readable authenticity result, or was that measurement unavailable?

This is a product-health feature, not a promise of undetectability. It must never label a profile “safe”, “anonymous”, “human”, or “undetectable”. It must preserve the existing authorization boundary and keep the compact mobile workspace free of diagnostic controls.

## Product behavior

### Automatic first-run probe

- Schedule one probe after the first successful browser launch for a profile that has no stored health result.
- Do not make browser launch wait for the probe.
- Do not start duplicate probes for the same profile.
- A failed or unavailable probe is still a stored result and may be rerun manually by an authorized operator.
- Later launches do not rerun automatically merely because the prior result was warning, failed, or unavailable.

### Manual rerun

- A profile viewer may read the latest health summary.
- An operator or administrator may request a rerun for a running profile.
- A rerun for a stopped profile returns a conflict response rather than launching it implicitly.
- Missing and unauthorized profiles remain indistinguishable through the existing scoped-profile helper.

### Human presentation

- Profile lists may show one compact status dot or short label.
- Profile details may show the latest component results, timestamp, safe warning categories, and source availability.
- The mobile live-browser workspace must not gain a persistent benchmark, scan, or rerun button.
- Raw IP addresses, proxy URLs, credentials, page text, launch arguments, browser storage, cookies, and host paths are never rendered.

## Data model

Create an additive `profile_health` SQLite table with one current row per profile.

| Column | Type | Meaning |
| --- | --- | --- |
| `profile_id` | text primary key | Existing profile identifier; cascade cleanup is performed by repository code. |
| `state` | text | `pending`, `running`, `passed`, `warning`, `failed`, or `unavailable`. |
| `checked_at` | text nullable | UTC ISO timestamp of the completed attempt. |
| `proxy_configured` | integer | Whether the profile has a proxy configured; no proxy value is stored. |
| `proxy_reachable` | integer nullable | Browser-path or trusted proxychecker reachability result. |
| `outbound_ip_masked` | text nullable | Coarse masked display value only, such as `203.0.113.x` or `2001:db8:abcd:…`. |
| `proxy_latency_ms` | real nullable | Measured end-to-end request latency when available. |
| `proxy_risk_score` | integer nullable | Normalized trusted-proxychecker risk score, 0-100. |
| `proxy_authenticity_score` | integer nullable | Explicitly derived as `100 - risk_score`; label the derivation in the API. |
| `fingerprint_consistency_score` | integer nullable | Deterministic config-versus-runtime consistency score, 0-100. |
| `browser_scan_score` | integer nullable | Visible external authenticity percentage when safely classified. |
| `warnings_json` | text | JSON array of whitelisted warning category strings. |
| `blockers_json` | text | JSON array of whitelisted availability/blocker category strings. |
| `error_code` | text nullable | Whitelisted stable error code, never a raw exception message. |
| `sources_json` | text | JSON object describing each component as measured, unavailable, skipped, or derived. |

The table stores only the latest result for the MVP. Historical observations belong to a later metrics feature and must not be inferred from this row.

## API contract

### Read latest health

`GET /api/profiles/{profile_id}/health`

- Required capability: `view`.
- Returns a stable response even before any probe: `state: "unavailable"`, `checked_at: null`, and all unmeasured components `null`.
- Uses explicit source states so the UI can distinguish missing, skipped, blocked, derived, and measured values.

### Rerun health

`POST /api/profiles/{profile_id}/health/run`

- Required capability: `operate`.
- Requires the profile to be running.
- Returns HTTP 202 with `state: "pending"` or `state: "running"`.
- Rejects a duplicate in-flight probe with the existing in-flight state rather than creating another task.

### Redaction rules

The response may contain:

- masked outbound IP;
- integer scores;
- safe latency values;
- whitelisted categories;
- stable timestamps and source states.

The response must not contain:

- a raw proxy URL, username, password, complete outbound IP, browser page content, raw provider response, filesystem path, fingerprint seed, user-agent string, launch argument, cookie, token, or exception message.

## Probe orchestration

Create `backend/profile_health.py` as a focused, dependency-injected service. It receives the stored profile configuration, the active `RunningProfile`, and optional adapters. It does not import the FastAPI app.

### State machine

```text
no row -> pending -> running -> passed | warning | failed | unavailable
                \-> existing in-flight result is reused
```

- `passed`: every requested and available component measured without a warning.
- `warning`: at least one measured component reported a consistency or risk warning, while the probe completed.
- `failed`: the browser runtime or required internal measurement failed after starting.
- `unavailable`: no meaningful component could be measured because an external source or prerequisite was unavailable.
- Component availability is more important than the aggregate label; the UI must show it.

### Browser-path network observation

- Open a temporary page inside the already-running Playwright browser context.
- Request a fixed HTTPS IP-echo endpoint through the actual browser runtime.
- Measure elapsed monotonic time around the navigation/request.
- Parse only the IP field, mask it immediately, discard the raw value, and close the temporary page.
- Network failure produces a stable `network_unreachable` or `network_timeout` code; raw exception text is not persisted.

### Fingerprint consistency observation

Evaluate a small, deterministic set of saved-config versus runtime properties:

- platform family;
- configured screen width and height;
- timezone;
- primary locale/language;
- hardware concurrency when configured;
- user-agent family only, not the full raw string.

Each available signal contributes equal weight. Missing runtime signals are blockers, not silent matches. The score is named `fingerprint_consistency_score`; it is not a detection-resistance score.

### BrowserScan observation

- Open BrowserScan in a separate temporary page within the active profile context.
- Never click consent prompts, CAPTCHAs, challenges, login elements, or anti-bot interstitials.
- Classify the visible page conservatively from a bounded text snapshot in memory.
- Extract only an explicit visible `authenticity ... N%` value and whitelisted category matches.
- Persist no DOM, screenshot, raw text, IP, identifier, or provider payload.
- If a challenge, consent wall, unsupported markup, timeout, or missing score prevents classification, record an explicit blocker and leave the score `null`.
- A missing BrowserScan score must never be replaced by the internal consistency score.

## Existing VCVM proxychecker adapter

The proxychecker service is optional enrichment. The Manager must remain useful when it is unavailable.

### Configuration

- Read `PROXYCHECKER_URL` from the environment.
- Empty means disabled.
- Accept only loopback, `host.docker.internal`, or an explicitly allow-listed private hostname supplied through deployment configuration.
- Use short connect/read timeouts and no retries in the foreground adapter.
- Never log the request body because it can contain a proxy credential.

### Request and normalization

- Send the configured proxy only to the trusted VCVM-local service.
- Use a fixed public HTTPS target.
- Consume only normalized reachability, latency, risk score, final verdict, and safe reason categories.
- Clamp numeric scores to 0-100.
- Derive `proxy_authenticity_score = 100 - proxy_risk_score` and mark its source as `derived`.
- Discard raw IPs, proxy strings, provider payloads, errors, routes, headers, and unrecognized reason text.

### Failure behavior

- Disabled service: component source `skipped`.
- Connection refused or timeout: source `unavailable`, blocker `proxychecker_unavailable`.
- Malformed response: source `unavailable`, blocker `proxychecker_invalid_response`.
- Browser-path reachability can still be measured independently.

## Security and access boundaries

- Reuse the existing profile capability helpers in `backend/main.py`.
- Do not expose health-run control to viewers.
- Do not weaken the `codex-computer-use` execution boundary; this server probe is a narrowly typed internal health operation, not a generic agent command.
- Do not accept a target URL from the client.
- Do not follow arbitrary provider links or render provider-returned HTML.
- Treat all external text as untrusted and reduce it to whitelisted categories.
- Keep all persisted strings length-bounded.
- Never include health details in public `/health`; that endpoint stays a minimal service liveness check.

## Performance constraints

- Launch must return before the automatic probe finishes.
- At most one probe runs per profile.
- Temporary diagnostic pages are closed in `finally` blocks.
- Default component timeouts are short and individually bounded.
- External calls are not retried in a tight loop.
- Health reads are one SQLite lookup and must not contact external services.

## Testing strategy

### Pure unit tests

- IPv4 and IPv6 masking.
- Score clamping and derivation.
- BrowserScan score extraction, challenge/consent fail-closed handling, and warning-category whitelisting.
- Fingerprint consistency scoring with matches, mismatches, and missing signals.
- Proxychecker URL trust validation and response normalization.

### Persistence tests

- Additive table creation on existing databases.
- Default unavailable DTO when no row exists.
- Upsert replaces the current result without duplicating rows.
- Deleting a profile removes its health row.
- JSON fields tolerate only normalized lists/objects.

### API and authorization tests

- Viewers may read.
- Viewers may not run.
- Operators and administrators may run a live profile.
- Stopped profile returns conflict.
- Unauthorized and missing profiles both return 404.
- No response contains raw proxy, complete IP, raw error, user-agent, fingerprint seed, or launch arguments.

### Launch integration tests

- First successful launch schedules exactly one probe.
- A stored result suppresses automatic rerun.
- A probe failure does not fail browser launch.
- Duplicate manual requests reuse the in-flight task.

### Frontend tests

- Compact status renders on profile surfaces.
- Details distinguish measured, derived, unavailable, and skipped values.
- Rerun is absent for viewers and available to operators/admins in the appropriate detail surface.
- Mobile live view receives no persistent scan or benchmark control.
- Raw proxy/IP/error data cannot be found in rendered output.

### VCVM acceptance

- Start or verify the existing proxychecker on VCVM loopback without exposing it publicly.
- Deploy Manager on VCVM with an explicit trusted service URL.
- Launch one no-proxy profile and one authorized proxy profile when safe credentials are already configured.
- Verify asynchronous launch, stored health, masked IP, component availability, manual rerun, and refresh persistence.
- Capture browser screenshots containing no credentials or full IP addresses.
- Run the complete backend, frontend, build, script, security, deployment, and authenticated mobile gates before promoting the status to proven.

## Explicit non-goals for this slice

- No claim that BrowserScan or any score guarantees stealth.
- No CAPTCHA, WAF, consent, login, or Terms-of-Service bypass.
- No proxy purchase, discovery, rotation, or credential editing UI.
- No historical time-series database.
- No mobile benchmark dashboard.
- No extension installation or generic harness execution; those remain separate P0 slices.

## Completion definition

The slice is complete only when the implementation, tests, API redaction, access enforcement, asynchronous first-launch behavior, VCVM deployment, browser screenshots, README, continuation skill, acceptance matrix, fork commit, push, and remote SHA verification all agree. Until VCVM evidence exists, documentation must say “implemented; scoped verification passed”, not “proven”.
