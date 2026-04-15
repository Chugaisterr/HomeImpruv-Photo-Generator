"""
Dataset Builder — assembles dataset.json + dataset.csv from:
  - classifications.json  (AI labels + captions from 'classify' command)
  - Media/ folder structure (ground truth for niche/use/subtype)

Caption source priority:
  1. classifications.json["caption"]  — AI-generated (from classify command)
  2. Empty string                     — not yet classified, fill later

Run: python tools/build_dataset_local.py
"""
import csv
import json
import random
from collections import Counter
from datetime import date
from pathlib import Path

from PIL import Image

MEDIA_DIR   = Path("Media")
OUTPUT_JSON = Path("dataset.json")
OUTPUT_CSV  = Path("dataset.csv")
CLS_FILE    = Path("classifications.json")

TRAIN_RATIO = 0.80
VAL_RATIO   = 0.10

# Tags auto-generated from niche + use + subtype (still useful for search/filtering)
NICHE_TAGS = {
    "bathroom": ["bathroom", "renovation", "tile", "fixtures", "remodeling", "interior"],
    "shower":   ["shower", "tile", "glass", "plumbing", "wet area", "renovation"],
    "flooring": ["flooring", "hardwood", "tile", "installation", "floor renovation"],
    "hvac":     ["hvac", "air conditioning", "heating", "installation", "equipment", "ductwork"],
    "siding":   ["siding", "exterior", "house", "cladding", "curb appeal"],
    "security": ["security", "camera", "surveillance", "installation", "smart home"],
    "general":  ["home improvement", "renovation", "contractor"],
}
USE_TAGS = {
    "ba":      ["before after", "comparison", "transformation"],
    "hero":    ["finished result", "showcase", "portfolio"],
    "project": ["work in progress", "installation process"],
}
SUBTYPE_TAGS = {
    "before":   ["before renovation", "original state"],
    "after":    ["after renovation", "completed work"],
    "together": ["before after side by side", "split view"],
    "result":   ["finished result", "completed project"],
    "process":  ["installation in progress", "work in progress"],
    "wide":     ["full room view", "wide angle"],
    "detail":   ["close-up", "detail shot"],
    "worker":   ["contractor", "professional worker"],
    "story":    ["social story format", "vertical"],
    "team":     ["crew", "team photo"],
    "product":  ["product shot", "equipment close-up"],
}


def get_tags(niche, use, subtype, has_person=False):
    tags = []
    tags += NICHE_TAGS.get(niche, ["home improvement"])
    tags += USE_TAGS.get(use, [])
    tags += SUBTYPE_TAGS.get(subtype, [])
    tags += ["contractor portfolio", "home renovation"]
    if has_person:
        tags.append("worker shown")
    # deduplicate preserving order
    seen, unique = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:12]


def get_dims(path):
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return 0, 0


def assign_splits_stratified(images_by_niche: dict, seed=42) -> dict:
    """Assign train/val/test splits stratified per niche for balanced distribution."""
    rng = random.Random(seed)
    split_map = {}
    for niche, paths in images_by_niche.items():
        shuffled = list(paths)
        rng.shuffle(shuffled)
        n = len(shuffled)
        n_val  = max(1, round(n * VAL_RATIO))
        n_test = max(1, round(n * (1 - TRAIN_RATIO - VAL_RATIO)))
        for i, p in enumerate(shuffled):
            if i < n_test:
                split_map[str(p)] = "test"
            elif i < n_test + n_val:
                split_map[str(p)] = "val"
            else:
                split_map[str(p)] = "train"
    return split_map


