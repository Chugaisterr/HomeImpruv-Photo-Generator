import re
from pathlib import Path


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^\w-]", "", text)
    text = re.sub(r"-+", "-", text)
    return text


def build_filename(
    category: str,
    subfolder: str,
    descriptor: str,
    version: int,
    stage: str,
    ext: str = "jpg",
) -> str:
    return f"{category}--{subfolder}--{slugify(descriptor)}--v{version}--{stage}.{ext}"


def parse_filename(filename: str) -> dict:
    name = Path(filename).stem
    ext = Path(filename).suffix.lstrip(".")
    parts = name.split("--")
    if len(parts) != 5:
        raise ValueError(f"Invalid filename format: {filename}")
    category, subfolder, descriptor, version, stage = parts
    return {
        "category": category,
        "subfolder": subfolder,
        "descriptor": descriptor,
        "version": int(version.lstrip("v")),
        "stage": stage,
        "ext": ext,
    }


def next_stage_path(current_path: Path, new_stage: str) -> Path:
    parts = parse_filename(current_path.name)
    parts["stage"] = new_stage
    new_name = build_filename(
        parts["category"],
        parts["subfolder"],
        parts["descriptor"],
        parts["version"],
        new_stage,
        parts["ext"],
    )
    return current_path.parent.parent / new_stage / new_name
