"""
Text/watermark removal test — 3 models comparison.

Sends test_compare/test_text.jpg.webp to 3 models with inpainting prompt.
Saves results to test_compare/TEXT_*.jpg

Run: python test_text_removal.py
"""
import base64
import os
import re
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TEST_PHOTO = "test_compare/test_text.jpg.webp"
OUTPUT_DIR = Path("test_compare")
INPUT_PX   = 1536    # max px on longest side before sending

MODELS = [
    # (short_name, model_id, use_modalities)
    ("TEXT_flux2-klein",     "black-forest-labs/flux.2-klein-4b", False),
    ("TEXT_riverflow-fast",  "sourceful/riverflow-v2-fast",    False),
]

REMOVAL_PROMPT = """\
This home improvement photo has overlaid text labels and a company logo/watermark.
Please inpaint them out naturally:
1. Remove ALL overlaid text (labels like "Before", "After", any captions or titles)
2. Remove ALL logos, brand marks and watermarks by inpainting with the background
3. Reconstruct the underlying scene naturally — siding, sky, surfaces, structure
4. Do NOT alter white balance, colors, or scene composition beyond removal
5. Preserve all actual construction work and photo content
Return ONE clean image without any text or logo overlays.
Suitable for contractor portfolio."""

# ── Helpers ───────────────────────────────────────────────────────────────────

def compress(path: Path, max_px: int) -> tuple[str, tuple]:
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode(), img.size


def parse_image(response_json: dict) -> bytes | None:
    try:
        msg = response_json["choices"][0]["message"]

        # Level 1: images[]
        for img in msg.get("images", []):
            url = img.get("image_url", {}).get("url", "") or img.get("url", "")
            if url.startswith("data:image"):
                return base64.b64decode(url.split(",", 1)[1])

        # Level 2: content[] array
        content = msg.get("content", "")
        if isinstance(content, list):
            for item in content:
                if item.get("type") == "image_url":
                    url = item["image_url"]["url"]
                    if url.startswith("data:image"):
                        return base64.b64decode(url.split(",", 1)[1])

        # Level 3: base64 in text
        if isinstance(content, str):
            m = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", content)
            if m:
                return base64.b64decode(m.group(1))
    except Exception:
        pass
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set in .env")
        return

    src = Path(TEST_PHOTO)
    if not src.exists():
        print(f"ERROR: Photo not found: {src}")
        return

    print(f"Source: {src.name}")
    b64, input_size = compress(src, INPUT_PX)
    print(f"Input size: {input_size[0]}x{input_size[1]}  ({len(b64)//1024}KB base64)")
    print(f"Output dir: {OUTPUT_DIR}/")
    print(f"Testing {len(MODELS)} models for text/watermark removal...\n")
    print(f"{'Model':<28} {'Status':>8}  {'Size':>14}  {'KB':>6}  {'Time':>6}")
    print("-" * 75)

    # Save original for reference
    with Image.open(src) as orig:
        orig = orig.convert("RGB")
        orig_out = OUTPUT_DIR / "TEXT_0_ORIGINAL.jpg"
        orig.save(orig_out, "JPEG", quality=95)
        print(f"{'TEXT_0_ORIGINAL':<28} {'saved':>8}  "
              f"{str(orig.size[0])+'x'+str(orig.size[1]):>14}  "
              f"{orig_out.stat().st_size//1024:>6}  {'':>6}")

    client = httpx.Client(
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://homeimpruv.com",
            "X-Title": "HomeImpruv Text Removal Test",
        },
        timeout=180.0,
    )

    results = []
    for short_name, model_id, use_modalities in MODELS:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": REMOVAL_PROMPT},
            ]}],
            "max_tokens": 4096,
        }
        if use_modalities:
            payload["modalities"] = ["image", "text"]

        t0 = time.time()
        try:
            resp = client.post("https://openrouter.ai/api/v1/chat/completions", json=payload)
            elapsed = time.time() - t0

            if resp.status_code != 200:
                try:
                    error_msg = resp.json().get("error", {}).get("message", resp.text[:80])
                except Exception:
                    error_msg = resp.text[:80]
                print(f"{short_name:<28} {str(resp.status_code):>8}  {'':>14}  {'':>6}  "
                      f"{elapsed:>5.1f}s  <- {error_msg[:50]}")
                results.append({"model": short_name, "status": resp.status_code, "error": error_msg})
                continue

            rj = resp.json()

            # Show token usage if available
            usage = rj.get("usage", {})
            tokens_in  = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

            img_bytes = parse_image(rj)
            if not img_bytes:
                msg_content = rj["choices"][0]["message"].get("content", "")
                preview = str(msg_content)[:80]
                print(f"{short_name:<28} {'no_image':>8}  {'':>14}  {'':>6}  "
                      f"{elapsed:>5.1f}s  <- {preview}")
                results.append({"model": short_name, "status": "no_image", "text": preview})
                continue

            # Save result — force .jpg extension (no double extension)
            out_path = OUTPUT_DIR / f"{short_name}.jpg"
            result_img = Image.open(BytesIO(img_bytes)).convert("RGB")
            result_img.save(out_path, "JPEG", quality=95)
            size_str = f"{result_img.size[0]}x{result_img.size[1]}"
            kb = out_path.stat().st_size // 1024
            token_info = f"  [{tokens_in}in/{tokens_out}out tok]" if tokens_in else ""
            print(f"{short_name:<28} {'OK':>8}  {size_str:>14}  {kb:>6}  {elapsed:>5.1f}s{token_info}")
            results.append({
                "model": short_name, "status": "ok",
                "size": result_img.size, "kb": kb, "time": elapsed,
                "tokens_in": tokens_in, "tokens_out": tokens_out,
            })

        except Exception as e:
            elapsed = time.time() - t0
            print(f"{short_name:<28} {'ERROR':>8}  {'':>14}  {'':>6}  "
                  f"{elapsed:>5.1f}s  <- {str(e)[:60]}")
            results.append({"model": short_name, "status": "error", "error": str(e)})

        time.sleep(1.0)

    client.close()

    print("\n" + "-" * 75)
    ok = [r for r in results if r.get("status") == "ok"]
    print(f"Done: {len(ok)}/{len(MODELS)} models produced an image")
    print(f"Results saved to: {OUTPUT_DIR}/")
    print("\nCompare TEXT_qwen-vl-plus.jpg / TEXT_qwen3-vl-32b.jpg / TEXT_gemini-2.5-flash.jpg")


if __name__ == "__main__":
    main()
