"""
AI-powered batch photo enhancer using Gemini via OpenRouter.

Pipeline per photo:
  1. Compress to max 1536px JPEG (API limit)
  2. Send to Gemini image generation model with enhancement prompt
  3. Parse returned image (3-level fallback)
  4. Normalize result to 1920×1080 with smart blurred fill
  5. Save to output_dir preserving folder structure

Usage:
  python -m processor enhance Media --output Media_enhanced
  python -m processor enhance Media/hvac --output Media_enhanced --workers 2
  python -m processor enhance Media/bathroom/hero --output Media_enhanced --model google/gemini-2.0-flash-preview-image-generation
"""
import base64
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from clients.openrouter_client import OpenRouterClient
from processor.normalizer import normalize_image, DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_QUALITY

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tiff"}

# Default model — fastest, fewer safety refusals
# Recommended models (cheapest → best quality):
# sourceful/riverflow-v2-fast        — $0.02/1K, $0.04/2K  ← best value
# google/gemini-2.5-flash-image      — ~$0.004/photo
# google/gemini-3.1-flash-image-preview — ~$0.005/photo
# google/gemini-3-pro-image-preview  — ~$0.019/photo (best Gemini quality)
DEFAULT_MODEL = "sourceful/riverflow-v2-fast"

# Max input size before sending to API (px on longest side)
MAX_INPUT_PX = 1536

# ─── Enhancement prompts per niche ───────────────────────────────────────────

BASE_PROMPT = """\
Enhance this home improvement photo professionally:
1. Inpaint any overlaid text, logos, or graphics naturally into the background
2. Correct white balance to neutral daylight 5500K, lift shadows gently
3. Boost micro-contrast and sharpen structural edges and surfaces
4. Remove sensor noise and compression artifacts
5. Do NOT alter the actual work, people, or scene composition
6. Do NOT add elements that are not in the original photo
Return ONE enhanced image. Suitable for contractor portfolio and Google Business listing.
"""

NICHE_PROMPTS = {
    "hvac": BASE_PROMPT + "\nFocus: sharpen HVAC unit fins, copper pipes, conduit runs, concrete pad texture.",
    "bathroom": BASE_PROMPT + "\nFocus: real estate interior style — clean whites, tile grout clarity, fixture chrome.",
    "shower": BASE_PROMPT + "\nFocus: tile detail, grout lines, glass clarity, wet surface reflections.",
    "flooring": BASE_PROMPT + "\nFocus: floor grain/texture, grout lines, transition strips, baseboard edges.",
    "siding": BASE_PROMPT + "\nFocus: exterior siding lines, trim edges, paint uniformity, sky balance.",
    "security": BASE_PROMPT + "\nFocus: camera housing detail, mounting brackets, lens clarity.",
}


def _get_prompt(niche: str) -> str:
    return NICHE_PROMPTS.get(niche.lower(), BASE_PROMPT)


# ─── Image compression ────────────────────────────────────────────────────────

