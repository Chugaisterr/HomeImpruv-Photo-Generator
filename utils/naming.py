"""
Media library naming convention.

Pattern: {niche}_{use}_{subtype}_{seq:03d}.jpg
Example: hvac_ba_together_001.jpg

Niches:   hvac | bathroom | shower | flooring | siding | security
Uses:     hero | ba | project
Subtypes:
  hero    -> worker | result | product | team
  ba      -> together | before | after | story | process
  project -> wide | detail | process
"""
from pathlib import Path

VALID_NICHES = {"hvac", "bathroom", "shower", "flooring", "siding", "security"}
VALID_USES = {"hero", "ba", "project"}
VALID_SUBTYPES = {
    "hero":    {"worker", "result", "product", "team"},
    "ba":      {"together", "before", "after", "story", "process"},
    "project": {"wide", "detail", "process"},
}


def build_media_filename(niche: str, use: str, subtype: str, seq: int, ext: str = "jpg") -> str:
    """Build canonical media filename: hvac_ba_together_001.jpg"""
    return f"{niche}_{use}_{subtype}_{seq:03d}.{ext}"


def parse_media_filename(filename: str) -> dict:
    """
    Parse canonical media filename back to components.
    Raises ValueError if format doesn't match.
    """
    name = Path(filename).stem
    ext  = Path(filename).suffix.lstrip(".")
    parts = name.split("_")
    if len(parts) != 4:
        raise ValueError(f"Expected niche_use_subtype_seq, got: {filename}")
    niche, use, subtype, seq_str = parts
    return {"niche": niche, "use": use, "subtype": subtype, "seq": int(seq_str), "ext": ext}
