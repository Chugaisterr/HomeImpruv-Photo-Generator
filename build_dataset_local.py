"""
Dataset Builder (local, no API) — uses existing classifications.json + folder structure.
Generates dataset.json + dataset.csv ready for ML training.

Run: python build_dataset_local.py
"""
import csv
import json
import random
from collections import Counter
from pathlib import Path

from PIL import Image

MEDIA_DIR   = Path("Media")
OUTPUT_JSON = Path("dataset.json")
OUTPUT_CSV  = Path("dataset.csv")
CLS_FILE    = Path("classifications.json")
RENAME_LOG  = Path("Media/rename_log.csv")

TRAIN_RATIO = 0.80
VAL_RATIO   = 0.10

# Tags auto-generated from niche + use + subtype
NICHE_TAGS = {
    "bathroom": ["bathroom", "renovation", "tile", "fixtures", "remodeling", "interior"],
    "shower":   ["shower", "tile", "glass", "plumbing", "wet area", "renovation"],
    "flooring": ["flooring", "hardwood", "tile", "installation", "floor renovation"],
    "hvac":     ["hvac", "air conditioning", "heating", "installation", "equipment", "ductwork"],
    "siding":   ["siding", "exterior", "house", "cladding", "james hardie", "curb appeal"],
    "security": ["security", "camera", "surveillance", "installation", "smart home"],
    "general":  ["home improvement", "renovation", "contractor", "construction"],
}
USE_TAGS = {
    "ba":      ["before after", "comparison", "transformation", "renovation result"],
    "hero":    ["finished result", "showcase", "professional photo", "portfolio"],
    "project": ["work in progress", "installation process", "contractor work"],
}
SUBTYPE_TAGS = {
    "before":   ["before renovation", "original state"],
    "after":    ["after renovation", "completed work"],
    "together": ["before after side by side", "split view"],
    "result":   ["finished result", "completed project"],
    "process":  ["installation process", "work in progress"],
    "wide":     ["wide angle", "full room view"],
    "detail":   ["close-up", "detail shot"],
    "worker":   ["contractor", "professional worker"],
    "story":    ["project story", "progression"],
    "hero":     ["hero shot", "showcase"],
}

CAPTIONS = {
    ("bathroom", "ba", "together"): "Professional bathroom renovation before and after comparison showing tile and fixture transformation.",
    ("bathroom", "ba", "before"):   "Bathroom before renovation showing original fixtures and tile condition.",
    ("bathroom", "ba", "after"):    "Completed bathroom renovation with new tile, fixtures, and modern finishes.",
    ("bathroom", "hero", "result"): "Professionally renovated bathroom showcasing high-quality tile work and modern fixtures.",
    ("bathroom", "project", "process"): "Bathroom renovation in progress showing tile installation and construction work.",
    ("shower", "ba", "together"):   "Shower remodel before and after comparison highlighting tile and glass transformation.",
    ("shower", "hero", "result"):   "Completed shower renovation featuring premium tile work and modern fixtures.",
    ("flooring", "ba", "together"): "Flooring renovation before and after comparison showing new hardwood or tile installation.",
    ("flooring", "hero", "result"): "Professionally installed flooring showcasing material quality and clean finish.",
    ("flooring", "project", "wide"): "Flooring installation in progress showing contractor work and material layout.",
    ("hvac", "ba", "together"):     "HVAC system installation before and after showing equipment and ductwork.",
    ("hvac", "hero", "result"):     "Professionally installed HVAC system showcasing clean equipment placement.",
    ("hvac", "project", "process"): "HVAC installation process showing equipment mounting and connection work.",
    ("siding", "ba", "together"):   "Exterior siding replacement before and after comparison showing curb appeal transformation.",
    ("siding", "hero", "result"):   "Completed James Hardie siding installation showcasing clean exterior finish.",
    ("security", "hero", "result"): "Professional security camera installation showing equipment placement and mounting.",
    ("security", "project", "process"): "Security system installation process showing camera mounting and wiring.",
}

def get_caption(niche, use, subtype):
    key = (niche, use, subtype)
    if key in CAPTIONS: return CAPTIONS[key]
    # fallback
    use_label = {"ba": "before and after", "hero": "showcase", "project": "installation process"}.get(use, use)
    return f"Professional {niche} {use_label} photo for contractor portfolio."

