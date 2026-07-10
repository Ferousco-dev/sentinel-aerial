"""Phase 6 (T13) — Telegram breach notifications.

On a zone breach, sends a snapshot of the offending frame to a Telegram chat via
the Bot API. Designed to never get in the pipeline's way or crash it:

* **Off by default when unconfigured.** Missing ``TELEGRAM_BOT_TOKEN`` /
  ``TELEGRAM_CHAT_ID`` (or missing ``requests``) → the notifier is simply
  disabled and every call is a no-op. CI and key-less users run fine.
* **Non-blocking.** Sends run on a background worker thread fed by a queue, so
  the network round-trip never stalls frame processing.
* **Rate-limited.** A per-zone cooldown stops a lingering intruder from spamming.
* **Failure-tolerant.** Network/API errors are logged, never raised.

Credentials come from the environment, never from config or the repo.
"""

from __future__ import annotations

import os
import queue
import threading
import time

import cv2

from .config import AlertConfig
from .logging_config import get_logger

_log = get_logger("sentinel.alerts")

_API = "https://api.telegram.org/bot{token}/{method}"
_STOP = object()  # sentinel enqueued to stop the worker


def resolve_chat_id(token: str, timeout: float = 10.0) -> str | None:
    """Best-effort: discover a chat id from the bot's recent updates.

    Returns the most recent chat id that has messaged the bot, or ``None``.
    Lets a user who has DM'd the bot skip manually copying their chat id.
    """
    try:
        import requests
        r = requests.get(_API.format(token=token, method="getUpdates"),
                         timeout=timeout)
        data = r.json()
    except Exception as exc:  # pragma: no cover - network dependent
        _log.debug("resolve_chat_id failed: %s", exc)
        return None
    if not data.get("ok"):
        return None
    for update in reversed(data.get("result", [])):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            return str(chat["id"])
    return None


class TelegramNotifier:
    """Sends breach snapshots to Telegram on a background worker thread."""

    def __init__(self, token: str | None, chat_id: str | None,
                 config: AlertConfig | None = None, session=None) -> None:
        self._cfg = config or AlertConfig()
        self._token = token or ""
        self._chat_id = chat_id or ""
        self._session = session
        self._last_sent: dict[str, float] = {}
        self._queue: queue.Queue = queue.Queue(maxsize=32)
        self._worker: threading.Thread | None = None

        self.enabled = bool(self._cfg.enabled and self._token and self._chat_id)
        if self.enabled and session is None:
            try:
                import requests
                self._session = requests.Session()
            except ImportError:
                _log.warning("requests not installed — Telegram alerts disabled.")
                self.enabled = False

        if self.enabled:
            self._worker = threading.Thread(
                target=self._run, name="telegram", daemon=True)
            self._worker.start()
            _log.info("Telegram alerts enabled (cooldown %.0fs).",
                      self._cfg.cooldown_s)
        else:
            _log.info("Telegram alerts disabled (missing token/chat id).")

    @classmethod
    def from_env(cls, config: AlertConfig | None = None,
                 session=None) -> "TelegramNotifier":
        """Build from ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``.

        If the token is present but the chat id is not, tries to auto-resolve the
        chat id from the bot's recent updates.
        """
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if token and not chat_id and session is None:
            chat_id = resolve_chat_id(token) or ""
            if chat_id:
                _log.info("Resolved Telegram chat id from getUpdates.")
        return cls(token, chat_id, config=config, session=session)

    # -- public API ---------------------------------------------------------
    def notify_breach(self, frame, zone: str, caption: str,
                      now: float | None = None) -> bool:
        """Queue a breach snapshot for ``zone``. Returns True if queued.

        Suppressed (returns False) when disabled or within the per-zone cooldown.
        """
        if not self.enabled:
            return False
        stamp = time.time() if now is None else now
        last = self._last_sent.get(zone)
        if last is not None and stamp - last < self._cfg.cooldown_s:
            return False

        ok, buf = cv2.imencode(
            ".jpg", frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._cfg.jpeg_quality])
        if not ok:
            return False
        try:
            self._queue.put_nowait((buf.tobytes(), caption))
        except queue.Full:
            _log.warning("Telegram queue full; dropping alert for '%s'.", zone)
            return False
        self._last_sent[zone] = stamp
        return True

    def flush(self, timeout: float = 5.0) -> None:
        """Block until queued messages are sent (used by tests)."""
        self._queue.join()

    def close(self) -> None:
        if self._worker is not None:
            self._queue.put(_STOP)
            self._worker.join(timeout=self._cfg.timeout_s + 2)
            self._worker = None

    # -- worker -------------------------------------------------------------
    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                jpeg, caption = item
                self._send(jpeg, caption)
            finally:
                self._queue.task_done()

    def _send(self, jpeg: bytes, caption: str) -> None:
        url = _API.format(token=self._token, method="sendPhoto")
        try:
            resp = self._session.post(
                url,
                data={"chat_id": self._chat_id, "caption": caption},
                files={"photo": ("breach.jpg", jpeg, "image/jpeg")},
                timeout=self._cfg.timeout_s,
            )
            if getattr(resp, "status_code", 200) != 200:
                _log.warning("Telegram sendPhoto HTTP %s: %s",
                             resp.status_code, getattr(resp, "text", ""))
        except Exception as exc:  # pragma: no cover - network dependent
            _log.warning("Telegram send failed: %s", exc)
