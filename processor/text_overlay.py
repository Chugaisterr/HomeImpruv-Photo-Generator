import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
FONTS_DIR = Path(__file__).parent.parent / "fonts"


def load_template(template_name: str) -> dict:
    path = TEMPLATES_DIR / f"{template_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {template_name}")
    with open(path) as f:
        return json.load(f)


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    font_path = FONTS_DIR / "primary" / "Inter-Bold.ttf"
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default()


def hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple:
    hex_color = hex_color.lstrip("#")
    r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return (r, g, b, alpha)


def draw_label(draw: ImageDraw.Draw, text: str, position: str, img_size: tuple, config: dict):
    w, h = img_size
    font_size = config.get("font_size", 48)
    padding = config.get("padding", 16)
    font = get_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    positions = {
        "top-left": (padding, padding),
        "top-right": (w - text_w - padding * 2, padding),
        "bottom-left": (padding, h - text_h - padding * 2),
        "bottom-center": ((w - text_w) // 2, h - text_h - padding * 2),
        "top-center": ((w - text_w) // 2, padding),
    }
    x, y = positions.get(position, (padding, padding))

    bg_alpha = int(config.get("bg_opacity", 1.0) * 255)
    bg_color = hex_to_rgba(config.get("bg_color", "#000000"), bg_alpha)
    text_color = hex_to_rgba(config.get("text_color", "#FFFFFF"))

    draw.rectangle([x - padding, y - padding, x + text_w + padding, y + text_h + padding], fill=bg_color)
    draw.text((x, y), text, font=font, fill=text_color)


def apply_overlay(input_path: Path, output_path: Path, template_name: str, custom_text: str = None) -> Path:
    """Apply text overlay template to image."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    template = load_template(template_name)

    with Image.open(input_path).convert("RGBA") as img:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        if template["type"] == "split":
            for side, cfg in template["labels"].items():
                text = custom_text or cfg["text"]
                draw_label(draw, text, cfg["position"], img.size, cfg)

        elif template["type"] == "overlay":
            text = custom_text or template.get("default_text", "")
            draw_label(draw, text, template["position"], img.size, template)

        combined = Image.alpha_composite(img, overlay).convert("RGB")
        combined.save(output_path)
        logger.info(f"Applied '{template_name}' overlay to {input_path.name}")

    return output_path
