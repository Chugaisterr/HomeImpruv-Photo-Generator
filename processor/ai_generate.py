import logging
import os
from pathlib import Path
from typing import Optional

from clients.openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)


def generate_image(
    output_path: Path,
    prompt: str,
    reference_image: Optional[Path] = None,
    width: int = 1024,
    height: int = 768,
) -> Path:
    """Generate image via OpenRouter API."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    client = OpenRouterClient(api_key=api_key)
    try:
        image_bytes = client.generate(
            prompt=prompt,
            reference_image=reference_image,
            width=width,
            height=height,
        )
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        logger.info(f"Generated image saved: {output_path.name}")
    finally:
        client.close()

    return output_path
