from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright

from benchmarks.auth.cdp_webauthn import (
    CdpWebAuthn,
    VirtualAuthenticatorOptions,
    WebAuthnStateError,
)
from benchmarks.auth.fixture_server import (
    FIXTURE_OTP,
    FIXTURE_PASSWORD,
    FIXTURE_USERNAME,
    AuthFixtureServer,
)
from benchmarks.auth.policy import assert_artifact_is_safe, validate_benchmark_origins
from benchmarks.auth.report import (
    AuthBenchmarkReport,
    AuthBenchmarkResult,
    build_report,
)
from benchmarks.auth.scenarios import expected_outcomes as scenario_expected_outcomes
from benchmarks.auth.scenarios import live_scenario_ids

# Canonical live matrix comes from the scenario catalog (not a parallel list).
DEFAULT_SCENARIOS = live_scenario_ids()
_KNOWN_SCENARIOS = frozenset(DEFAULT_SCENARIOS)


def catalog_expected_outcomes() -> dict[str, str]:
    """Live expected outcomes derived solely from the scenario catalog."""
    return scenario_expected_outcomes(live_only=True)


def default_chromium_binary() -> str | None:
    configured = os.environ.get("CBM_AUTH_CHROMIUM_BINARY", "").strip()
    candidates = (
        configured,
        "/home/coder/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
        "/home/coder/.cache/ms-playwright/chromium-1217/chrome-linux/chrome",
        "/usr/bin/google-chrome",
        "/snap/bin/chromium",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _is_allowed_request(url: str, allowed_origins: tuple[str, ...]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme in {"data", "blob", "about"}:
        return True
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin in allowed_origins


async def _install_route_guard(
    context: BrowserContext, allowed_origins: tuple[str, ...]
) -> None:
    async def guard(route: Route) -> None:
        if _is_allowed_request(route.request.url, allowed_origins):
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    await context.route("**/*", guard)


async def _fetch_json(page: Page, path: str, body: dict[str, str] | None = None) -> dict[str, Any]:
    return await page.evaluate(
        """async ({path, body}) => {
          const options = {method: body ? 'POST' : 'GET', headers: {}};
          if (body) {
            options.headers['Content-Type'] = 'application/x-www-form-urlencoded';
            options.body = new URLSearchParams(body).toString();
          }
          const response = await fetch(path, options);
          return {status: response.status, payload: await response.json()};
        }""",
        {"path": path, "body": body},
    )


async def _password_or_otp(page: Page, scenario_id: str) -> str:
    if scenario_id == "password_success":
        result = await _fetch_json(
            page,
            "/login",
            {"username": FIXTURE_USERNAME, "password": FIXTURE_PASSWORD},
        )
        return str(result["payload"]["outcome"])
    if scenario_id == "password_failure":
        result = await _fetch_json(
            page,
            "/login",
            {"username": FIXTURE_USERNAME, "password": "synthetic-wrong"},
        )
        return str(result["payload"]["outcome"])

    start = await _fetch_json(page, "/otp/start", {})
    challenge_id = str(start["payload"]["challenge_id"])
    if scenario_id == "otp_expiry":
        await _fetch_json(page, "/otp/expire", {"challenge_id": challenge_id})
    result = await _fetch_json(
        page,
        "/otp/verify",
        {"challenge_id": challenge_id, "otp": FIXTURE_OTP},
    )
    return str(result["payload"]["outcome"])


async def _open_oauth_popup(page: Page, url: str) -> Page:
    async with page.expect_popup() as pending:
        await page.evaluate("url => window.open(url, '_blank', 'width=520,height=640')", url)
    popup = await pending.value
    await popup.wait_for_load_state("domcontentloaded")
    return popup


async def _read_rp_callback_outcome(popup: Page, *, rp_base: str) -> str:
    """Verify OAuth completed at the local RP callback (not merely an IdP click)."""
    # Glob match: different query strings after /oauth/callback.
    await popup.wait_for_url(f"{rp_base}/oauth/callback**", timeout=8_000)
    await popup.wait_for_load_state("domcontentloaded")
    # RP callback serves JSON: {"outcome":"...","via":"callback"}
    raw = await popup.evaluate(
        """() => {
          const text = document.body ? document.body.innerText : '';
          try { return JSON.parse(text); } catch (e) { return {outcome: 'error', raw: text}; }
        }"""
    )
    if not isinstance(raw, dict):
        return "error"
    if str(raw.get("via") or "") != "callback":
        return "error"
    return str(raw.get("outcome") or "error")


async def _oauth(page: Page, server: AuthFixtureServer, scenario_id: str) -> tuple[str, int, int]:
    state = "state-" + secrets.token_hex(4)
    callback = f"{server.rp_base}/oauth/callback"
    auth_url = (
        f"{server.idp_base}/o/oauth2/auth?client_id=fixture"
        f"&redirect_uri={callback}&state={state}"
    )
    popup = await _open_oauth_popup(page, auth_url)

    if scenario_id == "oauth_popup_close":
        await popup.close()
        return "close", 1, 0
    if scenario_id == "oauth_popup_reopen":
        await popup.close()
        popup = await _open_oauth_popup(page, auth_url)
        await popup.get_by_role("button", name="Approve").click()
        outcome = await _read_rp_callback_outcome(popup, rp_base=server.rp_base)
        await popup.close()
        return outcome, 2, 1
    if scenario_id == "oauth_popup_cancel":
        await popup.get_by_role("button", name="Cancel").click()
        await popup.wait_for_load_state("domcontentloaded")
        await popup.close()
        return "cancel", 1, 0

    # oauth_popup_success (and 2FA first-hop): require RP callback after IdP approval.
    await popup.get_by_role("button", name="Approve").click()
    outcome = await _read_rp_callback_outcome(popup, rp_base=server.rp_base)
    await popup.close()
    return outcome, 1, 0


async def _create_passkey(page: Page) -> None:
    outcome = await page.evaluate(
        """async () => {
          const publicKey = {
            challenge: crypto.getRandomValues(new Uint8Array(32)),
            rp: {name: 'CloakBrowser Synthetic RP', id: location.hostname},
            user: {
              id: crypto.getRandomValues(new Uint8Array(16)),
              name: 'fixture-user',
              displayName: 'Fixture User'
            },
            pubKeyCredParams: [{type: 'public-key', alg: -7}],
            authenticatorSelection: {
              authenticatorAttachment: 'platform',
              residentKey: 'required',
              userVerification: 'required'
            },
            timeout: 3000,
            attestation: 'none'
          };
          try {
            const credential = await navigator.credentials.create({publicKey});
            window.__cbmCredentialId = Array.from(new Uint8Array(credential.rawId));
            return 'success';
          } catch (error) {
            return error && error.name ? error.name : 'error';
          }
        }"""
    )
    if outcome != "success":
        raise RuntimeError("synthetic passkey registration failed")


async def _assert_passkey(page: Page, *, abort_after_ms: int | None = None) -> str:
    return str(
        await page.evaluate(
            """async ({abortAfterMs}) => {
              const controller = new AbortController();
              let timer = null;
              if (abortAfterMs !== null) {
                timer = setTimeout(() => controller.abort(), abortAfterMs);
              }
              const publicKey = {
                challenge: crypto.getRandomValues(new Uint8Array(32)),
                rpId: location.hostname,
                allowCredentials: [{
                  type: 'public-key',
                  id: new Uint8Array(window.__cbmCredentialId || [])
                }],
                userVerification: 'required',
                timeout: 1500
              };
              try {
                const assertion = await navigator.credentials.get({
                  publicKey,
                  signal: controller.signal
                });
                return assertion ? 'success' : 'error';
              } catch (error) {
                return error && error.name ? error.name : 'error';
              } finally {
                if (timer !== null) clearTimeout(timer);
              }
            }""",
            {"abortAfterMs": abort_after_ms},
        )
    )


async def _assert_conditional_passkey(page: Page) -> str:
    return str(
        await page.evaluate(
            """async () => {
              const publicKey = {
                challenge: crypto.getRandomValues(new Uint8Array(32)),
                rpId: location.hostname,
                userVerification: 'required',
                timeout: 1500
              };
              try {
                const assertion = await navigator.credentials.get({
                  publicKey,
                  mediation: 'conditional'
                });
                return assertion ? 'success' : 'error';
              } catch (error) {
                return error && error.name ? error.name : 'error';
              }
            }"""
        )
    )


async def _passkey(
    page: Page,
    cdp_session: Any,
    scenario_id: str,
) -> tuple[str, int]:
    async def send(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await cdp_session.send(method, params or {})

    webauthn = CdpWebAuthn(send)
    await webauthn.enable(enable_ui=False)
    authenticator_id = await webauthn.add_virtual_authenticator(
        VirtualAuthenticatorOptions(
            protocol="ctap2",
            transport="internal",
            has_resident_key=True,
            has_user_verification=True,
            automatic_presence=True,
            is_user_verified=True,
        )
    )
    prompt_count = 0
    try:
        await _create_passkey(page)
        if scenario_id == "passkey_register_assert":
            prompt_count = 1
            return await _assert_passkey(page), prompt_count
        if scenario_id == "passkey_conditional_mediation":
            prompt_count = 1
            return await _assert_conditional_passkey(page), prompt_count
        if scenario_id == "passkey_prompt_recovery":
            await webauthn.set_automatic_presence(authenticator_id, False)
            for _ in range(2):
                prompt_count += 1
                aborted = await _assert_passkey(page, abort_after_ms=120)
                if aborted not in {"AbortError", "NotAllowedError"}:
                    raise RuntimeError("synthetic passkey prompt did not abort cleanly")
            await webauthn.set_automatic_presence(authenticator_id, True)
            prompt_count += 1
            return await _assert_passkey(page), prompt_count
        if scenario_id == "passkey_uv_required":
            await webauthn.set_user_verified(authenticator_id, False)
            try:
                webauthn.assert_ready_for_assertion(
                    authenticator_id, user_verification="required"
                )
            except WebAuthnStateError as exc:
                return exc.code, 1
            raise RuntimeError("user-verification policy unexpectedly passed")
        if scenario_id == "passkey_no_authenticator":
            await webauthn.remove_virtual_authenticator(authenticator_id)
            try:
                webauthn.assert_ready_for_assertion(authenticator_id)
            except WebAuthnStateError as exc:
                return exc.code, 1
            raise RuntimeError("missing-authenticator policy unexpectedly passed")
        if scenario_id == "passkey_revoked_credential":
            ref = await webauthn.seed_synthetic_credential(
                authenticator_id, rp_id="localhost"
            )
            await webauthn.revoke_synthetic_credential(authenticator_id, ref.credential_id)
            try:
                webauthn.assert_ready_for_assertion(
                    authenticator_id, credential_id=ref.credential_id
                )
            except WebAuthnStateError as exc:
                return exc.code, 1
            raise RuntimeError("revoked-credential policy unexpectedly passed")
        raise ValueError("unknown passkey scenario")
    finally:
        await webauthn.cleanup()


async def _run_one(browser: Browser, scenario_id: str) -> AuthBenchmarkResult:
    # WebAuthn rejects IP literals as RP IDs even though loopback HTTP is a
    # secure context. ``localhost`` keeps the fixture local while providing a
    # valid RP domain for registration and assertion.
    server = AuthFixtureServer(host="localhost", rp_port=0, idp_port=0)
    server.start()
    origins = validate_benchmark_origins([server.rp_base, server.idp_base])
    context = await browser.new_context()
    await _install_route_guard(context, origins)
    page = await context.new_page()
    profile_isolation_id = hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:16]
    started = time.perf_counter()
    outcome = "error"
    prompt_count = 0
    popup_count = 0
    popup_reopen_count = 0
    sensitive_hits: tuple[str, ...] = ()
    try:
        await page.goto(server.rp_base + "/", wait_until="domcontentloaded")
        if scenario_id.startswith(("password_", "otp_")):
            outcome = await _password_or_otp(page, scenario_id)
        elif scenario_id.startswith("oauth_popup_"):
            outcome, popup_count, popup_reopen_count = await _oauth(
                page, server, scenario_id
            )
        elif scenario_id.startswith("oauth_passkey_2fa_"):
            oauth_scenario = (
                "oauth_popup_reopen"
                if scenario_id.endswith("popup_reopen")
                else "oauth_popup_success"
            )
            oauth_outcome, popup_count, popup_reopen_count = await _oauth(
                page, server, oauth_scenario
            )
            if oauth_outcome != "success":
                outcome = oauth_outcome
            else:
                cdp_session = await context.new_cdp_session(page)
                outcome, prompt_count = await _passkey(
                    page, cdp_session, "passkey_register_assert"
                )
        elif scenario_id.startswith("passkey_"):
            cdp_session = await context.new_cdp_session(page)
            outcome, prompt_count = await _passkey(page, cdp_session, scenario_id)
        else:
            raise ValueError("unknown auth benchmark scenario")

        logs = "\n".join(server.drain_logs())
        try:
            assert_artifact_is_safe(logs)
        except ValueError:
            sensitive_hits = ("forbidden-auth-material",)
    finally:
        await context.close()
        server.stop()

    return AuthBenchmarkResult(
        scenario_id=scenario_id,
        outcome=outcome,
        duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
        prompt_count=prompt_count,
        popup_count=popup_count,
        popup_reopen_count=popup_reopen_count,
        sensitive_artifact_hits=sensitive_hits,
        profile_isolation_id=profile_isolation_id,
    )


async def run_live_benchmark(
    *,
    chromium_binary: str,
    scenario_ids: tuple[str, ...] = DEFAULT_SCENARIOS,
    max_parallel: int = 2,
    p95_budget_ms: int = 8_000,
) -> AuthBenchmarkReport:
    unknown = [scenario_id for scenario_id in scenario_ids if scenario_id not in _KNOWN_SCENARIOS]
    if unknown:
        raise ValueError(f"unknown auth benchmark scenario: {unknown[0]}")
    if not 1 <= max_parallel <= 4:
        raise ValueError("max_parallel must be between 1 and 4")
    binary = Path(chromium_binary)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("chromium binary is unavailable")

    semaphore = asyncio.Semaphore(max_parallel)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=str(binary),
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
            ],
        )
        try:
            async def bounded(scenario_id: str) -> AuthBenchmarkResult:
                async with semaphore:
                    return await _run_one(browser, scenario_id)

            results = await asyncio.gather(*(bounded(item) for item in scenario_ids))
        finally:
            await browser.close()

    return build_report(
        list(results),
        max_prompt_count=3,
        p95_budget_ms=p95_budget_ms,
        require_unique_profiles=True,
        expected_outcomes=catalog_expected_outcomes(),
    )


def write_report(path: Path, report: AuthBenchmarkReport) -> None:
    payload = {
        "schema": "cloakbrowser.auth-benchmark.v1",
        "passed": report.passed,
        "p95_duration_ms": report.p95_duration_ms,
        "failures": list(report.failures),
        "results": [
            {
                "scenario_id": item.scenario_id,
                "outcome": item.outcome,
                "duration_ms": item.duration_ms,
                "prompt_count": item.prompt_count,
                "popup_count": item.popup_count,
                "popup_reopen_count": item.popup_reopen_count,
                "profile_isolation_id": item.profile_isolation_id,
                "sensitive_artifact_hit_count": len(item.sensitive_artifact_hits),
            }
            for item in report.results
        ],
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    assert_artifact_is_safe(text)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)
    path.chmod(0o600)
