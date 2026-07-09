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
| 2     | enhance  | Denoise → CLAHE contrast → unsharp mask, near real-time on CPU     | ⏳ Next |
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
from sentinel import DiscoveryConfig, CaptureConfig, open_source

with open_source(DiscoveryConfig(), CaptureConfig()) as feed:
    for frame in feed:        # frame is a BGR numpy array
        ...                   # enhance -> detect -> log -> serve
```

---

## Quick start

```bash
git clone https://github.com/Ferousco-dev/sentinel-aerial.git
cd sentinel-aerial
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
