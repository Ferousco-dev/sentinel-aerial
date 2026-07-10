# Sentinel — AI Aerial Surveillance & Response System

A **software-only** surveillance brain for cheap, closed, non-programmable WiFi
FPV toy drones (E88 / E58 / S9 and the many rebrands). The drone stays a dumb
camera; the laptop does everything else — ingest the video feed over WiFi,
enhance it, run on-device AI detection, log events, and serve a live operations
dashboard with real-time zone-breach alerting.

No hardware mods. No firmware access. Just Python and OpenCV turning a $30 toy
into an autonomous aerial sensor.

> **Status:** Phase 1 (video ingest) complete. Built as an incremental pipeline;
> each phase ships working, testable code before the next is layered on top.

---

## Architecture

The system is a pipeline of composable stages, each consuming the previous
stage's output:

```
ingest  ->  enhance  ->  detect  ->  log  ->  serve  ->  alert  ->  report
  P1         P2          P3         P4      P5        P6         P7
```

| Phase | Stage    | Capability                                                        | State |
|:-----:|----------|-------------------------------------------------------------------|:-----:|
| 1     | ingest   | Stream discovery + transport-agnostic frame source with fallback  | ✅ Done |
| 2     | enhance  | Denoise → CLAHE contrast → unsharp mask, adaptive real-time on CPU | ✅ Done |
| 3     | detect   | YOLOv8n detection (person/car/…), structured per-frame results    | ⏳     |
| 4     | log      | SQLite event log with per-class de-duplication cooldown           | ⏳     |
| 5     | serve    | FastAPI + WebSocket dashboard (live video, event feed, counters)  | ⏳     |
| 6     | alert    | Draw restricted zone → breach → dashboard + Telegram snapshot     | ⏳     |
| 7     | report   | Post-flight HTML report (duration, counts, timeline, thumbnails)  | ⏳     |

### Phase 1 design

Ingest is deliberately over-built because it is the foundation every later stage
depends on:

- **`sentinel.config`** — all tunables as frozen dataclasses (candidate hosts,
  URL templates, timeouts, reconnect policy). Declarative and override-friendly.
- **`sentinel.video.discovery`** — active discovery: fast TCP port-probe to
  shortlist live hosts, then a real `VideoCapture` that must decode a genuine
  frame before a URL is accepted (`isOpened()` lies on dead toy streams).
- **`sentinel.video.source`** — a `FrameSource` ABC with two implementations:
  - `StreamSource` — network stream with transparent reconnection and bounded
    exponential backoff for lossy drone WiFi.
  - `ScreenSource` — `mss` desktop-region capture; the universal fallback that
    works with *any* drone by grabbing its mirrored phone app.
- **`open_source(...)`** — priority ladder: explicit URL → discovery → screen.

Sources are iterables and context managers, so the whole pipeline reads as:

```python
from sentinel import DiscoveryConfig, CaptureConfig, open_source, FrameEnhancer

enhancer = FrameEnhancer()                       # reused CLAHE + adaptive control
with open_source(DiscoveryConfig(), CaptureConfig()) as feed:
    for frame in feed:                           # frame is a BGR numpy array
        clean = enhancer.process(frame)          # denoise -> CLAHE -> unsharp
        ...                                      # detect -> log -> serve
```

### Phase 2 design

The enhancer (`sentinel.enhance`) is a stateful, reusable stage
(`FrameEnhancer`) that builds its CLAHE operator once and runs
`denoise → CLAHE local contrast → unsharp mask` per frame. Two things keep it
real-time on a laptop CPU:

- **Reused state** — CLAHE operator and kernels are constructed once, not per
  frame.
- **Adaptive quality** — `fastNlMeansDenoisingColored` is ~100× the cost of the
  other stages (measured ~130 ms vs ~1 ms at 640×480). An `AdaptiveController`
  keeps a **per-tier** latency estimate and slides the pipeline along a ladder
  (`FULL → FAST → LIGHT → BYPASS`) to hold the configured FPS budget. Because it
  remembers each tier's cost, it will not oscillate back into a tier it has
  learned is too expensive — it converges to the best tier that fits and stays
  there.

Right-size the budget for your demo machine first:

```bash
python -m sentinel.enhance 640 480      # benchmark each tier's latency/FPS
python -m sentinel --enhance            # run the feed with enhancement on
```

In the preview window: **`e`** toggles enhancement, **`c`** shows a live
raw-vs-enhanced split — the fastest way to demo the before/after on stage.

---

## Quick start

### One-command launch

```bash
git clone https://github.com/Ferousco-dev/sentinel-aerial.git
cd sentinel-aerial
./start.sh
```

`start.sh` creates a virtualenv, installs any missing dependencies, and boots the
full pipeline (ingest → enhance → detect → log) with sensible defaults. Configure
it with environment variables (or a gitignored `.env`):

```bash
SOURCE=screen REGION=100,100,640,480 ./start.sh   # phone-mirror capture
URL=rtsp://192.168.1.1:554/live ./start.sh          # known stream URL
LOG=0 ./start.sh                                    # detect but don't write events.db
ENHANCE=0 DETECT=0 ./start.sh                       # raw feed only
```

Vars: `SOURCE` (`auto`|`screen`), `URL`, `REGION`, `ENHANCE`, `DETECT`, `LOG`,
`LOG_LEVEL`. Extra args pass straight through: `./start.sh --log-level DEBUG`.

### Live web dashboard

```bash
DASHBOARD=1 DETECT=1 LOG=1 ./start.sh          # then open http://127.0.0.1:8000
# or directly:
python -m sentinel --dashboard --screen --region 0,0,640,480 --detect --log
```

Serves a FastAPI + WebSocket dashboard: live annotated video, a real-time event
feed, and per-class counters — no frontend framework, just one HTML page. The
pipeline runs on a background thread and streams JPEG frames + events to every
connected browser. Host/port via `--host`/`--port` or `DASHBOARD_HOST`/`DASHBOARD_PORT`.

### Secrets & `.env`

Copy `.env.example` to `.env` (gitignored) and fill in your keys — Telegram bot
token/chat id, dashboard token, Claude API key. `start.sh` loads `.env`
automatically. **Never commit `.env`.**

### Manual launch

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Power on the drone, join its WiFi hotspot from the laptop, then:

```bash
# Auto-discover a direct stream and open the preview window
python -m sentinel

# Phone-mirror fallback: mirror the vendor app to the laptop, capture a region
python -m sentinel --screen --region 100,100,640,480

# Try a known stream URL directly
python -m sentinel --url http://192.168.1.1:8080/?action=stream
```

Preview keys: **`q`** quit · **`s`** snapshot (saved to `snapshots/`).

---

## Requirements

- Python 3.10+
- `opencv-python`, `numpy`, `mss` (Phase 1)
- Later phases add `ultralytics`, `fastapi`, `uvicorn`, `websockets`, `requests`
  — see the `optional-dependencies` in `pyproject.toml`.

Models are lightweight only (YOLOv8n, ~6 MB) and downloaded on demand — no large
weights are committed to the repo.

---

## License

MIT — see [LICENSE](LICENSE).
