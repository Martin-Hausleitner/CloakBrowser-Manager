"""Privacy gate tests: content_capture default false, no secrets."""

from __future__ import annotations

import pytest

from backend.observability.privacy import (
    DEFAULT_CONTENT_CAPTURE,
    PrivacyError,
    assert_no_content_payload,
    looks_secret_like,
    sanitize_attributes,
)


def test_default_content_capture_is_false():
    assert DEFAULT_CONTENT_CAPTURE is False


def test_sanitize_blocks_prompts():
    with pytest.raises(PrivacyError):
        sanitize_attributes({"prompt": "hi"}, content_capture=False)


def test_sanitize_blocks_secret_like_values_on_safe_keys():
    with pytest.raises(PrivacyError):
        sanitize_attributes(
            {"cbm.run_id": "Authorization: Bearer supersecrettokenvalue"},
            content_capture=False,
        )


def test_looks_secret_like():
    assert looks_secret_like("password=hunter2")
    assert looks_secret_like("Authorization: Bearer abcdefghijklmnop")
    assert not looks_secret_like("run-123")


def test_assert_no_content_payload_nested():
    with pytest.raises(PrivacyError):
        assert_no_content_payload({"models": [{"messages": [{"role": "user"}]}]})
