"""API tests for typed task-run outputs and internal worker auth."""

from __future__ import annotations

import concurrent.futures
import threading
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from backend import database as db


WORKER_TOKEN = "worker-test-secret"


@pytest.fixture()
def client_access(tmp_db, monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "AUTH_TOKEN", "bootstrap-test-secret")
    monkeypatch.setattr(main, "ACCESS_CONTROL_ENABLED", True)
    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", WORKER_TOKEN)
    main._login_failures.clear()
    monkeypatch.setattr(main.browser_mgr, "cleanup_stale", AsyncMock())
    monkeypatch.setattr(main.browser_mgr, "cleanup_all", AsyncMock())
    monkeypatch.setattr(main.browser_mgr.vnc, "cleanup_stale", AsyncMock())
    with TestClient(main.app) as client:
        yield client


def bootstrap_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bootstrap-test-secret"}


def worker_headers() -> dict[str, str]:
    return {"X-CBM-Worker-Token": WORKER_TOKEN}


def create_user(
    client: TestClient,
    username: str,
    sandbox_id: str,
    *permissions: str,
) -> str:
    password = f"{username}-password-123"
    response = client.post(
        "/api/access/users",
        headers=bootstrap_headers(),
        json={
            "username": username,
            "password": password,
            "grants": [
                {"sandbox_id": sandbox_id, "permission": permission}
                for permission in permissions
            ],
        },
    )
    assert response.status_code == 201, response.text
    return password


def login(client: TestClient, username: str, password: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def seed_passed_health(profile_id: str) -> None:
    db.upsert_profile_health(
        profile_id,
        state="passed",
        checked_at=datetime.now(timezone.utc).isoformat(),
        proxy_configured=False,
        proxy_reachable=True,
        proxy_authenticity_score=88,
        fingerprint_consistency_score=100,
        browser_scan_score=90,
        warnings=[],
        blockers=[],
        error_code=None,
        sources={"proxy_authenticity": "measured"},
    )


def create_queued_run(client: TestClient) -> tuple[dict, dict]:
    profile = db.create_profile("Alpha browser", sandbox_id="alpha")
    seed_passed_health(profile["id"])
    session = db.create_task_session(profile["id"], "alpha", "bootstrap")
    password = create_user(client, "alpha-auto", "alpha", "automate")
    login(client, "alpha-auto", password)
    created = client.post(
        f"/api/task-sessions/{session['id']}/runs",
        json={
            "harness": "browser-use",
            "task": "Collect typed outputs",
            "profile_id": profile["id"],
            "allowed_origins": ["https://example.com"],
            "max_steps": 20,
            "timeout_seconds": 300,
        },
    )
    assert created.status_code == 201, created.text
    return created.json(), profile


def append_internal_output(client: TestClient, run_id: str, body: dict, headers=None):
    return client.post(
        f"/internal/task-runs/{run_id}/outputs",
        headers=headers if headers is not None else worker_headers(),
        json=body,
    )


def test_output_idempotency_and_first_action_marker(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    body = {
        "idempotency_key": "step-1",
        "kind": "action",
        "summary": "Navigate",
        "payload": {"url": "https://example.com"},
    }
    first = append_internal_output(client_access, run["id"], body)
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["sequence"] == 1
    assert first_body["kind"] == "action"

    second = append_internal_output(client_access, run["id"], body)
    assert second.status_code == 201
    assert second.json()["id"] == first_body["id"]
    assert second.json()["sequence"] == first_body["sequence"]

    fetched = client_access.get(f"/api/task-runs/{run['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["first_action_sequence"] == first_body["sequence"]
    assert fetched.json()["first_action_at"]

    later_action = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "step-2",
            "kind": "action",
            "summary": "Click",
            "payload": {"selector": "#go"},
        },
    )
    assert later_action.status_code == 201
    assert later_action.json()["sequence"] == 2
    still = client_access.get(f"/api/task-runs/{run['id']}").json()
    assert still["first_action_sequence"] == first_body["sequence"]
    assert still["first_action_at"] == fetched.json()["first_action_at"]


def test_conflicting_idempotency_key_returns_409(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    body = {
        "idempotency_key": "same-key",
        "kind": "status",
        "summary": "Working",
        "payload": {"status": "running"},
    }
    assert append_internal_output(client_access, run["id"], body).status_code == 201
    conflict = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "same-key",
            "kind": "status",
            "summary": "Different",
            "payload": {"status": "running"},
        },
    )
    assert conflict.status_code == 409