def _compress_for_api(path: Path, max_px: int = MAX_INPUT_PX) -> str:
    """Load image, resize to max_px on longest side, return base64 JPEG string."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode()


# ─── Response parsing — 3-level fallback ─────────────────────────────────────

def _parse_image_response(response_json: dict) -> Optional[bytes]:
    """
    Extract image bytes from Gemini/OpenRouter response.
    Level 1: choices[0].message.images[0].image_url.url
    Level 2: choices[0].message.content[] type=image_url
    Level 3: base64 data URI embedded in text content
    """
    try:
        msg = response_json["choices"][0]["message"]

        # Level 1 — OpenRouter image wrapper
        images = msg.get("images", [])
        if images:
            url = images[0].get("image_url", {}).get("url", "")
            if url.startswith("data:image"):
                b64 = url.split(",", 1)[1]
                return base64.b64decode(b64)

        # Level 2 — content array with image_url type
        content = msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    if url.startswith("data:image"):
                        b64 = url.split(",", 1)[1]
                        return base64.b64decode(b64)

        # Level 3 — base64 data URI in text content
        text = msg.get("content", "")
        if isinstance(text, str):
            match = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", text)
            if match:
                return base64.b64decode(match.group(1))

    except (KeyError, IndexError, Exception) as e:
        logger.debug(f"Response parse error: {e}")

    return None


# ─── Core: enhance single photo ───────────────────────────────────────────────

def enhance_image(
    source: Path,
    target: Path,
    client: OpenRouterClient,
    niche: str = "unknown",
    model: str = DEFAULT_MODEL,
    normalize: bool = True,
    norm_width: int = DEFAULT_WIDTH,
    norm_height: int = DEFAULT_HEIGHT,
    norm_quality: int = DEFAULT_QUALITY,
    retries: int = 2,
) -> dict:
    """
    Enhance a single photo via Gemini AI, optionally normalize to gold standard.

    Returns: {"source": ..., "target": ..., "status": "ok"|"error"|"refused", "error": ...}
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    prompt = _get_prompt(niche)
    img_b64 = _compress_for_api(source)

    last_error = None
    for attempt in range(retries + 1):
        try:
            response_json = client.enhance_vision(
                prompt=prompt,
                image_b64=img_b64,
                model=model,
            )

            # Check for safety refusal
            msg_content = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(msg_content, str) and any(
                phrase in msg_content.lower()
                for phrase in ["just a language model", "i'm not able", "i cannot", "can't generate"]
            ):
                # Retry with softer prompt
                if attempt < retries:
                    logger.warning(f"  Safety refusal on {source.name}, retrying with soft prompt...")
                    prompt = BASE_PROMPT  # fallback to generic
                    time.sleep(1)
                    continue
                return {"source": str(source), "target": str(target), "status": "refused",
                        "error": "Safety filter refusal"}

            # Parse image from response
            img_bytes = _parse_image_response(response_json)
            if not img_bytes:
                if attempt < retries:
                    logger.warning(f"  No image in response for {source.name}, retry {attempt+1}...")
                    time.sleep(2)
                    continue
                return {"source": str(source), "target": str(target), "status": "error",
                        "error": "No image in API response"}

            # Save raw enhanced image to temp, then normalize
            tmp = target.with_suffix(".tmp.jpg")
            tmp.write_bytes(img_bytes)

            if normalize:
                normalize_image(tmp, target, norm_width, norm_height, norm_quality)
                tmp.unlink(missing_ok=True)
            else:
                tmp.rename(target)

            return {"source": str(source), "target": str(target), "status": "ok"}

        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                logger.warning(f"  Error {source.name} attempt {attempt+1}: {e}")
                time.sleep(2 ** attempt)

    return {"source": str(source), "target": str(target), "status": "error", "error": last_error}


# ─── Batch enhancement ────────────────────────────────────────────────────────

def collect_images(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        and "_enhanced" not in p.stem
    )


def _detect_niche(path: Path, source_dir: Path) -> str:
    """Detect niche from folder path: Media/hvac/hero/file.jpg → 'hvac'."""
    try:
        rel = path.relative_to(source_dir)
        return rel.parts[0].lower()
    except (ValueError, IndexError):
        return "unknown"


def _get_mp(path: Path) -> float:
    """Return megapixels of image, 0 on error."""
    try:
        with Image.open(path) as img:
            return img.width * img.height / 1_000_000
    except Exception:
        return 0.0


