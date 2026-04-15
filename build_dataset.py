"""
Dataset Builder — re-classify all Media/ photos, generate rich tags + captions,
output dataset.json + dataset.csv + summary report.

Format: ML-ready dataset (image path, niche, use, subtype, tags, caption, split)

Run: python build_dataset.py
"""
import base64
import csv
import json
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MEDIA_DIR    = Path("Media")
OUTPUT_JSON  = Path("dataset.json")
OUTPUT_CSV   = Path("dataset.csv")
WORKERS      = 8
MODEL        = "openai/gpt-4o-mini"
RESUME_FILE  = Path("dataset_progress.json")
TRAIN_RATIO  = 0.80
VAL_RATIO    = 0.10
# TEST = remaining 0.10

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# ── Niche / use / subtype taxonomy ───────────────────────────────────────────

NICHES    = ["bathroom", "shower", "flooring", "hvac", "siding", "security", "general"]
USE_TYPES = ["ba", "hero", "project"]      # ba=before/after, hero=showcase, project=process
SUBTYPES  = ["before", "after", "together", "result", "process", "wide", "detail",
             "worker", "story", "hero"]

CLASSIFY_PROMPT = """You are a professional home improvement photo dataset annotator.

Analyze this photo and return ONLY a valid JSON object (no markdown, no explanation):

{
  "niche": "<one of: bathroom, shower, flooring, hvac, siding, security, general>",
  "use": "<one of: ba (before/after), hero (showcase result), project (work in progress/process)>",
  "subtype": "<one of: before, after, together (b+a in one frame), result, process, wide, detail, worker, story, hero>",
  "quality": <integer 1-10>,
  "has_text_overlay": <true/false>,
  "has_person": <true/false>,
  "tags": ["tag1", "tag2", ...],
  "caption": "<one sentence describing the photo for ML training — what is shown, style, context>"
}

Rules for tags (provide 8-14 tags):
- Include niche keywords (e.g. "tile", "grout", "shower head")
- Include visual style (e.g. "bright", "natural light", "close-up")
- Include content descriptors (e.g. "before after comparison", "installation", "finished result")
- Include quality markers (e.g. "high resolution", "professional photo")
- Include use-case tags (e.g. "contractor portfolio", "home renovation", "remodeling")

Caption must be 1 sentence, factual, suitable for image-text training pairs."""


# ── Image compression ─────────────────────────────────────────────────────────

def compress_image(path: Path, max_px: int = 768) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=82)
        return base64.b64encode(buf.getvalue()).decode()


def get_dimensions(path: Path) -> tuple:
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return 0, 0


# ── Classification ────────────────────────────────────────────────────────────

def classify_photo(path: Path, api_key: str, retries: int = 2) -> dict:
    """Send photo to GPT-4o-mini, return classification dict."""
    try:
        b64 = compress_image(path)
    except Exception as e:
        return {"error": f"compress: {e}"}

    client = httpx.Client(
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://homeimpruv.com",
            "X-Title": "HomeImpruv Dataset Builder",
        },
        timeout=60.0,
    )

    for attempt in range(retries + 1):
        try:
            resp = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": CLASSIFY_PROMPT},
                    ]}],
                    "max_tokens": 512,
                    "temperature": 0.1,
                }
            )
            if resp.status_code == 429:
                time.sleep(5)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            result = json.loads(content.strip())
            client.close()
            return result
        except json.JSONDecodeError as e:
            if attempt < retries:
                time.sleep(1)
            else:
                client.close()
                return {"error": f"json_parse: {e}", "raw": content[:200] if 'content' in dir() else ""}
        except Exception as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                client.close()
                return {"error": str(e)}

    client.close()
    return {"error": "max retries"}


# ── Dataset building ──────────────────────────────────────────────────────────

