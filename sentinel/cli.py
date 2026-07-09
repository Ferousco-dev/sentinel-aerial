"""Command-line entry point for the ingest subsystem.

    python -m sentinel                       # auto-discover, then preview
    python -m sentinel --screen              # phone-mirror fallback
    python -m sentinel --url rtsp://…        # known stream
    python -m sentinel --region 100,100,640,480 --screen
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from .config import AppConfig, CaptureConfig
from .logging_config import configure, get_logger
from .preview import run_preview
from .video import open_source

_log = get_logger("sentinel.cli")


def _parse_region(raw: str) -> tuple[int, int, int, int]:
    parts = raw.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be 'left,top,width,height'")
    try:
        left, top, width, height = (int(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("region values must be integers") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("region width/height must be positive")
    return left, top, width, height


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Sentinel — AI aerial surveillance ingest (Phase 1).",
    )
    parser.add_argument(
        "--screen", action="store_true",
        help="Skip discovery and capture a desktop region (phone-mirror mode).",
    )
    parser.add_argument(
        "--url", default=None,
        help="Try this exact stream URL before running discovery.",
    )
    parser.add_argument(
        "--region", type=_parse_region, default=None,
        help="Screen region 'left,top,width,height' (default: full monitor).",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity (default: INFO).",
    )
    return parser


def build_config(argv: list[str] | None = None) -> AppConfig:
    """Parse ``argv`` into an :class:`AppConfig` (pure; no side effects)."""
    args = _build_parser().parse_args(argv)
    capture = CaptureConfig(screen_region=args.region)
    return AppConfig(
        capture=capture,
        prefer_screen=args.screen,
        forced_url=args.url,
        log_level=args.log_level,
    )


def main(argv: list[str] | None = None) -> int:
    config = build_config(argv)
    configure(config.log_level)

    _log.info("Sentinel ingest starting…")
    try:
        source = open_source(
            config.discovery,
            config.capture,
            prefer_screen=config.prefer_screen,
            forced_url=config.forced_url,
        )
    except RuntimeError as exc:
        _log.error("Could not open a video source: %s", exc)
        return 2

    try:
        run_preview(source, config.capture)
    except KeyboardInterrupt:
        _log.info("Interrupted by operator.")
        source.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
