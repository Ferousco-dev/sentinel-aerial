"""Phase 2 — image enhancement pipeline.

Turns a noisy, low-contrast, soft toy-drone frame into something the Phase 3
detector can work with:

    denoise  ->  CLAHE local contrast  ->  unsharp-mask sharpen

Two design decisions make this real-time on a laptop CPU:

* **Reused state.** The CLAHE operator and Gaussian kernels are built once in the
  :class:`FrameEnhancer` constructor, not per frame.
* **Adaptive quality.** ``fastNlMeansDenoisingColored`` is by far the most
  expensive stage. An :class:`AdaptiveController` measures per-frame latency and
  slides the pipeline down a quality ladder (FULL → FAST → LIGHT → BYPASS) to
  stay within the configured FPS budget, then climbs back up when there is
  headroom. The result self-tunes to whatever hardware and frame size it meets.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from .config import EnhanceConfig, EnhanceQuality
from .logging_config import get_logger

_log = get_logger("sentinel.enhance")

Frame = np.ndarray


class AdaptiveController:
    """Chooses a per-frame :class:`EnhanceQuality` from measured latency.

    Naïve single-EMA control oscillates badly here because the tiers differ in
    cost by ~100× (FULL ≈ 130 ms vs LIGHT ≈ 1 ms): the moment the pipeline drops
    to a cheap tier the average recovers, it upgrades straight back into the
    expensive tier, and stutters again. To avoid that we keep a **per-tier
    latency estimate** and gate upgrades on the *target* tier's known cost — once
    a tier is measured to blow the budget, the controller stops climbing into it.

    * Downgrade: if the current tier's EMA exceeds the budget, step down now.
    * Upgrade: only into a tier whose last measured latency was under
      ``headroom``×budget. A tier that has never run is explored exactly once so
      the controller can learn its cost, then it sticks with what fits.
    """

    def __init__(self, config: EnhanceConfig) -> None:
        self._cfg = config
        self._current = config.max_quality
        # Per-tier smoothed latency (seconds); None until first measured.
        self._tier_ema: dict[EnhanceQuality, float | None] = {
            q: None for q in EnhanceQuality
        }

    @property
    def quality(self) -> EnhanceQuality:
        return self._current

    def record(self, latency_s: float) -> None:
        """Feed back the processing time of the tier that just ran."""
        if not self._cfg.adaptive:
            return

        tier = self._current
        alpha = self._cfg.latency_ema_alpha
        prev = self._tier_ema[tier]
        self._tier_ema[tier] = latency_s if prev is None else (
            alpha * latency_s + (1 - alpha) * prev
        )

        budget = self._cfg.latency_budget_s
        current_cost = self._tier_ema[tier] or 0.0

        if current_cost > budget and tier > EnhanceQuality.BYPASS:
            self._move(EnhanceQuality(tier - 1))
            return

        if tier < self._cfg.max_quality:
            target = EnhanceQuality(tier + 1)
            predicted = self._tier_ema[target]
            # Upgrade if the target is unmeasured (explore once) or known-cheap.
            if predicted is None or predicted < budget * self._cfg.upgrade_headroom:
                self._move(target)

    def _move(self, target: EnhanceQuality) -> None:
        target = EnhanceQuality(
            max(EnhanceQuality.BYPASS, min(self._cfg.max_quality, target)))
        if target != self._current:
            cost = self._tier_ema[self._current] or 0.0
            _log.info("Adaptive enhance: %s -> %s (%.1f ms, budget %.1f ms)",
                      self._current.name, target.name,
                      cost * 1e3, self._cfg.latency_budget_s * 1e3)
            self._current = target


@dataclass
class EnhanceStats:
    """Lightweight running telemetry surfaced to the HUD / dashboard."""

    last_latency_ms: float = 0.0
    quality: str = EnhanceQuality.FULL.name


class FrameEnhancer:
    """Stateful, reusable enhancer. One instance per stream.

    Not thread-safe: the internal CLAHE operator carries state, so use one
    enhancer per worker. Construction is cheap; call :meth:`process` per frame.
    """

    def __init__(self, config: EnhanceConfig | None = None) -> None:
        self._cfg = config or EnhanceConfig()
        # Build the CLAHE operator once — recreating it per frame is wasteful.
        self._clahe = cv2.createCLAHE(
            clipLimit=self._cfg.clahe_clip_limit,
            tileGridSize=self._cfg.clahe_tile_grid,
        )
        self._controller = AdaptiveController(self._cfg)
        self.stats = EnhanceStats(quality=self._controller.quality.name)

    # -- public API ---------------------------------------------------------
    @property
    def quality(self) -> EnhanceQuality:
        return self._controller.quality

    def process(self, frame: Frame) -> Frame:
        """Enhance one BGR frame, updating adaptive state and stats.

        Returns the original frame untouched when disabled or at BYPASS tier.
        """
        if not self._cfg.enabled:
            return frame

        start = time.perf_counter()
        quality = self._controller.quality
        out = self._apply(frame, quality)
        latency = time.perf_counter() - start

        self._controller.record(latency)
        self.stats.last_latency_ms = latency * 1e3
        self.stats.quality = quality.name
        return out

    def enhance_fixed(self, frame: Frame,
                      quality: EnhanceQuality | None = None) -> Frame:
        """Enhance at a fixed tier, bypassing the adaptive controller.

        Useful for benchmarking and for offline report thumbnails where quality
        matters more than latency.
        """
        return self._apply(frame, quality or self._cfg.max_quality)

    # -- pipeline stages ----------------------------------------------------
    def _apply(self, frame: Frame, quality: EnhanceQuality) -> Frame:
        if quality == EnhanceQuality.BYPASS:
            return frame
        work = self._denoise(frame, quality)
        work = self._local_contrast(work)
        work = self._sharpen(work)
        return work

    def _denoise(self, frame: Frame, quality: EnhanceQuality) -> Frame:
        if quality == EnhanceQuality.FULL:
            return cv2.fastNlMeansDenoisingColored(
                frame, None,
                h=self._cfg.nlm_h,
                hColor=self._cfg.nlm_h_color,
                templateWindowSize=self._cfg.nlm_template_window,
                searchWindowSize=self._cfg.nlm_search_window,
            )
        if quality == EnhanceQuality.FAST:
            # Edge-preserving and an order of magnitude cheaper than NLM.
            return cv2.bilateralFilter(
                frame,
                d=self._cfg.bilateral_diameter,
                sigmaColor=self._cfg.bilateral_sigma_color,
                sigmaSpace=self._cfg.bilateral_sigma_space,
            )
        # LIGHT: skip denoise entirely.
        return frame

    def _local_contrast(self, frame: Frame) -> Frame:
        # CLAHE on L of LAB lifts detail out of shadow without blowing highlights
        # or shifting colour (unlike equalizing RGB channels independently).
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        lightness, a, b = cv2.split(lab)
        lightness = self._clahe.apply(lightness)
        return cv2.cvtColor(cv2.merge((lightness, a, b)), cv2.COLOR_LAB2BGR)

    def _sharpen(self, frame: Frame) -> Frame:
        blurred = cv2.GaussianBlur(
            frame, (0, 0), sigmaX=self._cfg.unsharp_sigma)
        sharp = cv2.addWeighted(
            frame, 1.0 + self._cfg.unsharp_amount,
            blurred, -self._cfg.unsharp_amount, 0)
        if self._cfg.unsharp_threshold > 0:
            # Only apply sharpening where the local contrast exceeds the
            # threshold, so flat/noisy regions are not re-amplified.
            low_contrast = np.abs(frame.astype(np.int16)
                                  - blurred.astype(np.int16)) \
                < self._cfg.unsharp_threshold
            np.copyto(sharp, frame, where=low_contrast)
        return sharp


def enhance_frame(frame: Frame, config: EnhanceConfig | None = None) -> Frame:
    """Stateless convenience wrapper: enhance a single frame at full quality.

    Allocates a CLAHE operator per call, so prefer a persistent
    :class:`FrameEnhancer` in the hot loop. Handy for scripts and tests.
    """
    return FrameEnhancer(config).enhance_fixed(frame)


# ---------------------------------------------------------------------------
# Benchmark utility
# ---------------------------------------------------------------------------
@dataclass
class BenchmarkResult:
    quality: str
    frames: int
    mean_ms: float
    p95_ms: float
    fps: float


def benchmark(frame: Frame,
              config: EnhanceConfig | None = None,
              iterations: int = 60) -> list[BenchmarkResult]:
    """Time every fixed quality tier on ``frame`` and return per-tier results.

    Used by ``python -m sentinel.enhance`` to right-size ``target_fps`` for the
    demo machine before going live.
    """
    cfg = config or EnhanceConfig()
    enhancer = FrameEnhancer(cfg)
    results: list[BenchmarkResult] = []

    for quality in (EnhanceQuality.LIGHT, EnhanceQuality.FAST,
                    EnhanceQuality.FULL):
        # Warm up (first call pays allocation/JIT-in-OpenCV costs).
        enhancer.enhance_fixed(frame, quality)
        samples: list[float] = []
        for _ in range(iterations):
            start = time.perf_counter()
            enhancer.enhance_fixed(frame, quality)
            samples.append((time.perf_counter() - start) * 1e3)
        samples.sort()
        mean_ms = sum(samples) / len(samples)
        p95_ms = samples[min(len(samples) - 1, int(0.95 * len(samples)))]
        results.append(BenchmarkResult(
            quality=quality.name,
            frames=iterations,
            mean_ms=mean_ms,
            p95_ms=p95_ms,
            fps=1000.0 / mean_ms if mean_ms else float("inf"),
        ))
    return results


def _main() -> int:
    """Standalone benchmark: ``python -m sentinel.enhance [WIDTH HEIGHT]``."""
    import sys

    from .logging_config import configure
    configure("INFO")

    width, height = 640, 480
    if len(sys.argv) == 3:
        width, height = int(sys.argv[1]), int(sys.argv[2])

    # Synthetic noisy, low-contrast test frame — representative of a toy feed.
    rng = np.random.default_rng(0)
    base = np.full((height, width, 3), 90, dtype=np.uint8)
    base[:, width // 2:] = 130
    noise = rng.normal(0, 18, base.shape)
    frame = np.clip(base + noise, 0, 255).astype(np.uint8)

    _log.info("Benchmarking enhancement at %dx%d…", width, height)
    for r in benchmark(frame):
        _log.info("  %-5s  mean=%6.1f ms  p95=%6.1f ms  -> %5.1f FPS",
                  r.quality, r.mean_ms, r.p95_ms, r.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
