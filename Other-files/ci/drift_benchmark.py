"""Drift tracking on simulated MOKE frames (CI only): the old and the new
estimate against known shifts, their time per frame, and the residual of
bilinear and cubic shifting.

    python Other-files/ci/drift_benchmark.py

The scene is like the CIDWM camera view: 2048 x 2048, an hourglass device
(bright pads, blurred edges), ~1500 particles, vignetting fixed to the optics
(it does not move with the sample), domains that change in the centre
between the background and the frame, shot noise.
"""
import importlib.util
import math
import sys
import time
import types
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.modules.setdefault("pco", types.SimpleNamespace(Camera=object))
spec = importlib.util.spec_from_file_location("transport_drift", ROOT / "Transport_all.py")
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)

N, PAD = 2048, 128
SHIFTS = ((0.0, 0.0), (0.4, -0.35), (0.85, 0.6), (1.37, -2.81), (-5.62, 3.15), (12.25, -7.9), (35.5, 20.1))


def old_estimate(background_u16, image_u16):
    """The estimate before 2026-10-13 (OpenCV path)."""
    scale = 1.0 / 4
    bg = cv2.resize(background_u16.astype(np.float32), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    bg -= float(np.mean(bg))
    image = cv2.resize(image_u16.astype(np.float32), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    image -= float(np.mean(image))
    shift, response = cv2.phaseCorrelate(bg, image)
    return float(shift[0] * 4), float(shift[1] * 4), float(response)


def sample_scene(rng):
    size = N + 2 * PAD
    y, x = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = size / 2.0
    half_width = np.minimum(60.0 + 0.8 * np.abs(y - cy), 300.0)
    erf = np.vectorize(math.erf, otypes=[np.float32])
    device = 0.5 * (1.0 + erf((half_width - np.abs(x - cx)) / 1.5))
    sample = 6000.0 + 6000.0 * device
    particles = np.zeros((size, size), np.float32)
    for _ in range(1500):
        px, py = rng.uniform(0, size, 2)
        sigma = rng.uniform(1.2, 2.5)
        amplitude = rng.choice([-1.0, 0.6]) * rng.uniform(800, 3000)
        r = int(4 * sigma) + 1
        x0, x1 = max(0, int(px) - r), min(size, int(px) + r + 1)
        y0, y1 = max(0, int(py) - r), min(size, int(py) + r + 1)
        xx, yy = np.meshgrid(np.arange(x0, x1), np.arange(y0, y1))
        particles[y0:y1, x0:x1] += amplitude * np.exp(-((xx - px) ** 2 + (yy - py) ** 2) / (2 * sigma ** 2))
    sample += particles

    def domains(seed):
        field = np.random.default_rng(seed).normal(0, 1, (size, size)).astype(np.float32)
        field = cv2.GaussianBlur(field, (0, 0), 40)
        return 180.0 * np.tanh(field / (0.25 * float(np.std(field)))) * device

    centre = ((np.abs(x - cx) < 300) & (np.abs(y - cy) < 300)).astype(np.float32)
    before, after = domains(11), domains(12)
    return sample + before, sample + before * (1 - centre) + after * centre


def shifted(image, dx, dy):
    """The image moved by (dx, dy): Fourier shift (the scene is band-limited)."""
    fy = np.fft.fftfreq(image.shape[0])[:, None]
    fx = np.fft.fftfreq(image.shape[1])[None, :]
    spectrum = np.fft.fft2(image) * np.exp(-2j * math.pi * (fx * dx + fy * dy))
    return np.fft.ifft2(spectrum).real.astype(np.float32)


def camera(sample, rng):
    y, x = np.mgrid[0:N, 0:N].astype(np.float32)
    vignetting = 1.0 - 0.25 * (((x - N / 2) ** 2 + (y - N / 2) ** 2) / (N / 2) ** 2)
    signal = sample[PAD:PAD + N, PAD:PAD + N] * vignetting
    noisy = signal + rng.normal(0, 1, signal.shape).astype(np.float32) * np.sqrt(np.maximum(signal, 1))
    return np.clip(noisy, 0, 65535).astype(np.uint16), vignetting


def timed(function, *args, repeats=5):
    function(*args)
    start = time.perf_counter()
    for _ in range(repeats):
        result = function(*args)
    return result, (time.perf_counter() - start) / repeats * 1000.0


def main():
    rng = np.random.default_rng(3)
    before, after = sample_scene(rng)
    background, vignetting = camera(before, rng)
    prepared, prepare_ms = timed(app.DriftBackground, background, repeats=1)
    print(f"background prepared in {prepare_ms:.0f} ms; fine window {prepared.fine.size if prepared.fine else None}")
    worst_old = worst_new = 0.0
    for true_dx, true_dy in SHIFTS:
        frame, _ = camera(shifted(after, true_dx, true_dy), rng)
        (old_dx, old_dy, old_r), old_ms = timed(old_estimate, background, frame)
        (new_dx, new_dy, new_m), new_ms = timed(app.estimate_drift_shift, prepared, frame)
        old_error = math.hypot(old_dx - true_dx, old_dy - true_dy)
        new_error = math.hypot(new_dx - true_dx, new_dy - true_dy)
        worst_old, worst_new = max(worst_old, old_error), max(worst_new, new_error)
        print(f"true ({true_dx:+6.2f},{true_dy:+6.2f})  old ({old_dx:+7.3f},{old_dy:+7.3f}) err {old_error:5.3f} "
              f"resp {old_r:.3f} {old_ms:5.1f} ms | new ({new_dx:+7.3f},{new_dy:+7.3f}) err {new_error:5.3f} "
              f"match {new_m:.3f} {new_ms:5.1f} ms")
        if (true_dx, true_dy) == (1.37, -2.81):
            # The residual of the shift itself, at the true shift: outside the
            # changed centre and 100 px in from the edges.
            reference = background.astype(np.float32)
            keep = np.ones(frame.shape, bool)
            keep[:100], keep[-100:], keep[:, :100], keep[:, -100:] = False, False, False, False
            keep[N // 2 - 420:N // 2 + 420, N // 2 - 420:N // 2 + 420] = False
            for name, flag in (("bilinear", cv2.INTER_LINEAR), ("cubic", cv2.INTER_CUBIC)):
                matrix = np.array([[1, 0, -true_dx], [0, 1, -true_dy]], np.float32)
                aligned = cv2.warpAffine(frame.astype(np.float32), matrix, (N, N), flags=flag,
                                         borderMode=cv2.BORDER_REFLECT)
                rms = float(np.sqrt(np.mean((aligned - reference)[keep] ** 2)))
                binned = cv2.blur(aligned - reference, (4, 4))
                print(f"  {name:8s} shift: residual RMS {rms:6.1f} counts (4x4-averaged "
                      f"{float(np.sqrt(np.mean(binned[keep] ** 2))):5.1f})")
    print(f"WORST ERROR old {worst_old:.3f} px, new {worst_new:.3f} px")


if __name__ == "__main__":
    main()
