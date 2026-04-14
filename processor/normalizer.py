"""
Gold standard image normalizer.

Target: 1920 × 1080 px (landscape 16:9)
Mode:   CONTAIN — image fits inside frame without cropping.
        Padding is filled with a smart blurred background from the image edges.
Quality: JPEG 92, convert everything to RGB.

Usage:
  python -m processor normalize --media-dir Media/flooring/hero
  python -m processor normalize --media-dir Media --output-suffix _norm
  python -m processor normalize --media-dir Media/hvac --width 1280 --height 720 --in-place
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tiff"}

# Default gold standard
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_QUALITY = 92


# ─────────────────────────────────────────────────────────────────────────────
# Core: smart background fill
# ─────────────────────────────────────────────────────────────────────────────

def _make_background(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Create a background frame for the image using blurred edge-fill.

    Strategy:
    1. Scale the image to COVER the target (filling it completely)
    2. Apply heavy Gaussian blur (radius ~40px) to create soft background
    3. The original image will be pasted on top of this background
    """
    # Scale to cover (slightly larger than target)
    scale = max(target_w / img.width, target_h / img.height)
    cover_w = int(img.width * scale)
    cover_h = int(img.height * scale)
    bg = img.resize((cover_w, cover_h), Image.LANCZOS)

    # Center crop to target size
    left = (cover_w - target_w) // 2
    top = (cover_h - target_h) // 2
    bg = bg.crop((left, top, left + target_w, top + target_h))

    # Heavy blur to make it look like background, not a duplicate
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))

    # Slightly darken background to contrast with the image
    bg_arr = np.array(bg, dtype=np.float32)
    bg_arr = bg_arr * 0.6  # darken 40%
    bg = Image.fromarray(bg_arr.clip(0, 255).astype(np.uint8))

    return bg


def normalize_image(
    source: Path,
    target: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    quality: int = DEFAULT_QUALITY,
) -> Path:
    """
    Normalize a single image to target dimensions with smart fill.

    Returns the target path.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as raw:
        img = raw.convert("RGB")

    src_w, src_h = img.size
    src_ratio = src_w / src_h
    tgt_ratio = width / height

    # ── Already correct size
    if src_w == width and src_h == height:
        img.save(target, format="JPEG", quality=quality)
        return target

    # ── Calculate CONTAIN dimensions (fit inside target, keep aspect ratio)
    if src_ratio > tgt_ratio:
        # Wider than target: fit by width
        fit_w = width
        fit_h = round(width / src_ratio)
    else:
        # Taller than target: fit by height
        fit_h = height
        fit_w = round(height * src_ratio)

    # Resize the image to fit dimensions
    img_resized = img.resize((fit_w, fit_h), Image.LANCZOS)

    # ── If image fits perfectly (no padding needed), just save
    if fit_w == width and fit_h == height:
        img_resized.save(target, format="JPEG", quality=quality)
        return target

    # ── Create blurred background
    background = _make_background(img, width, height)

    # ── Paste resized image centered on background
    paste_x = (width - fit_w) // 2
    paste_y = (height - fit_h) // 2
    background.paste(img_resized, (paste_x, paste_y))

    background.save(target, format="JPEG", quality=quality)
    return target


# ─────────────────────────────────────────────────────────────────────────────
# Batch normalization
# ─────────────────────────────────────────────────────────────────────────────

def collect_images(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        and "_norm" not in p.stem  # skip already normalized
    )


def normalize_batch(
    source_dir: Path,
    output_dir: Path | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    quality: int = DEFAULT_QUALITY,
    in_place: bool = False,
    output_suffix: str = "_norm",
    workers: int = 4,
) -> list[dict]:
    """
    Normalize all images in source_dir.

    - If in_place=True: overwrite originals (use with caution)
    - If output_dir is given: save to output_dir preserving structure
    - Otherwise: save next to original with output_suffix appended to stem
    """
    images = collect_images(source_dir)
    if not images:
        logger.warning(f"No images found in {source_dir}")
        return []

    logger.info(f"Normalizing {len(images)} images to {width}x{height} ...")

    def _process(path: Path) -> dict:
        if in_place:
            dest = path.with_suffix(".jpg")
        elif output_dir:
            rel = path.relative_to(source_dir)
            dest = output_dir / rel.with_suffix(".jpg")
        else:
            dest = path.parent / (path.stem + output_suffix + ".jpg")

        try:
            normalize_image(path, dest, width, height, quality)
            return {"source": str(path), "target": str(dest), "status": "ok"}
        except Exception as e:
            logger.error(f"Failed {path.name}: {e}")
            return {"source": str(path), "target": str(dest), "status": "error", "error": str(e)}

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, p): p for p in images}
        done = 0
        for future in as_completed(futures):
            r = future.result()
            done += 1
            status = "OK" if r["status"] == "ok" else "ERR"
            src = Path(r["source"])
            logger.info(f"  [{done}/{len(images)}] {status} {src.name}")
            results.append(r)

    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "error")
    logger.info(f"Done: {ok} normalized, {err} errors")
    return results