def test_non_action_output_does_not_set_first_action(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    status = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "status-1",
            "kind": "status",
            "summary": "Queued",
            "payload": {"status": "queued"},
        },
    )
    assert status.status_code == 201
    fetched = client_access.get(f"/api/task-runs/{run['id']}").json()
    assert fetched["first_action_sequence"] is None
    assert fetched["first_action_at"] is None


def test_tail_uses_exclusive_after_sequence_and_ascending_order(
    client_access: TestClient,
):
    run, _profile = create_queued_run(client_access)
    for index in range(1, 5):
        response = append_internal_output(
            client_access,
            run["id"],
            {
                "idempotency_key": f"obs-{index}",
                "kind": "observation",
                "summary": f"Note {index}",
                "payload": {"text": f"line-{index}"},
            },
        )
        assert response.status_code == 201

    login(client_access, "alpha-auto", "alpha-auto-password-123")
    tail = client_access.get(
        f"/api/task-runs/{run['id']}/outputs",
        params={"after_sequence": 2, "limit": 2},
    )
    assert tail.status_code == 200
    items = tail.json()
    assert [item["sequence"] for item in items] == [3, 4]
    assert [item["summary"] for item in items] == ["Note 3", "Note 4"]


def test_public_tail_requires_view_and_hides_cross_sandbox(client_access: TestClient):
    alpha = db.create_profile("Alpha browser", sandbox_id="alpha")
    beta = db.create_profile("Beta browser", sandbox_id="beta")
    seed_passed_health(alpha["id"])
    seed_passed_health(beta["id"])
    beta_session = db.create_task_session(beta["id"], "beta", "bootstrap")
    beta_password = create_user(client_access, "beta-auto", "beta", "automate")
    login(client_access, "beta-auto", beta_password)
    beta_run = client_access.post(
        f"/api/task-sessions/{beta_session['id']}/runs",
        json={
            "harness": "browser-use",
            "task": "beta task",
            "profile_id": beta["id"],
            "allowed_origins": ["https://example.com"],
            "max_steps": 10,
            "timeout_seconds": 60,
        },
    ).json()
    assert (
        append_internal_output(
            client_access,
            beta_run["id"],
            {
                "idempotency_key": "beta-1",
                "kind": "summary",
                "summary": "done",
                "payload": {"text": "ok"},
            },
        ).status_code
        == 201
    )

    alpha_password = create_user(client_access, "alpha-view", "alpha", "view")
    login(client_access, "alpha-view", alpha_password)
    denied = client_access.get(f"/api/task-runs/{beta_run['id']}/outputs")
    assert denied.status_code == 404


def test_internal_output_auth_fail_closed_and_rejects_public_bearer(
    client_access: TestClient,
    monkeypatch,
):
    run, _profile = create_queued_run(client_access)
    body = {
        "idempotency_key": "auth-1",
        "kind": "metric",
        "summary": "latency",
        "payload": {"name": "rtt", "value": 12, "unit": "ms"},
    }

    missing = append_internal_output(client_access, run["id"], body, headers={})
    assert missing.status_code == 401

    wrong = append_internal_output(
        client_access,
        run["id"],
        body,
        headers={"X-CBM-Worker-Token": "wrong-token"},
    )
    assert wrong.status_code == 401
    assert missing.json() == wrong.json()

    bearer = append_internal_output(
        client_access,
        run["id"],
        body,
        headers=bootstrap_headers(),
    )
    assert bearer.status_code == 401

    from backend import main

    monkeypatch.setattr(main, "CBM_WORKER_TOKEN", None)
    unset = append_internal_output(client_access, run["id"], body)
    assert unset.status_code == 401
    assert "worker-test-secret" not in unset.text
    assert "worker-test-secret" not in unset.headers.get("authorization", "")


