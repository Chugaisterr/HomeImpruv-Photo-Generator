"""
Upscale model comparison test.

Sends ONE photo to 5 models, saves results to test_compare/
Run: python test_upscale.py
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

TEST_PHOTO = "Media/hvac/hero/hvac_hero_product_001.jpg"   # change if needed
OUTPUT_DIR = Path("test_compare")
INPUT_PX   = 1024    # compress input to this size before sending
OUTPUT_DIR.mkdir(exist_ok=True)

MODELS = [
    # (short_name, model_id, use_modalities)
    ("1_riverflow-fast",     "sourceful/riverflow-v2-fast",             False),
    ("2_riverflow-standard", "sourceful/riverflow-v2-standard-preview", False),
    ("3_riverflow-pro",      "sourceful/riverflow-v2-pro",              False),
    ("4_gemini-2.5-flash",   "google/gemini-2.5-flash-image",           True),
    ("5_gemini-3.1-flash",   "google/gemini-3.1-flash-image-preview",   True),
]

UPSCALE_PROMPT = """\
Upscale and enhance this home improvement photo to maximum quality:
1. Increase resolution and sharpness, recover fine details (equipment, surfaces, edges)
2. Correct white balance to neutral daylight 5500K
3. Inpaint any text, logo or watermark overlays naturally into the background
4. Reduce noise and JPEG compression artifacts
5. Do NOT change the scene, composition or add new elements
Return one enhanced high-resolution image."""

# ── Helpers ───────────────────────────────────────────────────────────────────

def compress(path: Path, max_px: int) -> str:
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
    print(f"Testing {len(MODELS)} models...\n")
    print(f"{'Model':<28} {'Status':>8}  {'Size':>14}  {'KB':>6}  {'Time':>6}")
    print("-" * 70)

    # Save original for reference
    with Image.open(src) as orig:
        orig_out = OUTPUT_DIR / "0_ORIGINAL.jpg"
        orig.save(orig_out, "JPEG", quality=95)
        print(f"{'0_ORIGINAL':<28} {'saved':>8}  {str(orig.size[0])+'x'+str(orig.size[1]):>14}  {orig_out.stat().st_size//1024:>6}  {'':>6}")

    client = httpx.Client(
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://homeimpruv.com",
            "X-Title": "HomeImpruv Upscale Test",
        },
        timeout=120.0,
    )

    results = []
    for short_name, model_id, use_modalities in MODELS:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": UPSCALE_PROMPT},
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
                error_msg = resp.json().get("error", {}).get("message", resp.text[:80])
                print(f"{short_name:<28} {str(resp.status_code):>8}  {'':>14}  {'':>6}  {elapsed:>5.1f}s  <-{error_msg[:50]}")
                results.append({"model": short_name, "status": resp.status_code, "error": error_msg})
                continue

            img_bytes = parse_image(resp.json())
            if not img_bytes:
                msg_content = resp.json()["choices"][0]["message"].get("content", "")
                print(f"{short_name:<28} {'no_image':>8}  {'':>14}  {'':>6}  {elapsed:>5.1f}s  <-{str(msg_content)[:60]}")
                results.append({"model": short_name, "status": "no_image"})
                continue

            out_path = OUTPUT_DIR / f"{short_name}.jpg"
            result_img = Image.open(BytesIO(img_bytes)).convert("RGB")
            result_img.save(out_path, "JPEG", quality=95)
            size_str = f"{result_img.size[0]}x{result_img.size[1]}"
            kb = out_path.stat().st_size // 1024
            print(f"{short_name:<28} {'OK':>8}  {size_str:>14}  {kb:>6}  {elapsed:>5.1f}s")
            results.append({"model": short_name, "status": "ok", "size": result_img.size, "kb": kb, "time": elapsed})

        except Exception as e:
            elapsed = time.time() - t0
            print(f"{short_name:<28} {'ERROR':>8}  {'':>14}  {'':>6}  {elapsed:>5.1f}s  <-{str(e)[:60]}")
            results.append({"model": short_name, "status": "error", "error": str(e)})

        time.sleep(0.5)

    client.close()

    print("\n" + "-" * 70)
    ok = [r for r in results if r.get("status") == "ok"]
    print(f"Done: {len(ok)}/{len(MODELS)} models produced an image")
    print(f"Results saved to: {OUTPUT_DIR}/")
    print("\nOpen the folder and compare the images manually.")


if __name__ == "__main__":
    main()
