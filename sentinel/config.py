"""Typed configuration for the ingest subsystem.

All tunables live here as frozen dataclasses so behaviour is declarative,
testable, and overridable from the CLI without touching call sites. No I/O or
side effects belong in this module.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Sequence


class SourceKind(str, enum.Enum):
    """Provenance of a frame stream. Downstream stages branch on this."""

    STREAM = "stream"   # a real network video stream (RTSP / HTTP-MJPEG / UDP)
    SCREEN = "screen"   # desktop region capture (phone-mirror fallback)


class EnhanceQuality(enum.IntEnum):
    """Enhancement tiers, ordered by cost. The adaptive controller moves along
    this ladder to hold a latency budget; ``IntEnum`` so tiers compare/step.

    The only difference between tiers is the denoise stage (the expensive part);
    CLAHE and unsharp are cheap and run at every non-bypass tier.
    """

    BYPASS = 0   # return the frame untouched (controller emergency floor)
    LIGHT = 1    # CLAHE + unsharp, no denoise
    FAST = 2     # cheap bilateral denoise + CLAHE + unsharp
    FULL = 3     # fastNlMeansDenoisingColored + CLAHE + unsharp (best quality)


@dataclass(frozen=True)
class DiscoveryConfig:
    """Search space and timing for locating a toy-drone video stream.

    Cheap E88/E58-class drones expose their camera on a small, well-known set of
    gateway IPs and URL patterns once you join their WiFi AP. We TCP-probe the
    media ports first (fast) and only run a full capture attempt against hosts
    that answer, because an unanswered ``VideoCapture`` on RTSP can block for
    seconds.
    """

    # Gateway IPs commonly handed out by these drones' access points.
    candidate_ips: Sequence[str] = (
        "192.168.4.1",     # generic ESP/AP firmware
        "192.168.1.1",
        "192.168.0.1",
        "192.168.4.153",   # observed on some E88 / "HFun" units
        "192.168.10.1",
        "172.16.10.1",     # observed on some "LSC" / "WiFi UFO" units
        "192.168.1.10",
    )

    # URL templates, cheapest/most-likely first. ``{ip}`` is substituted per host.
    url_templates: Sequence[str] = (
        # HTTP MJPEG (mjpg-streamer style) — most common on these toys.
        "http://{ip}:8080/?action=stream",
        "http://{ip}:8080/video",
        "http://{ip}:8080/live",
        "http://{ip}/live",
        "http://{ip}:80/",
        "http://{ip}:81/stream",       # ESP32-CAM based rigs
        # RTSP.
        "rtsp://{ip}:554/live",
        "rtsp://{ip}:554/11",
        "rtsp://{ip}:554/ch0_0.h264",
        "rtsp://{ip}:554/",
        # UDP (Tello-like; usually needs a handshake, but cheap to attempt).
        "udp://@{ip}:11111",
        "udp://@0.0.0.0:11111",
    )

    # TCP ports quick-probed to decide whether a host is worth a capture attempt.
    probe_ports: Sequence[int] = (8080, 80, 81, 554, 8554)

    # Per-port TCP connect timeout (seconds).
    port_timeout_s: float = 0.4

    # Time budget to pull the first valid frame from a candidate URL (seconds).
    first_frame_timeout_s: float = 4.0

    def iter_urls(self, live_ips: Sequence[str]) -> list[str]:
        """Expand templates into a de-duplicated, ordered URL list.

        TCP-backed templates are only expanded for ``live_ips`` (hosts that
        answered a port probe); UDP templates cannot be probed so they are
        always emitted against the first candidate IP.
        """
        hosts = list(live_ips) or list(self.candidate_ips)
        urls: list[str] = []
        for template in self.url_templates:
            if template.startswith("udp://"):
                urls.append(template.replace("{ip}", self.candidate_ips[0]))
            else:
                urls.extend(template.format(ip=ip) for ip in hosts)

        seen: set[str] = set()
        ordered: list[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                ordered.append(url)
        return ordered


@dataclass(frozen=True)
class CaptureConfig:
    """Runtime behaviour of an open frame source."""

    # Reconnect policy for network streams that drop mid-flight.
    reconnect_attempts: int = 5
    reconnect_backoff_s: float = 0.5      # multiplied by attempt index
    read_retry_pause_s: float = 0.2       # pause after a transient empty read

    # Screen-capture region as (left, top, width, height); None = full monitor.
    screen_region: tuple[int, int, int, int] | None = None
    screen_monitor_index: int = 1         # mss: 0 is the virtual all-monitors box

    # Directory for operator snapshots taken from the preview window.
    snapshot_dir: str = "snapshots"


@dataclass(frozen=True)
class EnhanceConfig:
    """Parameters for the enhancement pipeline (denoise → CLAHE → unsharp).

    Defaults are tuned for a ~640×480 low-quality toy-drone feed on a laptop
    CPU. The adaptive controller may downgrade ``max_quality`` at runtime to keep
    per-frame latency under ``target_fps``; it never exceeds ``max_quality``.
    """

    # -- pipeline toggle & starting/ceiling quality tier --
    enabled: bool = True
    max_quality: EnhanceQuality = EnhanceQuality.FULL

    # -- adaptive controller --
    adaptive: bool = True
    target_fps: float = 20.0          # latency budget = 1/target_fps per frame
    latency_ema_alpha: float = 0.3    # smoothing for the latency estimate
    upgrade_headroom: float = 0.6     # upgrade only if EMA < headroom*budget

    # -- FULL-tier denoise (fastNlMeansDenoisingColored) --
    nlm_h: float = 7.0                # luminance filter strength
    nlm_h_color: float = 7.0          # chrominance filter strength
    nlm_template_window: int = 7      # must be odd
    nlm_search_window: int = 21       # must be odd; dominates cost

    # -- FAST-tier denoise (bilateral: edge-preserving, far cheaper) --
    bilateral_diameter: int = 5
    bilateral_sigma_color: float = 50.0
    bilateral_sigma_space: float = 50.0

    # -- CLAHE (adaptive local contrast on the L channel of LAB) --
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: tuple[int, int] = (8, 8)

    # -- unsharp mask (sharpen = frame + amount*(frame - blur)) --
    unsharp_amount: float = 0.7
    unsharp_sigma: float = 1.0
    unsharp_threshold: int = 0        # only sharpen pixels changing by > threshold

    def __post_init__(self) -> None:
        # Odd-window invariants that OpenCV requires; fail fast if misconfigured.
        if self.nlm_template_window % 2 == 0 or self.nlm_search_window % 2 == 0:
            raise ValueError("NLM window sizes must be odd.")
        if self.target_fps <= 0:
            raise ValueError("target_fps must be positive.")

    @property
    def latency_budget_s(self) -> float:
        """Per-frame processing budget derived from the target FPS."""
        return 1.0 / self.target_fps


@dataclass(frozen=True)
class AppConfig:
    """Top-level aggregate passed through the CLI."""

    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    enhance: EnhanceConfig = field(default_factory=EnhanceConfig)
    prefer_screen: bool = False
    forced_url: str | None = None
    enhance_enabled: bool = False
    log_level: str = "INFO"
