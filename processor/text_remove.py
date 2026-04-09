import logging
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def detect_text_regions(image_path: Path) -> list[tuple]:
    """Detect text regions using EasyOCR. Returns list of bounding boxes."""
    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        results = reader.readtext(str(image_path), detail=1)
        boxes = []
        for (bbox, text, confidence) in results:
            if confidence > 0.3:
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                boxes.append((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))))
        return boxes
    except ImportError:
        logger.warning("easyocr not installed, skipping text detection")
        return []


def inpaint_regions(image_path: Path, boxes: list[tuple], padding: int = 10) -> np.ndarray:
    """Inpaint detected text regions using OpenCV."""
    img = cv2.imread(str(image_path))
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    for (x1, y1, x2, y2) in boxes:
        x1 = max(0, x1 - padding)
        y1 = max(0, y1 - padding)
        x2 = min(w, x2 + padding)
        y2 = min(h, y2 + padding)
        mask[y1:y2, x1:x2] = 255

    return cv2.inpaint(img, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)


def remove_text(input_path: Path, output_path: Path) -> Path:
    """Remove text from image and save to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    boxes = detect_text_regions(input_path)

    if not boxes:
        logger.info(f"No text detected in {input_path.name}")
        img = Image.open(input_path)
        img.save(output_path)
        return output_path

    logger.info(f"Found {len(boxes)} text region(s) in {input_path.name}")
    result = inpaint_regions(input_path, boxes)
    cv2.imwrite(str(output_path), result)
    logger.info(f"Saved clean image: {output_path.name}")
    return output_path
