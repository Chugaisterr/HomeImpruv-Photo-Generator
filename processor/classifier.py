"""
AI-powered media classifier using GPT-4o Mini via OpenRouter.

For each image in Media/:
  1. Compress to 512px JPEG (saves cost, speeds up requests)
  2. Send to GPT-4o Mini with structured classification prompt
  3. Parse JSON response → classification dict
  4. Add local fields: needs_upscale (resolution check), width, height, megapixels
  5. Save incremental results to classifications.json (resume-safe)

Usage:
  python -m processor classify --media-dir Media --output classifications.json
  python -m processor classify --media-dir Media/HVAC --workers 3
"""
import base64
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from clients.openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tiff"}

# Photos with shortest side below this threshold are flagged for AI upscale
MIN_UPSCALE_PX = 1920

CLASSIFICATION_PROMPT = """\
Analyze this home improvement photo. Return ONLY valid JSON — no markdown, no explanation.

Required JSON structure:
{
  "niche": "<one of: hvac | bathroom | shower | flooring | siding | security | unknown>",
  "use": "<one of: hero | ba | project | unknown>",
  "subtype": "<see rules below>",
  "has_text_overlay": <true or false>,
  "has_person": <true or false>,
  "quality_score": <integer 1-10>,
  "issues": [<array of: watermark | low_res | bad_lighting | blurry | cropped | irrelevant | duplicate_format>],
  "confidence": <float 0.0-1.0>,
  "caption": "<unique 1-2 sentence description of THIS specific photo for AI training>"
}

Subtype rules:
- if use=hero → one of: worker (tech/worker in action) | result (finished space, no people) | product (equipment/product shot) | team (crew/team)
- if use=ba  → one of: together (before+after in 1 frame) | before (before state only) | after (after state only) | story (vertical/social story format) | process (work in progress)
- if use=project → one of: wide (full room/area) | detail (close-up of work) | process (installation steps)
- if use=unknown → "unknown"

Niche reference:
- hvac: air conditioning, HVAC units, ductwork, technicians with AC equipment
- bathroom: bathroom renovation, tiles, vanity, tub, general bathroom remodel
- shower: walk-in shower specifically, shower conversion, shower tiles
- flooring: floor installation, hardwood, laminate, tile flooring, carpet
- siding: exterior siding, house facade, vinyl/fiber cement siding
- security: security cameras, CCTV, smart home security, alarm systems

Quality score guide: 1=unusable, 4=below avg, 6=usable, 8=good portfolio, 10=hero-worthy

Caption rules:
- Describe what is VISUALLY present in THIS specific photo (colors, materials, condition, composition)
- If before/after: mention both states ("old cracked tiles → modern large-format porcelain")
- If hero/result: describe the finished space or equipment shown
- Do NOT use generic phrases like "professional renovation" — be specific to what you see
- 1-2 sentences max, suitable for text-to-image model training

Folder hint: {folder_hint}
Filename: {filename}

Return ONLY the JSON object."""