def collect_images() -> list[Path]:
    return sorted(
        p for p in MEDIA_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def infer_from_path(path: Path) -> dict:
    """Infer niche/use/subtype from folder structure as fallback."""
    parts = [p.lower() for p in path.parts]
    niche = next((p for p in parts if p in NICHES), "general")
    use   = next((p for p in parts if p in USE_TYPES), "hero")
    # from filename stem
    stem = path.stem.lower()
    subtype = next((s for s in SUBTYPES if s in stem), "result")
    return {"niche": niche, "use": use, "subtype": subtype}


def assign_split(idx: int, total: int) -> str:
    r = idx / total
    if r < TRAIN_RATIO: return "train"
    if r < TRAIN_RATIO + VAL_RATIO: return "val"
    return "test"


def build_dataset():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set"); return

    images = collect_images()
    total  = len(images)
    print(f"\nFound {total} images in Media/")
    print(f"Model: {MODEL} | Workers: {WORKERS}")

    # Load resume progress
    done: dict = {}
    if RESUME_FILE.exists():
        with open(RESUME_FILE, encoding="utf-8") as f:
            done = json.load(f)
        print(f"Resuming: {len(done)} already classified")

    results: dict = dict(done)
    pending = [p for p in images if str(p) not in done]
    print(f"To classify: {len(pending)}")

    if pending:
        def _process(path: Path) -> tuple[str, dict]:
            cls = classify_photo(path, api_key)
            w, h = get_dimensions(path)
            cls["_w"] = w
            cls["_h"] = h
            cls["_kb"] = path.stat().st_size // 1024
            return str(path), cls

        done_count = len(done)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(_process, p): p for p in pending}
            for future in as_completed(futures):
                key, cls = future.result()
                results[key] = cls
                done_count += 1
                name = Path(key).name
                if "error" in cls:
                    logger.warning(f"  [{done_count}/{total}] ERR {name}: {cls['error']}")
                else:
                    logger.info(f"  [{done_count}/{total}] OK  {name} | {cls.get('niche','?')}/{cls.get('use','?')}/{cls.get('subtype','?')} q={cls.get('quality','?')}")
                # Save progress every 10
                if done_count % 10 == 0:
                    with open(RESUME_FILE, "w", encoding="utf-8") as f:
                        json.dump(results, f, ensure_ascii=False)

        # Save final progress
        with open(RESUME_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False)

    # ── Build dataset ─────────────────────────────────────────────────────────
    print("\nBuilding dataset...")

    # Shuffle for split assignment (seed for reproducibility)
    random.seed(42)
    shuffled = list(images)
    random.shuffle(shuffled)
    split_map = {str(p): assign_split(i, total) for i, p in enumerate(shuffled)}

    dataset_images = []
    for idx, path in enumerate(images):
        key = str(path)
        cls = results.get(key, {})
        fallback = infer_from_path(path)

        # Merge: AI result overrides fallback
        niche   = cls.get("niche")   or fallback["niche"]
        use     = cls.get("use")     or fallback["use"]
        subtype = cls.get("subtype") or fallback["subtype"]

        # Normalize use code
        if use in ("before_after", "before-after", "ba", "b/a"): use = "ba"

        tags    = cls.get("tags", [])
        caption = cls.get("caption", "")
        quality = cls.get("quality", 5)
        w       = cls.get("_w", 0)
        h       = cls.get("_h", 0)
        kb      = cls.get("_kb", 0)
        has_text    = cls.get("has_text_overlay", False)
        has_person  = cls.get("has_person", False)

        rel_path = str(path.relative_to(MEDIA_DIR)).replace("\\", "/")

        dataset_images.append({
            "id":          idx + 1,
            "path":        rel_path,
            "filename":    path.name,
            "niche":       niche,
            "use":         use,
            "subtype":     subtype,
            "quality":     quality,
            "width":       w,
            "height":      h,
            "size_kb":     kb,
            "has_text_overlay": has_text,
            "has_person":  has_person,
            "tags":        tags,
            "caption":     caption,
            "split":       split_map.get(key, "train"),
        })

    # ── Save JSON ─────────────────────────────────────────────────────────────
    from collections import Counter
    niche_counts   = Counter(e["niche"]   for e in dataset_images)
    use_counts     = Counter(e["use"]     for e in dataset_images)
    subtype_counts = Counter(e["subtype"] for e in dataset_images)
    quality_avg    = sum(e["quality"] for e in dataset_images) / len(dataset_images)
    split_counts   = Counter(e["split"]  for e in dataset_images)

    dataset = {
        "version":  "1.0",
        "created":  "2026-04-15",
        "total":    total,
        "stats": {
            "niches":        dict(niche_counts.most_common()),
            "use_types":     dict(use_counts.most_common()),
            "subtypes":      dict(subtype_counts.most_common()),
            "avg_quality":   round(quality_avg, 2),
            "splits":        dict(split_counts),
            "has_text":      sum(1 for e in dataset_images if e["has_text_overlay"]),
            "has_person":    sum(1 for e in dataset_images if e["has_person"]),
            "errors":        sum(1 for k in results if "error" in results[k]),
        },
        "taxonomy": {
            "niches":   NICHES,
            "use_types": {"ba": "before/after comparison", "hero": "finished showcase", "project": "work in progress"},
            "subtypes": SUBTYPES,
        },
        "images": dataset_images,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id","path","filename","niche","use","subtype","quality",
            "width","height","size_kb","has_text_overlay","has_person",
            "tags","caption","split"
        ])
        writer.writeheader()
        for e in dataset_images:
            row = dict(e)
            row["tags"] = "|".join(e["tags"])
            writer.writerow(row)

    # ── Print report ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  DATASET REPORT")
    print(f"{'='*60}")
    print(f"  Total images  : {total}")
    print(f"  Avg quality   : {quality_avg:.1f} / 10")
    print(f"  Has text      : {dataset['stats']['has_text']}")
    print(f"  Has person    : {dataset['stats']['has_person']}")
    print(f"  Errors        : {dataset['stats']['errors']}")
    print(f"\n  SPLITS")
    for s, c in split_counts.items():
        print(f"    {s:6} : {c:4}  ({c/total*100:.0f}%)")
    print(f"\n  BY NICHE")
    for n, c in niche_counts.most_common():
        bar = '#' * (c // 3)
        print(f"    {n:12} : {c:4}  {bar}")
    print(f"\n  BY USE TYPE")
    for u, c in use_counts.most_common():
        label = {"ba":"before/after","hero":"hero/showcase","project":"project/process"}.get(u, u)
        print(f"    {label:20} : {c:4}")
    print(f"\n  BY SUBTYPE")
    for s, c in subtype_counts.most_common():
        print(f"    {s:12} : {c:4}")
    print(f"\n  OUTPUT FILES")
    print(f"    {OUTPUT_JSON}  ({OUTPUT_JSON.stat().st_size // 1024} KB)")
    print(f"    {OUTPUT_CSV}   ({OUTPUT_CSV.stat().st_size // 1024} KB)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    build_dataset()
