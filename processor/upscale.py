import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

MIN_RESOLUTION = 1920


def needs_upscale(image_path: Path) -> bool:
    with Image.open(image_path) as img:
        return min(img.size) < MIN_RESOLUTION


def upscale(input_path: Path, output_path: Path, scale: int = 2) -> Path:
    """Upscale image using high-quality Lanczos resampling."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as img:
        w, h = img.size

        if min(w, h) >= MIN_RESOLUTION:
            logger.info(f"Resolution sufficient ({w}x{h}), skipping: {input_path.name}")
            img.save(output_path)
            return output_path

        new_w, new_h = w * scale, h * scale
        upscaled = img.resize((new_w, new_h), Image.LANCZOS)
        upscaled.save(output_path)
        logger.info(f"Upscaled {input_path.name}: {w}x{h} → {new_w}x{new_h}")

    return output_path
