"""FastAPI app: serves the dashboard page and streams frames/events over WS.

Each WebSocket connection runs its own async loop that reads the latest snapshot
from :class:`LiveState` and pushes it to that client. Because every client paces
itself and simply skips frames when it can't keep up, a slow client can never
apply backpressure to the pipeline or to other clients.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import AppConfig, DashboardConfig
from ..logging_config import get_logger
from .runner import PipelineRunner
from .state import LiveState

_log = get_logger("sentinel.dashboard.server")

_STATIC = Path(__file__).parent / "static"


def _index_html() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


def create_app(config: AppConfig,
               state: LiveState | None = None,
               runner: PipelineRunner | None = None) -> FastAPI:
    """Build the FastAPI app.

    If ``runner`` is provided it is started/stopped with the app lifespan; pass
    ``None`` (e.g. in tests) to serve a static ``state`` with no pipeline.
    """
    dash: DashboardConfig = config.dashboard
    state = state or LiveState(max_events=dash.max_events)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if runner is not None:
            runner.start()
        try:
            yield
        finally:
            if runner is not None:
                runner.stop()

    app = FastAPI(title="Sentinel Dashboard", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _index_html()

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "running": state.running})

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        last_frame_seq = -1
        last_event_id = 0
        try:
            # Replay recent events so a fresh client isn't blank.
            for e in state.events_since(0):
                await websocket.send_json({"type": "event", **e})
                last_event_id = e["id"]

            while True:
                seq, jpeg, stats = state.frame_snapshot()
                if jpeg is not None and seq != last_frame_seq:
                    await websocket.send_json({
                        "type": "frame",
                        "seq": seq,
                        "jpg": base64.b64encode(jpeg).decode("ascii"),
                        "stats": stats,
                    })
                    last_frame_seq = seq

                new_events = state.events_since(last_event_id)
                for e in new_events:
                    await websocket.send_json({"type": "event", **e})
                    last_event_id = e["id"]

                await asyncio.sleep(dash.push_interval_s)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("WebSocket closed: %s", exc)

    return app


def serve(config: AppConfig) -> None:
    """Blocking entry point: build the app + runner and run uvicorn."""
    import uvicorn

    state = LiveState(max_events=config.dashboard.max_events)
    runner = PipelineRunner(config, state)
    app = create_app(config, state=state, runner=runner)
    _log.info("Dashboard on http://%s:%d",
              config.dashboard.host, config.dashboard.port)
    uvicorn.run(app, host=config.dashboard.host, port=config.dashboard.port,
                log_level="warning")
