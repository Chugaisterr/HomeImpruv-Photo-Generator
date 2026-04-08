"""
Main pipeline: runs raw → clean → upscaled → corrected → final
"""
import logging
from pathlib import Path

from .text_remove import remove_text
from .upscale import upscale
from .color_correct import color_correct
from .text_overlay import apply_overlay

logger = logging.getLogger(__name__)

STEPS = ["text_remove", "upscale", "color_correct", "text_overlay"]


def run_pipeline(
    input_path: Path,
    output_dir: Path,
    steps: list[str] = None,
    template: str = None,
    overlay_text: str = None,
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
        out = output_dir / "corrected" / current.name.replace("--upscaled.", "--corrected.")
        current = color_correct(current, out)
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
    """Run pipeline on all images in a directory."""
    images = list(input_dir.glob("*.jpg")) + list(input_dir.glob("*.png")) + list(input_dir.glob("*.webp"))
    results = []
    for img in images:
        logger.info(f"Processing: {img.name}")
        result = run_pipeline(img, output_dir, steps, template)
        results.append({"input": img, **result})
    return results
