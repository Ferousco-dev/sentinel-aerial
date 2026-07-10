"""Unit tests for the Telegram breach notifier (T13).

A fake requests-style session captures outbound calls, so the send path, the
per-zone cooldown, graceful-disable, and error tolerance are all verified with
no network and no credentials.
"""

from __future__ import annotations

import numpy as np

from sentinel import AlertConfig, TelegramNotifier

_FRAME = np.zeros((16, 16, 3), np.uint8)


class _FakeResp:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text


class _FakeSession:
    def __init__(self, status=200, raise_exc=None):
        self.calls = []
        self._status = status
        self._raise = raise_exc

    def post(self, url, data=None, files=None, timeout=None):
        if self._raise:
            raise self._raise
        self.calls.append({"url": url, "data": data, "files": files})
        return _FakeResp(self._status)


def _notifier(session, cooldown=30.0, enabled=True):
    return TelegramNotifier(
        token="TESTTOKEN", chat_id="123456",
        config=AlertConfig(enabled=enabled, cooldown_s=cooldown),
        session=session)


def test_disabled_without_credentials():
    n = TelegramNotifier(token="", chat_id="", session=_FakeSession())
    assert n.enabled is False
    assert n.notify_breach(_FRAME, "north", "hi") is False


def test_disabled_by_config():
    n = _notifier(_FakeSession(), enabled=False)
    assert n.enabled is False


def test_sends_photo_with_caption():
    s = _FakeSession()
    n = _notifier(s)
    assert n.notify_breach(_FRAME, "north", "breach!", now=100.0) is True
    n.flush(); n.close()
    assert len(s.calls) == 1
    call = s.calls[0]
    assert call["url"].endswith("/sendPhoto")
    assert "TESTTOKEN" in call["url"]
    assert call["data"]["chat_id"] == "123456"
    assert call["data"]["caption"] == "breach!"
    assert call["files"]["photo"][2] == "image/jpeg"


def test_per_zone_cooldown_suppresses():
    s = _FakeSession()
    n = _notifier(s, cooldown=30.0)
    assert n.notify_breach(_FRAME, "north", "1", now=100.0) is True
    assert n.notify_breach(_FRAME, "north", "2", now=110.0) is False   # < 30s
    assert n.notify_breach(_FRAME, "north", "3", now=131.0) is True    # >= 30s
    # A different zone is independent.
    assert n.notify_breach(_FRAME, "south", "4", now=110.0) is True
    n.flush(); n.close()
    assert len(s.calls) == 3


def test_network_error_is_swallowed():
    s = _FakeSession(raise_exc=RuntimeError("boom"))
    n = _notifier(s)
    assert n.notify_breach(_FRAME, "north", "x", now=100.0) is True  # queued
    n.flush(); n.close()  # worker hits the error but must not crash


def test_http_error_status_logged_not_raised():
    s = _FakeSession(status=403)
    n = _notifier(s)
    n.notify_breach(_FRAME, "north", "x", now=100.0)
    n.flush(); n.close()
    assert len(s.calls) == 1  # attempted; 403 handled gracefully


def test_config_validation():
    import pytest
    with pytest.raises(ValueError):
        AlertConfig(cooldown_s=-1)
    with pytest.raises(ValueError):
        AlertConfig(jpeg_quality=0)