def test_output_validation_rejects_secrets_and_unsafe_screenshot_payload(
    client_access: TestClient,
):
    run, _profile = create_queued_run(client_access)

    secret = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "bad-secret",
            "kind": "observation",
            "summary": "leak",
            "payload": {"text": "ok", "authorization": "Bearer secret"},
        },
    )
    assert secret.status_code == 422
    assert "Bearer secret" not in secret.text

    screenshot = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "bad-shot",
            "kind": "screenshot",
            "summary": "frame",
            "payload": {
                "artifact_id": "future-ref",
                "path": "/tmp/secret.png",
            },
        },
    )
    assert screenshot.status_code == 422

    unknown_kind = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "bad-kind",
            "kind": "dom_dump",
            "summary": "nope",
            "payload": {},
        },
    )
    assert unknown_kind.status_code == 422

    safe_shot = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "good-shot",
            "kind": "screenshot",
            "summary": "frame metadata",
            "payload": {
                "artifact_id": "opaque-ref",
                "width": 1280,
                "height": 720,
                "media_type": "image/png",
                "sha256": "a" * 64,
            },
        },
    )
    assert safe_shot.status_code == 201, safe_shot.text


def test_supported_output_kinds_accept_allowlisted_payloads(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    cases = [
        ("status", {"status": "ok"}),
        ("action", {"name": "click", "selector": "#x"}),
        ("observation", {"text": "visible"}),
        ("extracted_data", {"label": "title", "data": {"value": "Hello"}}),
        ("link", {"url": "https://example.com", "title": "Example"}),
        ("metric", {"name": "steps", "value": 3, "unit": "count"}),
        ("error", {"code": "nav_blocked", "message": "blocked", "retryable": False}),
        ("approval", {"prompt": "Continue?", "required": True}),
        ("summary", {"text": "done", "result": "success"}),
    ]
    for index, (kind, payload) in enumerate(cases, start=1):
        response = append_internal_output(
            client_access,
            run["id"],
            {
                "idempotency_key": f"kind-{index}",
                "kind": kind,
                "summary": f"{kind} ok",
                "payload": payload,
            },
        )
        assert response.status_code == 201, (kind, response.text)
        assert response.json()["kind"] == kind


def test_concurrent_output_sequence_and_first_action_atomicity(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    start = threading.Barrier(2)
    results: list[object] = []

    def append_action(key: str) -> None:
        start.wait(timeout=5)
        response = append_internal_output(
            client_access,
            run["id"],
            {
                "idempotency_key": key,
                "kind": "action",
                "summary": key,
                "payload": {"name": key},
            },
        )
        results.append((key, response.status_code, response.json()))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(append_action, "a1"),
            executor.submit(append_action, "a2"),
        ]
        for future in futures:
            future.result(timeout=10)

    assert all(status == 201 for _key, status, _body in results)
    sequences = sorted(body["sequence"] for _key, _status, body in results)
    assert sequences == [1, 2]

    fetched = client_access.get(f"/api/task-runs/{run['id']}").json()
    first_sequences = [
        body["sequence"]
        for _key, _status, body in results
        if body["sequence"] == fetched["first_action_sequence"]
    ]
    assert len(first_sequences) == 1
    assert fetched["first_action_sequence"] in {1, 2}
    assert fetched["first_action_at"]


SECRET_MARKER = "secret-token-MARKER-do-not-echo"


def _assert_rejected_without_echo(response, marker: str = SECRET_MARKER) -> None:
    assert response.status_code == 422, response.text
    assert marker not in response.text
    assert marker not in response.headers.get("www-authenticate", "")


def test_output_rejects_unknown_top_level_authorization_field(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    response = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "extra-auth",
            "kind": "observation",
            "summary": "visible note",
            "payload": {"text": "ok"},
            "authorization": f"Bearer {SECRET_MARKER}",
        },
    )
    _assert_rejected_without_echo(response)