def enhance_batch(
    source_dir: Path,
    output_dir: Path,
    model: str = DEFAULT_MODEL,
    normalize: bool = True,
    norm_width: int = DEFAULT_WIDTH,
    norm_height: int = DEFAULT_HEIGHT,
    norm_quality: int = DEFAULT_QUALITY,
    workers: int = 2,
    resume: bool = True,
    ai_max_mp: float = 2.0,   # photos BELOW this → AI enhance; above → just normalize
    ai_only: bool = False,    # if True → skip large photos entirely (no normalize-only)
) -> list[dict]:
    """
    Smart batch pipeline for source_dir → output_dir:

    - Photos < ai_max_mp  → AI enhance via model + normalize to 1920x1080
    - Photos >= ai_max_mp → just normalize to 1920x1080 (no AI cost)
    - Preserves folder structure
    - resume=True: skip already-done files
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in .env")

    images = collect_images(source_dir)
    if not images:
        logger.warning(f"No images found in {source_dir}")
        return []

    # Split: AI vs normalize-only
    need_ai, just_normalize = [], []
    for p in images:
        mp = _get_mp(p)
        if mp < ai_max_mp:
            need_ai.append((p, mp))
        else:
            just_normalize.append((p, mp))

    logger.info(f"Found {len(images)} images → {output_dir}")
    logger.info(f"  AI enhance  (<{ai_max_mp}MP): {len(need_ai)} photos  [model: {model}]")
    if ai_only:
        logger.info(f"  Skipping (>={ai_max_mp}MP): {len(just_normalize)} photos  [--ai-only mode]")
        just_normalize = []
    else:
        logger.info(f"  Normalize only (>={ai_max_mp}MP): {len(just_normalize)} photos  [free]")

    def _dest(path: Path) -> Path:
        rel = path.relative_to(source_dir)
        return output_dir / rel.with_suffix(".jpg")

    # Resume: skip already done
    if resume:
        need_ai_todo       = [(p, mp) for p, mp in need_ai       if not _dest(p).exists()]
        just_norm_todo     = [(p, mp) for p, mp in just_normalize if not _dest(p).exists()]
        skipped = len(images) - len(need_ai_todo) - len(just_norm_todo)
        if skipped:
            logger.info(f"  Resuming: {skipped} already done, {len(need_ai_todo)+len(just_norm_todo)} remaining")
    else:
        need_ai_todo   = need_ai
        just_norm_todo = just_normalize

    results = []
    done_count = 0
    total = len(need_ai_todo) + len(just_norm_todo)

    if total == 0:
        logger.info("All images already processed.")
        return [{"source": str(p), "target": str(_dest(p)), "status": "ok"} for p in images]

    # ── Step 1: normalize-only (fast, no API) ────────────────────────────────
    for path, mp in just_norm_todo:
        dest = _dest(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            normalize_image(path, dest, norm_width, norm_height, norm_quality)
            done_count += 1
            logger.info(f"  [{done_count}/{total}] NORM {path.name}  ({mp:.1f}MP)")
            results.append({"source": str(path), "target": str(dest), "status": "ok", "method": "normalize"})
        except Exception as e:
            done_count += 1
            logger.warning(f"  [{done_count}/{total}] NORM_ERR {path.name}: {e}")
            results.append({"source": str(path), "target": str(dest), "status": "error", "error": str(e)})

    if not need_ai_todo:
        return results

    # ── Step 2: AI enhance (costs money) ─────────────────────────────────────
    client = OpenRouterClient(api_key=api_key)

    # don't reset results — keep normalize results from step 1
    total_ai = len(need_ai_todo)
    ai_done  = 0

    def _process(path_mp: tuple) -> dict:
        path, mp = path_mp
        niche = _detect_niche(path, source_dir)
        dest = _dest(path)
        return enhance_image(
            source=path,
            target=dest,
            client=client,
            niche=niche,
            model=model,
            normalize=normalize,
            norm_width=norm_width,
            norm_height=norm_height,
            norm_quality=norm_quality,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, (p, mp)): p for p, mp in need_ai_todo}
        for future in as_completed(futures):
            r = future.result()
            ai_done += 1
            src_name = Path(r["source"]).name
            status_str = {
                "ok": "AI+OK",
                "error": f'ERR [{r.get("error","?")}]',
                "refused": "REFUSED",
            }.get(r["status"], r["status"])
            logger.info(f"  [{ai_done}/{total_ai}] {status_str} {src_name}")
            results.append(r)

    client.close()

    all_results = results  # includes both normalize and AI results
    ai_ok   = sum(1 for r in all_results if r["status"] == "ok" and r.get("method") != "normalize")
    norm_ok = sum(1 for r in all_results if r.get("method") == "normalize")
    err     = sum(1 for r in all_results if r["status"] == "error")
    refused = sum(1 for r in all_results if r["status"] == "refused")
    logger.info(f"\nDone: {ai_ok} AI enhanced, {norm_ok} normalized, {err} errors, {refused} refused")
    return results


def print_summary(results: list[dict]) -> None:
    ok = [r for r in results if r["status"] == "ok"]
    errors = [r for r in results if r["status"] == "error"]
    refused = [r for r in results if r["status"] == "refused"]
    print(f"\n{'='*50}")
    print(f"Enhanced:  {len(ok)}")
    print(f"Errors:    {len(errors)}")
    print(f"Refused:   {len(refused)}")
    if errors:
        print("\nErrors:")
        for r in errors[:10]:
            print(f"  {Path(r['source']).name}: {r.get('error','?')}")
    if refused:
        print("\nRefused by safety filter:")
        for r in refused[:5]:
            print(f"  {Path(r['source']).name}")
    print(f"{'='*50}")