def get_tags(niche, use, subtype, has_text=False, has_person=False):
    tags = []
    tags += NICHE_TAGS.get(niche, ["home improvement"])
    tags += USE_TAGS.get(use, [])
    tags += SUBTYPE_TAGS.get(subtype, [])
    tags += ["contractor portfolio", "home renovation", "professional photo"]
    if has_person: tags.append("worker shown")
    if not has_text: tags.append("clean photo")
    # deduplicate
    seen, unique = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t); unique.append(t)
    return unique[:14]

def get_dims(path):
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except: return 0, 0

def assign_split(idx, total):
    r = idx / total
    if r < TRAIN_RATIO: return "train"
    if r < TRAIN_RATIO + VAL_RATIO: return "val"
    return "test"

def load_classifications():
    """Load existing classifications, key by filename."""
    if not CLS_FILE.exists(): return {}
    with open(CLS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    by_name = {}
    for entry in data:
        fname = Path(entry.get("file","")).name
        if fname: by_name[fname] = entry
    return by_name

def load_rename_log():
    """Map original filename → target path + metadata."""
    if not RENAME_LOG.exists(): return {}
    import csv as csv_mod
    log = {}
    with open(RENAME_LOG, encoding="utf-8") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            target = Path(row.get("target",""))
            log[target.name] = row
    return log

def infer_from_path(path):
    """Infer niche/use/subtype from folder parts and filename."""
    parts = [p.lower() for p in path.parts]
    niches = {"bathroom","shower","flooring","hvac","siding","security"}
    uses   = {"ba","hero","project"}
    subtypes = {"before","after","together","result","process","wide","detail","worker","story","hero"}

    niche   = next((p for p in parts if p in niches), "general")
    use     = next((p for p in parts if p in uses), "hero")
    stem    = path.stem.lower()
    subtype = next((s for s in subtypes if s in stem), "result")
    return niche, use, subtype

def build():
    print("\nBuilding dataset from local data...")

    images = sorted(
        p for p in MEDIA_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in {".jpg",".jpeg",".png",".webp"}
    )
    total = len(images)
    print(f"Found {total} images")

    cls_by_name  = load_classifications()
    rename_log   = load_rename_log()

    random.seed(42)
    shuffled = list(images)
    random.shuffle(shuffled)
    split_map = {str(p): assign_split(i, total) for i, p in enumerate(shuffled)}

    dataset_images = []
    for idx, path in enumerate(images):
        rel_path = str(path.relative_to(MEDIA_DIR)).replace("\\", "/")
        name = path.name

        # Try to get metadata from rename_log
        log_row = rename_log.get(name, {})
        cls_row = cls_by_name.get(name, {})

        # Infer from path structure
        niche_p, use_p, subtype_p = infer_from_path(path)

        # Priority: rename_log > classifications > path inference
        niche   = log_row.get("niche")   or cls_row.get("niche")   or niche_p
        use     = log_row.get("use")     or cls_row.get("use")     or use_p
        subtype = log_row.get("subtype") or cls_row.get("subtype") or subtype_p
        quality = int(log_row.get("quality_score") or cls_row.get("quality_score") or 7)
        has_text   = str(log_row.get("has_text_overlay") or cls_row.get("has_text_overlay","false")).lower() == "true"
        has_person = str(cls_row.get("has_person", "false")).lower() == "true"

        # Normalize
        if use in ("b/a","before_after","before-after"): use = "ba"
        niche   = niche.lower().strip()   if niche   else "general"
        use     = use.lower().strip()     if use     else "hero"
        subtype = subtype.lower().strip() if subtype else "result"

        w, h = get_dims(path)
        kb   = path.stat().st_size // 1024

        tags    = get_tags(niche, use, subtype, has_text, has_person)
        caption = get_caption(niche, use, subtype)

        dataset_images.append({
            "id":               idx + 1,
            "path":             rel_path,
            "filename":         name,
            "niche":            niche,
            "use":              use,
            "subtype":          subtype,
            "quality":          quality,
            "width":            w,
            "height":           h,
            "size_kb":          kb,
            "has_text_overlay": has_text,
            "has_person":       has_person,
            "tags":             tags,
            "caption":          caption,
            "split":            split_map.get(str(path), "train"),
        })

    # Stats
    niche_c   = Counter(e["niche"]   for e in dataset_images)
    use_c     = Counter(e["use"]     for e in dataset_images)
    subtype_c = Counter(e["subtype"] for e in dataset_images)
    split_c   = Counter(e["split"]   for e in dataset_images)
    q_avg     = sum(e["quality"] for e in dataset_images) / len(dataset_images)
    q_high    = sum(1 for e in dataset_images if e["quality"] >= 7)
    q_low     = sum(1 for e in dataset_images if e["quality"] < 5)
    has_dims  = sum(1 for e in dataset_images if e["width"] > 0)
    avg_w     = sum(e["width"]  for e in dataset_images if e["width"]>0)  // max(has_dims,1)
    avg_h     = sum(e["height"] for e in dataset_images if e["height"]>0) // max(has_dims,1)

    dataset = {
        "version": "1.0",
        "created": "2026-04-15",
        "total":   total,
        "stats": {
            "niches":      dict(niche_c.most_common()),
            "use_types":   dict(use_c.most_common()),
            "subtypes":    dict(subtype_c.most_common()),
            "splits":      dict(split_c),
            "avg_quality": round(q_avg, 2),
            "high_quality_count": q_high,
            "low_quality_count":  q_low,
            "avg_resolution":     f"{avg_w}x{avg_h}",
            "has_text_overlay":   sum(1 for e in dataset_images if e["has_text_overlay"]),
            "has_person":         sum(1 for e in dataset_images if e["has_person"]),
        },
        "taxonomy": {
            "niches": ["bathroom","shower","flooring","hvac","siding","security","general"],
            "use_types": {
                "ba":      "before/after comparison",
                "hero":    "finished showcase / portfolio",
                "project": "work in progress / process",
            },
            "subtypes": ["before","after","together","result","process","wide","detail","worker","story","hero"],
        },
        "images": dataset_images,
    }

    # Save JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    # Save CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        fields = ["id","path","filename","niche","use","subtype","quality",
                  "width","height","size_kb","has_text_overlay","has_person",
                  "tags","caption","split"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for e in dataset_images:
            row = dict(e)
            row["tags"] = "|".join(e["tags"])
            writer.writerow(row)

    # Print report
    print(f"\n{'='*58}")
    print(f"  DATASET REPORT — HomeIQ Media Library")
    print(f"{'='*58}")
    print(f"  Total images     : {total}")
    print(f"  Avg quality      : {q_avg:.1f}/10  (high={q_high}, low={q_low})")
    print(f"  Avg resolution   : {avg_w}x{avg_h}px")
    print(f"  Has text overlay : {dataset['stats']['has_text_overlay']}")
    print(f"  Has person       : {dataset['stats']['has_person']}")
    print(f"\n  TRAIN / VAL / TEST SPLITS")
    for s in ["train","val","test"]:
        c = split_c.get(s,0)
        print(f"    {s:5} : {c:4}  ({c/total*100:.0f}%)")
    print(f"\n  BY NICHE")
    for n, c in niche_c.most_common():
        bar = '#' * (c // 3)
        print(f"    {n:12} : {c:4}  {bar}")
    print(f"\n  BY USE TYPE")
    labels = {"ba":"Before/After","hero":"Hero/Showcase","project":"Project/Process"}
    for u, c in use_c.most_common():
        print(f"    {labels.get(u,u):22} : {c:4}")
    print(f"\n  BY SUBTYPE")
    for s, c in subtype_c.most_common():
        print(f"    {s:12} : {c:4}")
    print(f"\n  OUTPUT FILES")
    print(f"    {OUTPUT_JSON}  ({OUTPUT_JSON.stat().st_size//1024} KB)")
    print(f"    {OUTPUT_CSV}  ({OUTPUT_CSV.stat().st_size//1024} KB)")
    print(f"{'='*58}")
    print(f"\n  NOTE: Re-run 'python build_dataset.py' after topping up")
    print(f"  OpenRouter credits to add AI-generated tags & captions.")

if __name__ == "__main__":
    build()
