import re
from pathlib import Path

# ─── Media library naming convention ────────────────────────────────────────
# Pattern: {niche}_{use}_{subtype}_{seq:03d}.jpg
# Example: hvac_ba_together_001.jpg
#
# Niche codes:   hvac | bathroom | shower | flooring | siding | security
# Use codes:     hero | ba | project
# Subtypes:
#   hero    → worker | result | product | team
#   ba      → together | before | after | story | process
#   project → wide | detail | process
# ─────────────────────────────────────────────────────────────────────────────

VALID_NICHES = {"hvac", "bathroom", "shower", "flooring", "siding", "security"}
VALID_USES = {"hero", "ba", "project"}
VALID_SUBTYPES = {
    "hero": {"worker", "result", "product", "team"},
    "ba": {"together", "before", "after", "story", "process"},
    "project": {"wide", "detail", "process"},
}


def build_media_filename(
    niche: str,
    use: str,
    subtype: str,
    seq: int,
    ext: str = "jpg",
) -> str:
    """Build canonical media filename: hvac_ba_together_001.jpg"""
    return f"{niche}_{use}_{subtype}_{seq:03d}.{ext}"


def parse_media_filename(filename: str) -> dict:
    """
    Parse a canonical media filename back to components.
    Raises ValueError if format doesn't match.
    """
    name = Path(filename).stem
    ext = Path(filename).suffix.lstrip(".")
    parts = name.split("_")
    if len(parts) != 4:
        raise ValueError(f"Expected niche_use_subtype_seq, got: {filename}")
    niche, use, subtype, seq_str = parts
    return {
        "niche": niche,
        "use": use,
        "subtype": subtype,
        "seq": int(seq_str),
        "ext": ext,
    }


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