def test_output_rejects_sensitive_content_in_allowed_payload_text(
    client_access: TestClient,
):
    run, _profile = create_queued_run(client_access)
    cases = [
        ("auth-bearer", {"text": f"Authorization: Bearer {SECRET_MARKER}"}),
        ("cookie-assign", {"text": f"cookie={SECRET_MARKER}"}),
        ("set-cookie", {"text": f"Set-Cookie: session={SECRET_MARKER}"}),
        ("password-assign", {"text": f"password={SECRET_MARKER}"}),
        ("secret-assign", {"text": f"secret: {SECRET_MARKER}"}),
        ("api-key-assign", {"text": f"api-key={SECRET_MARKER}"}),
        ("access-token-assign", {"text": f"access-token={SECRET_MARKER}"}),
        ("proxy-creds", {"text": f"http://user:{SECRET_MARKER}@proxy.example:8080"}),
    ]
    for key, payload in cases:
        response = append_internal_output(
            client_access,
            run["id"],
            {
                "idempotency_key": f"sensitive-{key}",
                "kind": "observation",
                "summary": "safe summary",
                "payload": payload,
            },
        )
        _assert_rejected_without_echo(response)


def test_output_rejects_raw_html_and_dom_markup(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    response = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "html-dom",
            "kind": "observation",
            "summary": "page note",
            "payload": {"text": f"<html><body data-secret='{SECRET_MARKER}'></body></html>"},
        },
    )
    _assert_rejected_without_echo(response)


def test_output_rejects_filesystem_paths_but_allows_https_urls(
    client_access: TestClient,
):
    run, _profile = create_queued_run(client_access)
    path_cases = [
        ("rel-path", {"text": "tmp/secret.png"}),
        ("dot-rel", {"text": "../secret"}),
        ("windows-drive", {"text": r"C:\secret\file.txt"}),
        ("unc-path", {"text": r"\\server\share\secret.txt"}),
    ]
    for key, payload in path_cases:
        response = append_internal_output(
            client_access,
            run["id"],
            {
                "idempotency_key": f"path-{key}",
                "kind": "observation",
                "summary": "path probe",
                "payload": payload,
            },
        )
        assert response.status_code == 422, (key, response.text)

    allowed = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "https-url-ok",
            "kind": "link",
            "summary": "safe link",
            "payload": {"url": "https://example.com/docs", "title": "Docs"},
        },
    )
    assert allowed.status_code == 201, allowed.text


def test_output_rejects_base64_and_data_uri_blobs(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    long_b64 = "A" * 80 + "B" * 40 + "="
    cases = [
        ("data-uri", {"text": f"data:image/png;base64,{SECRET_MARKER}"}),
        ("base64-colon", {"text": f"base64:{SECRET_MARKER}"}),
        ("base64-comma", {"text": f"base64,{SECRET_MARKER}"}),
        ("long-blob", {"text": long_b64}),
    ]
    for key, payload in cases:
        response = append_internal_output(
            client_access,
            run["id"],
            {
                "idempotency_key": f"b64-{key}",
                "kind": "observation",
                "summary": "blob probe",
                "payload": payload,
            },
        )
        if SECRET_MARKER in payload["text"]:
            _assert_rejected_without_echo(response)
        else:
            assert response.status_code == 422, (key, response.text)


def test_output_rejects_sensitive_content_in_summary(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    response = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "summary-secret",
            "kind": "observation",
            "summary": f"Authorization: Bearer {SECRET_MARKER}",
            "payload": {"text": "otherwise fine"},
        },
    )
    _assert_rejected_without_echo(response)


def test_output_allows_ordinary_prose_mentioning_token_word(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    response = append_internal_output(
        client_access,
        run["id"],
        {
            "idempotency_key": "prose-token",
            "kind": "observation",
            "summary": "Received a token from the page title",
            "payload": {"text": "The form asked for a token count"},
        },
    )
    assert response.status_code == 201, response.text


def test_screenshot_artifact_id_rejects_path_traversal(client_access: TestClient):
    run, _profile = create_queued_run(client_access)
    for key, artifact_id in [
        ("slash", "dir/secret"),
        ("backslash", r"dir\secret"),
        ("dotdot", "../secret"),
    ]:
        response = append_internal_output(
            client_access,
            run["id"],
            {
                "idempotency_key": f"artifact-{key}",
                "kind": "screenshot",
                "summary": "frame metadata",
                "payload": {
                    "artifact_id": artifact_id,
                    "width": 10,
                    "height": 10,
                    "media_type": "image/png",
                    "sha256": "b" * 64,
                },
            },
        )
        assert response.status_code == 422, (key, response.text)
