# Authentication and Passkey Benchmark

This benchmark exercises authentication failure and recovery paths in isolated
Chromium contexts on the VCVM. It is intentionally local-only: the relying
party and the Google-style OAuth identity provider are synthetic loopback
fixtures. The suite never opens Google, reads a real account, or imports a real
password, OTP, cookie, passkey, or browser profile.

## What is covered

The live matrix runs in parallel with a fresh browser context and fixture
server for every scenario:

- password success and failure;
- OTP handoff and expiry;
- OAuth popup success, cancel, close, and bounded close/reopen recovery;
- virtual passkey registration and assertion;
- two aborted passkey prompts followed by a successful recovery;
- required user-verification failure;
- missing authenticator and revoked synthetic credential failures;
- conditional passkey mediation;
- Google-style OAuth followed by passkey 2FA;
- Google-style OAuth popup reopen followed by passkey 2FA.

The report gate fails on a wrong outcome, more than three passkey prompts, more
than one popup reopen, duplicate parallel profile identifiers, sensitive output,
or a p95 runtime above the configured budget.

## Run on the VCVM

```bash
cd /home/coder/orca/workspaces/CloakBrowser-Manager-browser-use/cbm-auth-passkey-benchmark
PYTHONPATH=.:benchmarks/auth python3 -m pytest benchmarks/auth -q
PYTHONPATH=. python3 benchmarks/auth/run_auth_benchmark.py \
  --parallel 4 \
  --output /tmp/cbm-auth-benchmark/report.json
```

Run a focused popup/passkey recovery subset:

```bash
PYTHONPATH=. python3 benchmarks/auth/run_auth_benchmark.py \
  --parallel 3 \
  --scenario oauth_popup_reopen \
  --scenario passkey_prompt_recovery \
  --scenario oauth_passkey_2fa_popup_reopen \
  --output /tmp/cbm-auth-benchmark/recovery.json
```

The output file is created with mode `0600`. It contains scenario identifiers,
outcomes, duration, bounded prompt/popup counters, and an opaque per-context
isolation identifier. It does not contain request/response bodies, credentials,
cookies, WebAuthn credential material, screenshots, HAR, video, traces, or
storage state.

## ACPX and Orca workflow

The companion ACPX pipeline models this release path:

```text
Plan -> Forge -> [Password | OAuth | WebAuthn] -> IDR/Security -> Tribunal -> Report
```

Agent stages route through ACPX with Grok Build. Browser measurements remain
deterministic local subprocesses, so CI can verify them without an LLM account.
The pipeline defaults to plan/dry-run mode; execution must be explicit and is
bounded to four parallel lanes.

Verify the pinned ACPX runtime and render the safe plan:

```bash
python3 scripts/acpx_runtime_lock.py --repo .
npm --prefix deploy/acpx-runtime ci --ignore-scripts
PYTHONPATH=. python3 benchmarks/auth/run_acpx_pipeline.py \
  --mode plan \
  --parallel 4 \
  --output /tmp/cbm-auth-acpx-plan/report.json
```

The verified graph has eight stages and uses the route `acpx:grok-build` for
every agent stage. Its three measurement lanes partition all 16 live scenarios
exactly once and write to distinct private files. `execute` mode additionally
requires absolute, validated permission-policy and MCP-config paths and fails
closed if the repo-pinned ACPX 0.12.1 executable is unavailable.

## Verified VCVM result

On 2026-07-30 the exact CI test command completed with `127 passed`. The full
16-scenario browser run completed with `passed=true`, no outcome failures, and
a measured p95 of 4.051 seconds under four-way parallelism. The independent
final Tribunal found no remaining P0, P1, or P2 issue.

## Security boundary

- Allowed hosts: exact `localhost`, `127.0.0.1`, or `::1` loopback origins only.
- External navigation is aborted by a browser-context route guard.
- Real Google hosts and unknown scenario values fail closed.
- CDP WebAuthn supports only a narrow synthetic virtual-authenticator API.
- Credential enumeration and password-manager extraction methods are denied.
- Each scenario uses a fresh browser context; no storage state is persisted.

Virtual authenticators prove the browser and benchmark control flow, not the
UX of a hardware key, Secure Enclave, Android/iOS device prompt, or a real
Google account. Those paths require a separate operator-controlled device lane
and must never be labelled as deterministic CI evidence.

References: [Chrome DevTools Protocol WebAuthn](https://chromedevtools.github.io/devtools-protocol/tot/WebAuthn/),
[Google Account passkeys](https://support.google.com/accounts/answer/13548313),
and [Google Identity Services with FedCM](https://developers.google.com/identity/sign-in/web/gsi-with-fedcm).
