"""Focused mocked tests for scripts/cbm_browser_ctl.py."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().with_name("cbm_browser_ctl.py")


def _load():
    import sys

    spec = importlib.util.spec_from_file_location("cbm_browser_ctl", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # dataclasses require the module to be present in sys.modules during exec.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_auth_header_reads_key_file_when_env_key_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    mod = _load()
    key_file = tmp_path / "orca-agent-key"
    key_file.write_text("cbm_agent_from_file_not_real\n", encoding="utf-8")
    monkeypatch.delenv("CBM_AGENT_KEY", raising=False)
    monkeypatch.delenv("CBM_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.setenv("CBM_AGENT_KEY_FILE", str(key_file))
    assert mod._auth_header() == {"Authorization": "Bearer cbm_agent_from_file_not_real"}


def test_validate_rejects_non_http_and_long_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load()
    monkeypatch.setenv("CBM_BROWSER_ARTIFACT_ROOT", str(tmp_path))
    with pytest.raises(mod.BrowserCtlError) as url_exc:
        mod.validate_http_url("javascript:alert(1)")
    assert url_exc.value.code == "invalid_url"
    with pytest.raises(mod.BrowserCtlError) as cred_exc:
        mod.validate_http_url("https://user:pass@example.com/")
    assert cred_exc.value.code == "invalid_url"
    with pytest.raises(mod.BrowserCtlError) as sel_exc:
        mod.validate_selector("x" * (mod.MAX_SELECTOR_CHARS + 1))
    assert sel_exc.value.code == "invalid_selector"
    with pytest.raises(mod.BrowserCtlError) as text_exc:
        mod.validate_text("y" * (mod.MAX_TEXT_CHARS + 1))
    assert text_exc.value.code == "invalid_text"
    with pytest.raises(mod.BrowserCtlError):
        mod.validate_screenshot_path("../escape.png")


@pytest.mark.parametrize(
    "bad_id",
    [
        "../escape",
        "a/b",
        "id?x=1",
        "id#frag",
        "has space",
        "has\nnewline",
        "id&x",
        "",
        "bad/../../../etc",
    ],
)
def test_validate_profile_id_rejects_unsafe_segments(bad_id: str):
    mod = _load()
    with pytest.raises(mod.BrowserCtlError) as exc:
        mod.validate_profile_id(bad_id)
    assert exc.value.code == "invalid_profile_id"


def test_validate_profile_id_accepts_uuid_and_quotes_path():
    mod = _load()
    pid = "550e8400-e29b-41d4-a716-446655440000"
    assert mod.validate_profile_id(pid) == pid
    path = mod.profile_api_path(pid, "automation-leases")
    assert path == f"/api/profiles/{pid}/automation-leases"
    assert "?" not in path and "#" not in path


def test_redact_url_strips_userinfo_and_secret_query():
    mod = _load()
    cleaned = mod.redact_url(
        "https://user:secret@example.com/path?token=cbm_lease_abc&q=ok#frag"
    )
    assert "secret" not in cleaned
    assert "user:" not in cleaned
    assert "cbm_lease_abc" not in cleaned
    assert "q=ok" in cleaned
    assert "REDACTED" in cleaned
    assert "Bearer cbm_agent_LEAK" not in mod.redact_text(
        "Authorization: Bearer cbm_agent_LEAKEDTOKEN999"
    )


def test_lease_acquire_and_release_even_when_action_fails(monkeypatch: pytest.MonkeyPatch):
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        calls.append((method, path, headers))
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-1",
                "token": "cbm_lease_deadbeefcafebabe",
                "heartbeat_interval_seconds": 60,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            return {"expires_at": "2099-01-01T00:00:00+00:00", "heartbeat_interval_seconds": 60}
        if method == "DELETE":
            return {"ok": True, "status": 204}
        raise AssertionError(f"unexpected {method} {path}")

    class BoomPage:
        url = "about:blank"

        def bring_to_front(self):
            return None

        def title(self):
            raise RuntimeError("page exploded")

    class BoomBrowser:
        contexts = [SimpleNamespace(pages=[BoomPage()])]

        def close(self):
            calls.append(("CLOSE", "browser", None))

    def fake_connect(endpoint, *, headers):
        calls.append(("CONNECT", endpoint, headers))
        return BoomBrowser()

    monkeypatch.setattr(mod, "_request", fake_request)
    with pytest.raises(RuntimeError, match="page exploded"):
        with mod.leased_page("profile-1", connect_over_cdp=fake_connect) as session:
            session.page.title()

    methods = [item[0] for item in calls]
    assert methods[0] == "POST"
    assert methods[1] == "CONNECT"
    assert methods[-2] == "CLOSE"
    assert methods[-1] == "DELETE"
    connect_headers = calls[1][2]
    assert connect_headers["Authorization"] == "Bearer cbm_agent_test_key_not_real"
    assert connect_headers["X-CBM-Automation-Lease"] == "cbm_lease_deadbeefcafebabe"


def test_heartbeat_runs_during_long_action(monkeypatch: pytest.MonkeyPatch):
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    heartbeats: list[str] = []

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-hb",
                "token": "cbm_lease_heartbeat_token1",
                "heartbeat_interval_seconds": 0.05,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            heartbeats.append(path)
            return {"expires_at": "2099-01-01T00:00:00+00:00", "heartbeat_interval_seconds": 0.05}
        if method == "DELETE":
            return {"ok": True, "status": 204}
        raise AssertionError(path)

    class SlowPage:
        url = "https://example.com/"

        def bring_to_front(self):
            return None

        def title(self):
            time.sleep(0.22)
            return "slow"

    class FakeBrowser:
        contexts = [SimpleNamespace(pages=[SlowPage()])]

        def close(self):
            return None

    monkeypatch.setattr(mod, "_request", fake_request)
    with mod.leased_page("profile-hb", connect_over_cdp=lambda *a, **k: FakeBrowser()) as session:
        assert session.page.title() == "slow"
    assert len(heartbeats) >= 2
    assert all("/heartbeat" in path for path in heartbeats)
    assert "cbm_lease_heartbeat_token1" not in json.dumps(heartbeats)


def test_heartbeat_failure_aborts_safely(monkeypatch: pytest.MonkeyPatch):
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    released: list[str] = []

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-fail",
                "token": "cbm_lease_fail_token_xxxx",
                "heartbeat_interval_seconds": 0.05,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            raise mod.BrowserCtlError("http_error", "HTTP 404 POST /heartbeat")
        if method == "DELETE":
            released.append(path)
            return {"ok": True, "status": 204}
        raise AssertionError(path)

    class SlowPage:
        url = "https://example.com/"

        def bring_to_front(self):
            return None

        def title(self):
            time.sleep(0.18)
            return "late"

    class FakeBrowser:
        contexts = [SimpleNamespace(pages=[SlowPage()])]
        closed = False

        def close(self):
            self.closed = True

    browser = FakeBrowser()
    monkeypatch.setattr(mod, "_request", fake_request)
    with pytest.raises(mod.BrowserCtlError) as exc:
        with mod.leased_page("profile-fail", connect_over_cdp=lambda *a, **k: browser) as session:
            session.page.title()
    assert exc.value.code == "lease_heartbeat_failed"
    assert "cbm_lease_" not in exc.value.message
    assert browser.closed is True
    assert released  # still released after abort


def test_release_failure_preserves_action_result_and_is_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys
):
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-rel",
                "token": "cbm_lease_release_tokenxx",
                "heartbeat_interval_seconds": 60,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            return {"expires_at": "2099-01-01T00:00:00+00:00", "heartbeat_interval_seconds": 60}
        if method == "DELETE":
            raise mod.BrowserCtlError("http_error", "HTTP 500 DELETE /automation-leases/lease-rel")
        raise AssertionError(path)

    class Page:
        url = "https://example.com/ok"
        def bring_to_front(self):
            return None
        def title(self):
            return "OK"

    class FakeBrowser:
        contexts = [SimpleNamespace(pages=[Page()])]
        def close(self):
            return None

    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(["inspect", "--profile-id", "profile-rel"])
    with pytest.raises(mod.BrowserCtlLifecycleIssue) as exc:
        mod.cmd_inspect(args, connect_over_cdp=lambda *a, **k: FakeBrowser())
    assert exc.value.code == "lease_release_failed"
    assert exc.value.result["command"] == "inspect"
    assert exc.value.result["url"] == "https://example.com/ok"
    assert exc.value.result["warnings"]
    assert "cbm_lease_" not in json.dumps(exc.value.result)

    # main() must not silently exit 0
    monkeypatch.setattr(
        mod,
        "cmd_inspect",
        lambda args, **kwargs: (_ for _ in ()).throw(
            mod.BrowserCtlLifecycleIssue(
                "lease_release_failed",
                "Lease release failed",
                result={
                    "ok": True,
                    "command": "inspect",
                    "profile_id": "profile-rel",
                    "url": "https://example.com/ok",
                    "title": "OK",
                    "warnings": [{"code": "lease_release_failed", "message": "Lease release failed"}],
                },
            )
        ),
    )
    code = mod.main(["inspect", "--profile-id", "profile-rel"])
    assert code != 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["url"] == "https://example.com/ok"
    assert payload["warnings"]


def test_page_targeting_defaults_to_last_and_supports_index_and_url(monkeypatch: pytest.MonkeyPatch):
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")

    class Page:
        def __init__(self, url: str):
            self.url = url
            self.front = False

        def bring_to_front(self):
            self.front = True

        def title(self):
            return self.url

    pages = [
        Page("https://example.com/one"),
        Page("https://example.com/two"),
        Page("https://example.com/three"),
    ]

    class FakeBrowser:
        contexts = [SimpleNamespace(pages=pages)]

        def close(self):
            return None

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-pages",
                "token": "cbm_lease_pages_tokenxxxx",
                "heartbeat_interval_seconds": 60,
            }
        if method == "DELETE":
            return {"ok": True, "status": 204}
        if method == "POST" and path.endswith("/heartbeat"):
            return {"expires_at": "2099-01-01T00:00:00+00:00", "heartbeat_interval_seconds": 60}
        raise AssertionError(path)

    monkeypatch.setattr(mod, "_request", fake_request)
    def connect(*a, **k):
        return FakeBrowser()

    with mod.leased_page("profile-pages", connect_over_cdp=connect) as session:
        assert session.page.url.endswith("/three")
        assert session.page.front is True

    for p in pages:
        p.front = False
    with mod.leased_page("profile-pages", page_index=1, connect_over_cdp=connect) as session:
        assert session.page.url.endswith("/two")
        assert session.page.front is True

    for p in pages:
        p.front = False
    with mod.leased_page(
        "profile-pages",
        page_url="example.com/one",
        connect_over_cdp=connect,
    ) as session:
        assert session.page.url.endswith("/one")
        assert session.page.front is True


def test_browser_close_is_client_disconnect_only(monkeypatch: pytest.MonkeyPatch):
    """browser.close() must run; docs note managed profile stays up (live E2E)."""
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    closed = {"n": 0}

    class Page:
        url = "https://example.com/"
        def bring_to_front(self):
            return None
        def title(self):
            return "t"

    class FakeBrowser:
        contexts = [SimpleNamespace(pages=[Page()])]
        def close(self):
            closed["n"] += 1

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-close",
                "token": "cbm_lease_close_tokenxxxxx",
                "heartbeat_interval_seconds": 60,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            return {"expires_at": "2099-01-01T00:00:00+00:00", "heartbeat_interval_seconds": 60}
        if method == "DELETE":
            return {"ok": True, "status": 204}
        raise AssertionError(path)

    monkeypatch.setattr(mod, "_request", fake_request)
    with mod.leased_page("profile-close", connect_over_cdp=lambda *a, **k: FakeBrowser()) as session:
        assert session.page.title() == "t"
        assert session.browser_closed is False
    assert closed["n"] == 1


def test_cdp_headers_and_endpoint_never_put_token_in_url(monkeypatch: pytest.MonkeyPatch):
    mod = _load()
    monkeypatch.setenv("CBM_BASE_URL", "http://127.0.0.1:18115")
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    endpoint = mod.cdp_endpoint("profile-9")
    assert endpoint == "http://127.0.0.1:18115/api/profiles/profile-9/cdp"
    assert "?" not in endpoint
    headers = mod.cdp_headers("cbm_lease_abc")
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["X-CBM-Automation-Lease"] == "cbm_lease_abc"


def test_command_routing_inspect_navigate_click_fill_text_screenshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")
    monkeypatch.setenv("CBM_BROWSER_ARTIFACT_ROOT", str(tmp_path))

    class FakePage:
        def __init__(self):
            self.url = "https://example.com/"
            self._title = "Example Domain"
            self.actions: list[tuple] = []

        def bring_to_front(self):
            self.actions.append(("bring_to_front",))

        def title(self):
            return self._title

        def goto(self, url, wait_until=None):
            self.actions.append(("goto", url, wait_until))
            self.url = url
            self._title = "Navigated"

        def click(self, selector):
            self.actions.append(("click", selector))

        def fill(self, selector, text):
            self.actions.append(("fill", selector, text))

        def locator(self, selector):
            page = self

            class Loc:
                def inner_text(self_inner):
                    page.actions.append(("text", selector))
                    return "visible text"

            return Loc()

        def screenshot(self, path, full_page=False):
            self.actions.append(("screenshot", path, full_page))
            Path(path).write_bytes(b"png")

    page = FakePage()

    class FakeBrowser:
        contexts = [SimpleNamespace(pages=[page])]

        def close(self):
            return None

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-1",
                "token": "cbm_lease_deadbeefcafebabe",
                "heartbeat_interval_seconds": 60,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            return {"expires_at": "2099-01-01T00:00:00+00:00", "heartbeat_interval_seconds": 60}
        return {"ok": True, "status": 204}

    def fake_connect(endpoint, *, headers):
        assert headers["X-CBM-Automation-Lease"].startswith("cbm_lease_")
        return FakeBrowser()

    monkeypatch.setattr(mod, "_request", fake_request)

    parser = mod.build_parser()
    cases = [
        (["inspect", "--profile-id", "p1"], "inspect"),
        (["navigate", "--profile-id", "p1", "--url", "https://example.com/x"], "navigate"),
        (["click", "--profile-id", "p1", "--selector", "#go"], "click"),
        (["fill", "--profile-id", "p1", "--selector", "input", "--text", "hi"], "fill"),
        (["text", "--profile-id", "p1"], "text"),
        (["screenshot", "--profile-id", "p1", "--path", "shot.png"], "screenshot"),
    ]
    for argv, command in cases:
        args = parser.parse_args(argv)
        payload = args.func(args, connect_over_cdp=fake_connect)
        assert payload["ok"] is True
        assert payload["command"] == command
        assert "token" not in payload
        assert "Authorization" not in json.dumps(payload)

    import runpy
    import sys
    from io import StringIO

    buf = StringIO()
    old = sys.stdout
    sys.argv = ["cbm_browser_ctl.py", "--help"]
    try:
        sys.stdout = buf
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        assert exc.value.code == 0
    finally:
        sys.stdout = old
        sys.argv = ["pytest"]
    help_text = buf.getvalue()
    for name in ("inspect", "navigate", "click", "fill", "text", "screenshot", "page-index", "page-url"):
        assert name in help_text


def test_output_url_redaction_on_inspect(monkeypatch: pytest.MonkeyPatch):
    mod = _load()
    monkeypatch.setenv("CBM_AGENT_KEY", "cbm_agent_test_key_not_real")

    class Page:
        url = "https://user:pass@example.com/x?access_token=cbm_agent_secretvalue1"
        def bring_to_front(self):
            return None
        def title(self):
            return "Bearer cbm_agent_titleleak0001"

    class FakeBrowser:
        contexts = [SimpleNamespace(pages=[Page()])]
        def close(self):
            return None

    def fake_request(method, path, *, body=None, headers=None, expect_json=True):
        if method == "POST" and path.endswith("/automation-leases"):
            return {
                "lease_id": "lease-redact",
                "token": "cbm_lease_redact_tokenxxxx",
                "heartbeat_interval_seconds": 60,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            return {"expires_at": "2099-01-01T00:00:00+00:00", "heartbeat_interval_seconds": 60}
        if method == "DELETE":
            return {"ok": True, "status": 204}
        raise AssertionError(path)

    monkeypatch.setattr(mod, "_request", fake_request)
    args = mod.build_parser().parse_args(["inspect", "--profile-id", "profile-redact"])
    payload = mod.cmd_inspect(args, connect_over_cdp=lambda *a, **k: FakeBrowser())
    blob = json.dumps(payload)
    assert "pass" not in blob
    assert "cbm_agent_secretvalue1" not in blob
    assert "cbm_agent_titleleak0001" not in blob
    assert "[redacted]" in blob
