"""
Style normalization via automatic reference selection + histogram matching.

Pipeline:
1. Analyze all images in batch → score each by quality metrics
2. Auto-select best image as reference (highest composite score)
3. Match all other images to reference histogram (per channel)
"""
import logging
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

def score_sharpness(img_bgr: np.ndarray) -> float:
    """Laplacian variance — higher = sharper."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def score_brightness(img_bgr: np.ndarray) -> float:
    """How close mean brightness is to ideal range (110-130% of 128)."""
    target_min, target_max = 128 * 1.10, 128 * 1.30
    mean_val = float(img_bgr.mean())
    if target_min <= mean_val <= target_max:
        return 1.0
    distance = min(abs(mean_val - target_min), abs(mean_val - target_max))
    return max(0.0, 1.0 - distance / 128.0)


def score_contrast(img_bgr: np.ndarray) -> float:
    """Normalized std deviation — higher = more contrast."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return float(gray.std()) / 128.0


def score_noise(img_bgr: np.ndarray) -> float:
    """Estimate noise level — lower noise = higher score."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    noise = float(np.std(gray - blurred))
    return max(0.0, 1.0 - noise / 30.0)


def score_color_temperature(img_bgr: np.ndarray) -> float:
    """
    Estimate color temperature proximity to 5500-6000K (warm-neutral).
    Uses R/B channel ratio as a proxy.
    Target ratio ≈ 1.0-1.15 (slightly warm).
    """
    b_mean = float(img_bgr[:, :, 0].mean()) + 1e-6
    r_mean = float(img_bgr[:, :, 2].mean()) + 1e-6
    ratio = r_mean / b_mean
    target_min, target_max = 1.0, 1.15
    if target_min <= ratio <= target_max:
        return 1.0
    distance = min(abs(ratio - target_min), abs(ratio - target_max))
    return max(0.0, 1.0 - distance / 0.5)


def compute_quality_score(image_path: Path) -> float:
    """Composite quality score (0-1). Higher = better reference candidate."""
    img = cv2.imread(str(image_path))
    if img is None:
        return 0.0

    weights = {
        "sharpness":    0.35,
        "brightness":   0.25,
        "contrast":     0.20,
        "noise":        0.10,
        "temperature":  0.10,
    }

    sharpness_raw = score_sharpness(img)
    # Normalize sharpness: typical range 0-2000, cap at 1000
    sharpness_norm = min(sharpness_raw / 1000.0, 1.0)

    scores = {
        "sharpness":   sharpness_norm,
        "brightness":  score_brightness(img),
        "contrast":    min(score_contrast(img), 1.0),
        "noise":       score_noise(img),
        "temperature": score_color_temperature(img),
    }

    composite = sum(scores[k] * weights[k] for k in weights)
    logger.debug(f"{image_path.name} scores: {scores} → composite={composite:.3f}")
    return composite


def select_reference(image_paths: list[Path]) -> Path:
    """Automatically select the best image as style reference."""
    scored = [(p, compute_quality_score(p)) for p in image_paths]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_path, best_score = scored[0]
    logger.info(f"Auto-selected reference: {best_path.name} (score={best_score:.3f})")
    for p, s in scored[1:]:
        logger.info(f"  candidate: {p.name} (score={s:.3f})")
    return best_path


# ---------------------------------------------------------------------------
# Histogram matching
# ---------------------------------------------------------------------------

def match_histogram_channel(source: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Match histogram of one channel to reference channel."""
    src_flat = source.ravel()
    ref_flat = reference.ravel()

    src_hist, _ = np.histogram(src_flat, bins=256, range=(0, 256))
    ref_hist, _ = np.histogram(ref_flat, bins=256, range=(0, 256))

    src_cdf = src_hist.cumsum().astype(np.float64)
    ref_cdf = ref_hist.cumsum().astype(np.float64)

    src_cdf_norm = src_cdf / src_cdf[-1]
    ref_cdf_norm = ref_cdf / ref_cdf[-1]

    # Build lookup table: for each src value, find closest ref value
    lut = np.zeros(256, dtype=np.uint8)
    ref_idx = 0
    for src_val in range(256):
        while ref_idx < 255 and ref_cdf_norm[ref_idx] < src_cdf_norm[src_val]:
            ref_idx += 1
        lut[src_val] = ref_idx

    return lut[source]


def match_style(source_path: Path, reference_path: Path, output_path: Path) -> Path:
    """Match source image style to reference via per-channel histogram matching."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    src = cv2.imread(str(source_path))
    ref = cv2.imread(str(reference_path))

    if src is None or ref is None:
        raise ValueError(f"Cannot read images: {source_path}, {reference_path}")

    # Resize ref to same size if needed (for histogram only, size doesn't matter)
    result = np.zeros_like(src)
    for ch in range(3):
        result[:, :, ch] = match_histogram_channel(src[:, :, ch], ref[:, :, ch])

    # Soft blend: 80% matched + 20% original (preserves some natural look)
    blended = cv2.addWeighted(result, 0.80, src, 0.20, 0)

    # Prevent blown highlights
    blended = np.clip(blended, 0, 250)

    cv2.imwrite(str(output_path), blended)
    logger.info(f"Style matched: {source_path.name} → ref: {reference_path.name}")
    return output_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_batch_style(
    input_paths: list[Path],
    output_dir: Path,
    reference_path: Path = None,
) -> dict[Path, Path]:
    """
    Normalize style of all images to a common reference.

    Args:
        input_paths: list of source image paths
        output_dir: directory to save corrected images
        reference_path: optional manual reference; if None → auto-selected

    Returns:
        dict mapping input_path → output_path
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if reference_path is None:
        reference_path = select_reference(input_paths)

    results = {}
    for src_path in input_paths:
        out_name = src_path.name.replace("--upscaled.", "--corrected.").replace("--raw.", "--corrected.")
        out_path = output_dir / out_name

        if src_path == reference_path:
            # Reference copies as-is (it's already the ideal)
            import shutil
            shutil.copy2(src_path, out_path)
            logger.info(f"Reference copied as-is: {out_path.name}")
        else:
            match_style(src_path, reference_path, out_path)

        results[src_path] = out_path

    return results


def color_correct(
    input_path: Path,
    output_path: Path,
    reference_path: Path = None,
) -> Path:
    """
    Single-image style correction.
    If reference_path provided → histogram match to it.
    Otherwise → normalize to style guide targets via adaptive adjustment.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if reference_path:
        return match_style(input_path, reference_path, output_path)

    # Fallback: adaptive correction toward style guide targets
    img = cv2.imread(str(input_path))
    if img is None:
        raise ValueError(f"Cannot read image: {input_path}")

    mean_brightness = img.mean()
    target_brightness = 128 * 1.20

    alpha = target_brightness / (mean_brightness + 1e-6)
    alpha = np.clip(alpha, 0.8, 1.5)

    adjusted = np.clip(img.astype(np.float32) * alpha, 0, 250).astype(np.uint8)
    cv2.imwrite(str(output_path), adjusted)
    logger.info(f"Adaptive correction applied: {input_path.name} (alpha={alpha:.2f})")
    return output_path
