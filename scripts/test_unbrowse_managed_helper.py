from __future__ import annotations

import pytest

from scripts.unbrowse_managed_helper import (
    normalized_origin,
    rewrite_discovery,
    select_target,
    validate_cdp_state,
    validate_flow,
)


def cdp_flow(*actions: dict[str, object], **overrides: object) -> dict[str, object]:
    flow: dict[str, object] = {
        "engine": "cdp",
        "url": "https://cloud.browser-use.com/signup",
        "actions": list(actions),
    }
    flow.update(overrides)
    return flow


def test_normalized_origin_removes_default_port_and_rejects_credentials() -> None:
    assert normalized_origin("HTTPS://Cloud.Browser-Use.com:443/signup?q=1") == (
        "https://cloud.browser-use.com"
    )
    with pytest.raises(ValueError, match="absolute http"):
        normalized_origin("file:///tmp/secret")


def test_select_target_requires_allowed_origin_and_persisted_task_url() -> None:
    flow = cdp_flow({"op": "snapshot"})
    claim = {
        "allowed_origins": ["https://cloud.browser-use.com"],
        "task": "Open https://cloud.browser-use.com/signup using the managed profile.",
    }
    assert select_target(claim, flow) == "https://cloud.browser-use.com/signup"

    claim["task"] = "Open https://example.com only."
    with pytest.raises(ValueError, match="persisted task"):
        select_target(claim, flow)


def test_validate_flow_accepts_same_origin_navigation_and_rejects_cross_origin() -> None:
    task = (
        "Open https://cloud.browser-use.com/signup then "
        "https://cloud.browser-use.com/signin."
    )
    validate_flow(
        cdp_flow(
            {"op": "fill", "selector": "#email", "source": "vcvm_email"},
            {"op": "navigate", "url": "https://cloud.browser-use.com/signin"},
            {"op": "snapshot"},
        ),
        task=task,
    )

    with pytest.raises(ValueError, match="navigation origin"):
        validate_flow(
            cdp_flow({"op": "navigate", "url": "https://attacker.invalid/collect"}),
            task=task,
        )

    with pytest.raises(ValueError, match="explicit in the persisted task"):
        validate_flow(
            cdp_flow(
                {
                    "op": "navigate",
                    "url": "https://cloud.browser-use.com/account/delete",
                }
            ),
            task=task,
        )


def test_validate_flow_rejects_unapproved_cdp_value_source() -> None:
    with pytest.raises(ValueError, match="approved VCVM value source"):
        validate_flow(
            cdp_flow({"op": "fill", "selector": "#password", "source": "literal"})
        )


def test_rewrite_discovery_keeps_cdp_on_ephemeral_loopback_gateway() -> None:
    rewritten = rewrite_discovery(
        {
            "webSocketDebuggerUrl": (
                "ws://manager/api/profiles/profile/cdp/devtools/browser/browser-id"
            )
        },
        local_base="ws://127.0.0.1:43123",
        upstream_path="/api/profiles/profile/cdp",
    )
    assert rewritten == {
        "webSocketDebuggerUrl": "ws://127.0.0.1:43123/devtools/browser/browser-id"
    }


def test_authenticated_gate_requires_positive_redacted_state() -> None:
    flow = cdp_flow(
        {"op": "snapshot"},
        require_authenticated=True,
        require_progress=True,
    )
    validate_cdp_state(
        {
            "authenticated": True,
            "captcha": False,
            "formCount": 0,
            "verification": False,
        },
        flow,
    )

    with pytest.raises(RuntimeError, match="login was not proven"):
        validate_cdp_state(
            {
                "authenticated": False,
                "captcha": False,
                "formCount": 1,
                "verification": False,
            },
            flow,
        )

    with pytest.raises(RuntimeError, match="authentication request failed"):
        validate_cdp_state(
            {
                "authenticated": True,
                "captcha": False,
                "formCount": 0,
                "fetchResults": [{"status": 401, "genericError": True}],
            },
            flow,
        )


def test_captcha_fails_closed_even_when_authenticated_marker_is_present() -> None:
    with pytest.raises(RuntimeError, match="human verification"):
        validate_cdp_state(
            {"authenticated": True, "captcha": True, "formCount": 0},
            cdp_flow({"op": "snapshot"}, require_authenticated=True),
        )