def load_classifications() -> dict:
    """Load classifications.json, key by filename."""
    if not CLS_FILE.exists():
        return {}
    with open(CLS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    by_name = {}
    for entry in data:
        fname = Path(entry.get("file", "")).name
        if fname:
            by_name[fname] = entry
    return by_name


def infer_from_path(path: Path):
    """Infer niche/use/subtype from folder structure."""
    parts = [p.lower() for p in path.parts]
    niches   = {"bathroom", "shower", "flooring", "hvac", "siding", "security"}
    uses     = {"ba", "hero", "project"}
    subtypes = {"before", "after", "together", "result", "process",
                "wide", "detail", "worker", "story", "team", "product"}

    niche   = next((p for p in parts if p in niches), "general")
    use     = next((p for p in parts if p in uses), "hero")
    stem    = path.stem.lower()
    subtype = next((s for s in subtypes if s in stem), "result")
    return niche, use, subtype


def build():
    print("\nBuilding dataset from local data...")

    images = sorted(
        p for p in MEDIA_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    total = len(images)
    print(f"Found {total} images")

    cls_by_name = load_classifications()
    ai_captions = sum(1 for e in cls_by_name.values() if e.get("caption"))
    print(f"Classifications loaded: {len(cls_by_name)} entries, {ai_captions} with AI captions")

    # Group by niche for stratified split
    images_by_niche: dict[str, list] = {}
    for path in images:
        niche_p, _, _ = infer_from_path(path.relative_to(MEDIA_DIR))
        cls_row = cls_by_name.get(path.name, {})
        niche = cls_row.get("niche") or niche_p
        images_by_niche.setdefault(niche, []).append(path)

    split_map = assign_splits_stratified(images_by_niche)

    dataset_images = []
    for idx, path in enumerate(images):
        rel_path = str(path.relative_to(MEDIA_DIR)).replace("\\", "/")
        name = path.name
        cls_row = cls_by_name.get(name, {})

        # Infer from path as fallback
        niche_p, use_p, subtype_p = infer_from_path(path.relative_to(MEDIA_DIR))

        niche   = cls_row.get("niche")   or niche_p
        use     = cls_row.get("use")     or use_p
        subtype = cls_row.get("subtype") or subtype_p
        quality = int(cls_row.get("quality_score") or 7)

        has_text   = bool(cls_row.get("has_text_overlay", False))
        has_person = bool(cls_row.get("has_person", False))

        # Normalize values
        if use in ("b/a", "before_after", "before-after"):
            use = "ba"
        niche   = (niche   or "general").lower().strip()
        use     = (use     or "hero").lower().strip()
        subtype = (subtype or "result").lower().strip()

        # Dimensions — prefer pre-computed from classifications, fallback to reading file
        w = int(cls_row.get("width") or 0)
        h = int(cls_row.get("height") or 0)
        if not w or not h:
            w, h = get_dims(path)
        mp = round(w * h / 1_000_000, 2) if w and h else 0.0
        needs_upscale = (min(w, h) < 1920) if w and h else False
        kb = path.stat().st_size // 1024

        # Caption: AI-generated if available, else empty (fill later)
        caption = cls_row.get("caption") or ""

        tags = get_tags(niche, use, subtype, has_person)

        dataset_images.append({
            "id":             idx + 1,
            "path":           rel_path,
            "niche":          niche,
            "use":            use,
            "subtype":        subtype,
            "quality":        quality,
            "width":          w,
            "height":         h,
            "megapixels":     mp,
            "size_kb":        kb,
            "needs_upscale":  needs_upscale,
            "has_text_overlay": has_text,
            "has_person":     has_person,
            "tags":           tags,
            "caption":        caption,
            "split":          split_map.get(str(path), "train"),
        })

    # ── Stats ──────────────────────────────────────────────────────────────
    niche_c   = Counter(e["niche"]   for e in dataset_images)
    use_c     = Counter(e["use"]     for e in dataset_images)
    subtype_c = Counter(e["subtype"] for e in dataset_images)
    split_c   = Counter(e["split"]   for e in dataset_images)
    q_avg     = sum(e["quality"] for e in dataset_images) / len(dataset_images)
    q_high    = sum(1 for e in dataset_images if e["quality"] >= 7)
    q_low     = sum(1 for e in dataset_images if e["quality"] < 5)
    has_dims  = sum(1 for e in dataset_images if e["width"] > 0)
    avg_w     = sum(e["width"]  for e in dataset_images if e["width"]  > 0) // max(has_dims, 1)
    avg_h     = sum(e["height"] for e in dataset_images if e["height"] > 0) // max(has_dims, 1)
    with_caption = sum(1 for e in dataset_images if e["caption"])
    need_upscale = sum(1 for e in dataset_images if e["needs_upscale"])

    dataset = {
        "version": "1.1",
        "created": str(date.today()),
        "total":   total,
        "stats": {
            "niches":             dict(niche_c.most_common()),
            "use_types":          dict(use_c.most_common()),
            "subtypes":           dict(subtype_c.most_common()),
            "splits":             dict(split_c),
            "avg_quality":        round(q_avg, 2),
            "high_quality_count": q_high,
            "low_quality_count":  q_low,
            "avg_resolution":     f"{avg_w}x{avg_h}",
            "with_caption":       with_caption,
            "needs_upscale":      need_upscale,
            "has_text_overlay":   sum(1 for e in dataset_images if e["has_text_overlay"]),
            "has_person":         sum(1 for e in dataset_images if e["has_person"]),
        },
        "taxonomy": {
            "niches": {
                "bathroom": "Bathroom renovation — tiles, vanity, tub",
                "shower":   "Walk-in shower conversion and remodel",
                "flooring": "Floor installation — hardwood, tile, laminate",
                "hvac":     "HVAC / air conditioning installation",
                "siding":   "Exterior siding replacement",
                "security": "Home security camera installation",
            },
            "use_types": {
                "ba":      "Before/after comparison (paired or split frame)",
                "hero":    "Finished showcase — hero page or portfolio",
                "project": "Work in progress / installation process",
            },
            "subtypes": {
                "together": "Before + after in single frame (split view)",
                "before":   "Before state only",
                "after":    "After state only",
                "story":    "Vertical / social story format (9:16)",
                "process":  "Mid-installation, work in progress",
                "result":   "Completed project, no people",
                "worker":   "Technician actively working",
                "product":  "Equipment or product close-up",
                "team":     "Crew or team group shot",
                "wide":     "Full room / area overview",
                "detail":   "Close-up of workmanship or material",
            },
        },
        "images": dataset_images,
    }

    # ── Save JSON ──────────────────────────────────────────────────────────
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    # ── Save CSV ───────────────────────────────────────────────────────────
    fields = ["id", "path", "niche", "use", "subtype", "quality",
              "width", "height", "megapixels", "size_kb",
              "needs_upscale", "has_text_overlay", "has_person",
              "tags", "caption", "split"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for e in dataset_images:
            row = {k: e[k] for k in fields}
            row["tags"] = "|".join(e["tags"])
            writer.writerow(row)

    # ── Report ─────────────────────────────────────────────────────────────
    print(f"\n{'='*58}")
    print(f"  DATASET REPORT — HomeIQ Media Library v1.1")
    print(f"{'='*58}")
    print(f"  Total images     : {total}")
    caption_note = "OK" if with_caption == total else "<- run classify to fill"
    print(f"  With AI caption  : {with_caption} / {total}  {caption_note}")
    print(f"  Needs upscale    : {need_upscale}")
    print(f"  Avg quality      : {q_avg:.1f}/10  (high={q_high}, low={q_low})")
    print(f"  Avg resolution   : {avg_w}x{avg_h}px")
    print(f"\n  SPLITS  (stratified per niche)")
    for s in ["train", "val", "test"]:
        c = split_c.get(s, 0)
        print(f"    {s:5} : {c:4}  ({c/total*100:.0f}%)")
    print(f"\n  BY NICHE")
    for n, c in niche_c.most_common():
        bar = "#" * (c // 3)
        print(f"    {n:12} : {c:4}  {bar}")
    print(f"\n  BY USE TYPE")
    labels = {"ba": "Before/After", "hero": "Hero/Showcase", "project": "Project/Process"}
    for u, c in use_c.most_common():
        print(f"    {labels.get(u, u):22} : {c:4}")
    print(f"\n  OUTPUT")
    print(f"    {OUTPUT_JSON}  ({OUTPUT_JSON.stat().st_size // 1024} KB)")
    print(f"    {OUTPUT_CSV}  ({OUTPUT_CSV.stat().st_size // 1024} KB)")
    print(f"{'='*58}")


if __name__ == "__main__":
    build()
