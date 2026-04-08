import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

logger = logging.getLogger(__name__)


def color_correct(
    input_path: Path,
    output_path: Path,
    brightness: float = 1.20,
    contrast: float = 1.10,
    saturation: float = 1.00,
) -> Path:
    """Apply color correction to match style guide targets."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as img:
        img = img.convert("RGB")

        img = ImageEnhance.Brightness(img).enhance(brightness)
        img = ImageEnhance.Contrast(img).enhance(contrast)
        img = ImageEnhance.Color(img).enhance(saturation)

        # Prevent blown highlights
        arr = np.array(img)
        arr = np.clip(arr, 0, 250)
        img = Image.fromarray(arr.astype(np.uint8))

        img.save(output_path)
        logger.info(
            f"Color corrected {input_path.name} "
            f"(brightness={brightness}, contrast={contrast}, saturation={saturation})"
        )

    return output_path
