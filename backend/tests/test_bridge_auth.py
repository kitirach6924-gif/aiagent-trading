"""Regression tests for the bridge's auth contract.

Bug: `Handler._authorized` carried an unreachable `if not _ALLOW_TRADE: return
False` followed by `return False`. Both returned False, so the guard changed
nothing, while the comment above it advertised a "stdio-compatible fallback for
headerless clients" that never existed. Denying headerless requests is correct;
the misleading contract was the defect.

These tests pin the actual behaviour so a future "helpful" fallback cannot be
added silently on the strength of a comment.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402


class _FakeHandler:
    """Minimal stand-in exercising the real _authorized without a live server."""

    @staticmethod
    def make(headers, token, allow_trade):
        # Bind the real unbound function to a throwaway instance.
        from app.mt5 import http_bridge

        class H(http_bridge.Handler):
            def __init__(self):  # noqa: D107 - no BaseHTTPRequestHandler init
                self.headers = headers

        orig_t, orig_a = http_bridge._TOKEN, http_bridge._ALLOW_TRADE
        http_bridge._TOKEN = token
        http_bridge._ALLOW_TRADE = allow_trade
        try:
            return H()._authorized()
        finally:
            http_bridge._TOKEN, http_bridge._ALLOW_TRADE = orig_t, orig_a


def test_no_token_configured_denies_even_valid_looking_header():
    assert _FakeHandler.make({"Authorization": "Bearer anything"},
                             token="", allow_trade=True) is False


def test_valid_bearer_token_is_accepted():
    assert _FakeHandler.make({"Authorization": "Bearer s3cret"},
                             token="s3cret", allow_trade=True) is True


def test_wrong_bearer_token_is_rejected():
    assert _FakeHandler.make({"Authorization": "Bearer wrong"},
                             token="s3cret", allow_trade=True) is False


def test_missing_authorization_header_is_rejected_even_when_trade_allowed():
    # The contract the old comment described but the code never implemented.
    assert _FakeHandler.make({}, token="s3cret", allow_trade=True) is False


def test_missing_header_rejected_regardless_of_allow_trade_flag():
    # The removed guard was unreachable: both branches returned False. Pin the
    # behaviour that survives so nobody "restores" the no-op guard.
    for allow in (True, False):
        assert _FakeHandler.make({}, token="s3cret", allow_trade=allow) is False


def test_non_bearer_scheme_is_rejected():
    assert _FakeHandler.make({"Authorization": "Basic dXNlcjpwYXNz"},
                             token="s3cret", allow_trade=True) is False


def test_unreachable_allow_trade_guard_is_gone():
    """The source must not reintroduce the dead `if not _ALLOW_TRADE` branch."""
    import inspect

    from app.mt5 import http_bridge

    src = inspect.getsource(http_bridge.Handler._authorized)
    body = src.split('"""')[-1]  # drop the docstring that explains the history
    assert "_ALLOW_TRADE" not in body, (
        "_authorized must not branch on _ALLOW_TRADE; that guard was "
        "unreachable and its comment claimed a fallback that never existed")
    # And the real check must still be present.
    assert "compare_digest" in body, "token comparison was removed"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:warnings"]))
