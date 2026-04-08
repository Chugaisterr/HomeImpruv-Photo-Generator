"""
Main pipeline: runs raw → clean → upscaled → corrected → final

Style correction uses auto-reference selection:
  - In batch mode: agent scores all images, picks best as reference,
    then histogram-matches all others to it.
  - In single mode: adaptive correction toward style guide targets.
"""
import logging
from pathlib import Path

from .text_remove import remove_text
from .upscale import upscale
from .color_correct import color_correct, normalize_batch_style, select_reference
from .text_overlay import apply_overlay

logger = logging.getLogger(__name__)

STEPS = ["text_remove", "upscale", "color_correct", "text_overlay"]
IMAGE_EXTS = ("*.jpg", "*.jpeg", "*.png", "*.webp")


def _collect_images(directory: Path) -> list[Path]:
    images = []
    for ext in IMAGE_EXTS:
        images.extend(directory.glob(ext))
    return sorted(images)


def run_pipeline(
    input_path: Path,
    output_dir: Path,
    steps: list[str] = None,
    template: str = None,
    overlay_text: str = None,
    reference_path: Path = None,
) -> dict[str, Path]:
    """Run full or partial processing pipeline on a single image."""
    if steps is None:
        steps = STEPS

    results = {}
    current = input_path

    if "text_remove" in steps:
        out = output_dir / "clean" / input_path.name.replace("--raw.", "--clean.")
        current = remove_text(current, out)
        results["clean"] = current

    if "upscale" in steps:
        out = output_dir / "upscaled" / current.name.replace("--clean.", "--upscaled.")
        current = upscale(current, out)
        results["upscaled"] = current

    if "color_correct" in steps:
        out = output_dir / "corrected" / current.name.replace("--upscaled.", "--corrected.").replace("--raw.", "--corrected.")
        current = color_correct(current, out, reference_path=reference_path)
        results["corrected"] = current

    if "text_overlay" in steps and template:
        out = output_dir / "final" / current.name.replace("--corrected.", "--final.")
        current = apply_overlay(current, out, template, overlay_text)
        results["final"] = current

    return results


def run_batch(
    input_dir: Path,
    output_dir: Path,
    steps: list[str] = None,
    template: str = None,
) -> list[dict]:
    """
    Run pipeline on all images in a directory.

    Style correction strategy:
      1. Run text_remove + upscale on all images first.
      2. Agent auto-selects best upscaled image as style reference.
      3. Histogram-match all images to the reference.
      4. Apply text overlay if template provided.
    """
    if steps is None:
        steps = STEPS

    images = _collect_images(input_dir)
    if not images:
        logger.warning(f"No images found in {input_dir}")
        return []

    logger.info(f"Found {len(images)} image(s) in {input_dir}")

    # Phase 1: text removal + upscale for each image
    phase1_results = []
    for img_path in images:
        logger.info(f"Phase 1 — {img_path.name}")
        partial = run_pipeline(img_path, output_dir, steps=["text_remove", "upscale"])
        last = partial.get("upscaled") or partial.get("clean") or img_path
        phase1_results.append({"input": img_path, "phase1": last, **partial})

    # Phase 2: auto-select reference + batch style normalization
    if "color_correct" in steps:
        phase1_paths = [r["phase1"] for r in phase1_results]
        logger.info("Phase 2 — Auto-selecting style reference...")
        reference = select_reference(phase1_paths)
        logger.info(f"Reference selected: {reference.name}")

        corrected_dir = output_dir / "corrected"
        corrected_map = normalize_batch_style(
            input_paths=phase1_paths,
            output_dir=corrected_dir,
            reference_path=reference,
        )
        for r in phase1_results:
            r["corrected"] = corrected_map.get(r["phase1"])
            r["reference"] = reference

    # Phase 3: text overlay
    if "text_overlay" in steps and template:
        for r in phase1_results:
            src = r.get("corrected") or r.get("phase1")
            if src:
                out = output_dir / "final" / src.name.replace("--corrected.", "--final.").replace("--upscaled.", "--final.")
                r["final"] = apply_overlay(src, out, template)

    logger.info(f"Batch complete. Processed {len(phase1_results)} image(s).")
    return phase1_results