def compress_image(path: Path, max_px: int = 512, quality: int = 85) -> str:
    """Resize image to max_px on longest side, return base64 JPEG string."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode()


def _folder_hint(path: Path, media_dir: Path) -> str:
    """Extract relative folder path as hint for the model."""
    try:
        return str(path.parent.relative_to(media_dir))
    except ValueError:
        return path.parent.name


def _parse_response(raw: str) -> dict:
    """Parse JSON from model response, tolerating minor formatting issues."""
    raw = raw.strip()
    # Strip markdown code blocks if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(raw)


def _local_image_info(path: Path) -> dict:
    """Read image dimensions locally (no API). Returns width, height, megapixels, needs_upscale."""
    try:
        with Image.open(path) as img:
            w, h = img.size
            mp = round(w * h / 1_000_000, 2)
            return {
                "width": w,
                "height": h,
                "megapixels": mp,
                "needs_upscale": min(w, h) < MIN_UPSCALE_PX,
            }
    except Exception:
        return {"width": 0, "height": 0, "megapixels": 0.0, "needs_upscale": False}


def classify_one(
    client: OpenRouterClient,
    path: Path,
    media_dir: Path,
    model: str = "openai/gpt-4o-mini",
    retries: int = 2,
) -> dict:
    """Classify a single image. Returns classification dict with 'file' key."""
    folder_hint = _folder_hint(path, media_dir)
    prompt = (
        CLASSIFICATION_PROMPT
        .replace("{folder_hint}", folder_hint)
        .replace("{filename}", path.name)
    )

    # Always collect local info (free, no API)
    local_info = _local_image_info(path)

    last_error = None
    for attempt in range(retries + 1):
        try:
            image_b64 = compress_image(path)
            raw = client.chat_vision(prompt, image_b64, model=model)
            result = _parse_response(raw)
            result["file"] = str(path.relative_to(media_dir))
            result["folder_hint"] = folder_hint
            result.update(local_info)
            return result
        except json.JSONDecodeError as e:
            last_error = f"JSON parse error: {e}"
            logger.warning(f"[{path.name}] attempt {attempt+1}: {last_error}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[{path.name}] attempt {attempt+1}: {last_error}")
            if attempt < retries:
                time.sleep(2 ** attempt)

    return {
        "file": str(path.relative_to(media_dir)),
        "folder_hint": folder_hint,
        "error": last_error,
        "niche": "unknown",
        "use": "unknown",
        "subtype": "unknown",
        "has_text_overlay": False,
        "has_person": False,
        "quality_score": 0,
        "issues": ["classification_failed"],
        "confidence": 0.0,
        "caption": "",
        **local_info,
    }


def collect_images(media_dir: Path) -> list[Path]:
    """Recursively collect all image files under media_dir."""
    images = []
    for path in sorted(media_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            images.append(path)
    return images


def classify_all(
    media_dir: Path,
    output_path: Path,
    api_key: str,
    workers: int = 5,
    model: str = "openai/gpt-4o-mini",
    resume: bool = True,
) -> list[dict]:
    """
    Classify all images in media_dir.

    - Saves results incrementally to output_path (resume-safe).
    - Skips already-classified files if resume=True.
    - Returns final list of classification dicts.
    """
    images = collect_images(media_dir)
    if not images:
        logger.warning(f"No images found in {media_dir}")
        return []

    # Load existing results for resume
    existing: dict[str, dict] = {}
    if resume and output_path.exists():
        with open(output_path) as f:
            data = json.load(f)
        existing = {r["file"]: r for r in data if "error" not in r}
        logger.info(f"Resuming — {len(existing)} already classified")

    client = OpenRouterClient(api_key=api_key)
    results: list[dict] = list(existing.values())
    already_done = set(existing.keys())

    pending = [
        p for p in images
        if str(p.relative_to(media_dir)) not in already_done
    ]

    logger.info(f"Images: {len(images)} total, {len(pending)} to classify")

    def save_results():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    completed_count = len(already_done)
    total = len(images)

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(classify_one, client, p, media_dir, model): p
                for p in pending
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        "file": str(path.relative_to(media_dir)),
                        "error": str(e),
                        "niche": "unknown",
                        "use": "unknown",
                        "subtype": "unknown",
                    }

                results.append(result)
                completed_count += 1

                status = "✓" if "error" not in result else "✗"
                niche = result.get("niche", "?")
                use = result.get("use", "?")
                subtype = result.get("subtype", "?")
                logger.info(
                    f"[{completed_count}/{total}] {status} {path.name} "
                    f"→ {niche}/{use}/{subtype}"
                )

                # Save after every 10 results
                if completed_count % 10 == 0:
                    save_results()

    finally:
        save_results()
        client.close()

    logger.info(f"Classification complete. Results saved to {output_path}")
    return results


def print_summary(results: list[dict]) -> None:
    """Print classification summary to console."""
    total = len(results)
    errors = sum(1 for r in results if "error" in r)
    with_text = sum(1 for r in results if r.get("has_text_overlay"))
    with_person = sum(1 for r in results if r.get("has_person"))
    need_upscale = sum(1 for r in results if r.get("needs_upscale"))
    with_caption = sum(1 for r in results if r.get("caption"))

    print(f"\n{'='*50}")
    print(f"CLASSIFICATION SUMMARY — {total} files")
    print(f"{'='*50}")
    print(f"  Errors:          {errors}")
    print(f"  Has caption:     {with_caption} / {total}")
    print(f"  Has text/logo:   {with_text}  ← needs text removal")
    print(f"  Needs upscale:   {need_upscale}  ← min side < {MIN_UPSCALE_PX}px")
    print(f"  Has person:      {with_person}")

    by_niche: dict[str, int] = {}
    by_use: dict[str, int] = {}
    for r in results:
        n = r.get("niche", "unknown")
        u = r.get("use", "unknown")
        by_niche[n] = by_niche.get(n, 0) + 1
        by_use[u] = by_use.get(u, 0) + 1

    print(f"\n  By niche:")
    for k, v in sorted(by_niche.items(), key=lambda x: -x[1]):
        print(f"    {k:<15} {v}")

    print(f"\n  By use:")
    for k, v in sorted(by_use.items(), key=lambda x: -x[1]):
        print(f"    {k:<15} {v}")
    print()
