"""Fix unknown niche/use in classifications.json using folder path as ground truth."""
import json
from pathlib import Path

CLS_FILE  = Path("classifications.json")
MEDIA_DIR = Path("Media")

NICHES   = {"bathroom", "shower", "flooring", "hvac", "siding", "security"}
USES     = {"ba", "hero", "project"}
SUBTYPES = {"before", "after", "together", "result", "process",
            "wide", "detail", "worker", "story", "team", "product"}

with open(CLS_FILE, encoding="utf-8") as f:
    data = json.load(f)

fixed = 0
for entry in data:
    if entry.get("niche") != "unknown" and entry.get("use") != "unknown":
        continue

    raw = entry.get("file", "").replace("\\", "/")
    file_path = Path(raw)
    parts = [p.lower() for p in file_path.parts]
    stem  = file_path.stem.lower()

    inferred_niche   = next((p for p in parts if p in NICHES), None)
    inferred_use     = next((p for p in parts if p in USES), None)
    inferred_subtype = next((s for s in SUBTYPES if s in stem), None)

    if inferred_niche and entry.get("niche") == "unknown":
        print(f"niche fix: {raw}  unknown -> {inferred_niche}")
        entry["niche"] = inferred_niche
        fixed += 1

    if inferred_use and entry.get("use") == "unknown":
        print(f"use fix:   {raw}  unknown -> {inferred_use}")
        entry["use"] = inferred_use
        fixed += 1

    if inferred_subtype and entry.get("subtype") == "unknown":
        entry["subtype"] = inferred_subtype

print(f"\nTotal fixes: {fixed}")

with open(CLS_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("Saved classifications.json")
