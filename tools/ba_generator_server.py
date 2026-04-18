"""
Before/After Generator Server — автоматична генерація "after" фото з "before".

Flow:
  1. Знаходить всі before фото з dataset.json
  2. Генерує after для кожного через Gemini (фоновий батч, 5 воркерів)
  3. Tinder UI: показує before | generated after поруч
  4. Approve → зберігає як пару в Media/niche/ba/
     Reject  → видаляє, можна retry
     Retry   → генерує знову з іншим seed

Run:  python tools/ba_generator_server.py
Open: http://localhost:8085
"""
import base64
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path

from PIL import Image
from dotenv import load_dotenv
import httpx

load_dotenv()
logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PORT         = 8085
MEDIA_DIR    = Path("Media")
STAGING_DIR  = Path("Media_ba_generated")   # тимчасово до апруву
DATASET_FILE = Path("dataset.json")
WORKERS      = 5
MAX_INPUT_PX = 1536
MODEL        = "google/gemini-2.5-flash-image"

# ── Промпти по нішах ──────────────────────────────────────────────────────────
# Ключові правила:
#  - Ніколи "remove watermark/logo" → safety filter
#  - Завжди "KEEP IDENTICAL" для консистентності
#  - Вказувати конкретні матеріали, не абстракції
#  - Фінальний рядок: мета для контексту моделі

AFTER_PROMPTS = {
    "bathroom": """\
This is a BEFORE photo of a bathroom. Generate a professional AFTER photo \
showing the same space fully renovated.

KEEP IDENTICAL: camera angle, room dimensions, window position and size, \
ceiling height, natural light direction, door location.

RENOVATE:
- Replace old tile with large-format porcelain (60x60cm, light warm grey)
- Install frameless glass shower enclosure or tub surround
- Add floating vanity with undermount sink, quartz countertop
- Install LED backlit mirror, matte black or brushed nickel fixtures
- Clean painted walls (soft white), recessed lighting

PHOTO STYLE: neutral daylight 5500K, lifted shadows, sharp tile grout \
and fixture detail. No people, no text. \
Suitable for Houzz / contractor portfolio / Google Business listing.""",

    "shower": """\
This is a BEFORE photo of a shower or bathroom. Generate a professional \
AFTER photo showing a completed walk-in shower conversion.

KEEP IDENTICAL: camera angle, room dimensions, window position, \
ceiling height, light direction.

RENOVATE:
- Remove old tub or dated shower, install walk-in shower
- Large-format subway tile or porcelain slab walls (floor-to-ceiling)
- Frameless glass panel or door, linear drain
- Niche shelf built into wall, rainfall showerhead
- Clean grout lines, matte black or chrome fixtures

PHOTO STYLE: bright even lighting, sharp tile and glass detail, \
neutral 5500K. No people, no text. Suitable for contractor portfolio.""",

    "flooring": """\
This is a BEFORE photo of a room with old flooring. Generate a professional \
AFTER photo showing new flooring installed throughout.

KEEP IDENTICAL: camera angle, room layout, walls, windows, ceiling, \
furniture silhouettes if present, light direction.

RENOVATE:
- Replace old carpet, linoleum or damaged wood with wide-plank LVP \
or hardwood (light oak or warm walnut tone)
- Clean transitions at doorways
- Baseboards freshly painted white

PHOTO STYLE: wide room view showing full floor area, neutral daylight, \
sharp wood grain detail. No people, no text. \
Suitable for flooring contractor portfolio.""",

    "hvac": """\
This is a BEFORE photo showing an old or absent HVAC system. Generate \
a professional AFTER photo showing a completed mini-split installation.

KEEP IDENTICAL: camera angle, room or exterior wall layout, \
window positions, ceiling height.

SHOW IN AFTER:
- White mini-split indoor unit mounted high on wall, optimal position
- Copper line set neatly run along wall with line hide cover
- Clean installation, no exposed wiring
- Outdoor condenser unit visible if exterior shot (level pad, proper clearance)

PHOTO STYLE: clean interior or exterior light, sharp equipment detail, \
neutral white balance. No people, no text. \
Suitable for HVAC contractor portfolio / Google Business.""",

    "siding": """\
This is a BEFORE photo of a house exterior with old or damaged siding. \
Generate a professional AFTER photo showing completed siding replacement.

KEEP IDENTICAL: house shape, roofline, window positions and sizes, \
driveway, landscaping, trees, camera angle and distance from house.

RENOVATE:
- Replace old siding with James Hardie fiber cement lap siding \
(Monterey Taupe, Cobblestone or Arctic White)
- Clean white trim around windows and corners
- Updated front door if visible (dark navy or black)
- Remove any visible damage, peeling paint, or staining

PHOTO STYLE: golden hour or bright overcast light, slight sky \
enhancement, sharp facade texture detail. No people, no text. \
Suitable for exterior contractor portfolio / Houzz.""",

    "security": """\
This is a BEFORE photo of a home exterior or interior area without \
a security system. Generate a professional AFTER photo showing \
completed security camera installation.

KEEP IDENTICAL: camera angle, wall or exterior surface, \
architecture, surrounding area.

ADD IN AFTER:
- 1-2 professional dome or bullet security cameras (white or grey)
- Mounted at optimal height and angle (corner position if possible)
- Clean wire routing or wireless installation
- Small junction box if applicable, neatly mounted

PHOTO STYLE: clean natural or artificial light, sharp camera detail, \
neutral white balance. No people, no text. \
Suitable for security contractor portfolio.""",
}

DEFAULT_PROMPT = """\
This is a BEFORE photo. Generate a professional AFTER photo showing \
the same space after a high-quality renovation.
KEEP IDENTICAL: camera angle, room layout, light direction.
STYLE: neutral daylight 5500K, sharp detail, contractor portfolio quality.
No people, no text overlays."""


# ── State ─────────────────────────────────────────────────────────────────────

state = {
    "entries":    [],   # list of {before, staged, niche, seq, status, error}
    "processing": False,
    "done":       False,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_before_photos() -> list[dict]:
    """Load all before photos from dataset.json."""
    if not DATASET_FILE.exists():
        logger.error("dataset.json not found — run build_dataset_local.py first")
        return []
    with open(DATASET_FILE, encoding="utf-8") as f:
        data = json.load(f)
    entries = []
    for img in data["images"]:
        if img.get("subtype") == "before":
            before_path = MEDIA_DIR / img["path"].replace("\\", "/")
            if before_path.exists():
                entries.append({
                    "before":  str(before_path),
                    "niche":   img["niche"],
                    "use":     img["use"],
                    "subtype": img["subtype"],
                    "seq":     img["path"].split("_")[-1].replace(".jpg","").replace(".jpeg",""),
                    "caption": img.get("caption", ""),
                    "status":  "pending",
                    "error":   "",
                    "staged":  "",
                })
    return entries


def compress(path: Path, max_px: int = MAX_INPUT_PX) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode()


def parse_image(rj: dict):
    """3-level fallback parser for OpenRouter image response."""
    try:
        msg = rj["choices"][0]["message"]
        for img in msg.get("images", []):
            url = img.get("image_url", {}).get("url", "") or img.get("url", "")
            if url.startswith("data:image"):
                return base64.b64decode(url.split(",", 1)[1])
        content = msg.get("content", "")
        if isinstance(content, list):
            for item in content:
                if item.get("type") == "image_url":
                    url = item["image_url"]["url"]
                    if url.startswith("data:image"):
                        return base64.b64decode(url.split(",", 1)[1])
        if isinstance(content, str):
            m = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", content)
            if m:
                return base64.b64decode(m.group(1))
    except Exception:
        pass
    return None


def staged_path(entry: dict) -> Path:
    niche = entry["niche"]
    seq   = entry["seq"]
    name  = f"{niche}_ba_after_{seq}_generated.jpg"
    return STAGING_DIR / niche / name


def generate_after(entry: dict, api_key: str):
    """Call Gemini to generate after photo from before."""
    before = Path(entry["before"])
    out    = staged_path(entry)
    out.parent.mkdir(parents=True, exist_ok=True)
    entry["staged"] = str(out)
    entry["status"] = "processing"

    # Resume — already generated
    if out.exists():
        entry["status"] = "ready"
        logger.info(f"  Resume: {before.name}")
        return

    prompt = AFTER_PROMPTS.get(entry["niche"], DEFAULT_PROMPT)

    try:
        b64 = compress(before)
        client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://homeimpruv.com",
                "X-Title": "HomeImpruv BA Generator",
            },
            timeout=180.0,
        )
        for attempt in range(3):
            try:
                resp = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json={
                        "model": MODEL,
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": prompt},
                        ]}],
                        "max_tokens": 4096,
                    }
                )
                if resp.status_code != 200:
                    err = resp.json().get("error", {}).get("message", resp.text[:120])
                    raise Exception(f"HTTP {resp.status_code}: {err}")

                img_bytes = parse_image(resp.json())
                if not img_bytes:
                    raise Exception("No image in response")

                out.write_bytes(img_bytes)
                entry["status"] = "ready"
                logger.info(f"  Generated: {before.name} -> {out.name}")
                client.close()
                return

            except Exception as e:
                if attempt < 2:
                    logger.warning(f"  Retry {attempt+1} ({before.name}): {e}")
                    time.sleep(2 ** attempt)
                else:
                    raise
        client.close()

    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)
        logger.error(f"  Error {before.name}: {e}")


def process_all_async(api_key: str):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pending = [e for e in state["entries"] if e["status"] == "pending"]
    logger.info(f"Generating {len(pending)} after photos ({WORKERS} workers)...")
    state["processing"] = True

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(generate_after, e, api_key): e for e in pending}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as ex:
                logger.error(f"Worker error: {ex}")

    state["processing"] = False
    state["done"] = True
    ready = sum(1 for e in state["entries"] if e["status"] == "ready")
    logger.info(f"Generation done: {ready}/{len(state['entries'])} ready for review")


def save_approved(entry: dict) -> Path:
    """Move approved generated after to proper Media/niche/ba/ location."""
    niche = entry["niche"]
    seq   = entry["seq"]
    dest_dir = MEDIA_DIR / niche / "ba"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Find next available seq for after
    prefix   = f"{niche}_ba_after_"
    existing = list(dest_dir.glob(f"{prefix}*.jpg"))
    new_seq  = len(existing) + 1
    dest     = dest_dir / f"{prefix}{new_seq:03d}.jpg"
    while dest.exists():
        new_seq += 1
        dest = dest_dir / f"{prefix}{new_seq:03d}.jpg"

    # Save as proper JPEG
    with Image.open(entry["staged"]) as img:
        img.convert("RGB").save(dest, "JPEG", quality=95)

    # Clean up staged file
    Path(entry["staged"]).unlink(missing_ok=True)
    logger.info(f"  Approved: saved as {dest.name}")
    return dest


# ── HTML UI ───────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>BA Generator Review</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0a; color: #fff; font-family: system-ui, sans-serif; min-height: 100vh; }

#header { padding: 14px 24px; background: #111; border-bottom: 1px solid #1e1e1e;
          display: flex; align-items: center; gap: 16px; }
#header h1 { font-size: 15px; font-weight: 600; }
.badge { font-size: 11px; padding: 3px 10px; border-radius: 10px; font-weight: 700;
         background: #1a2a3a; color: #4fc3f7; }
#progress { margin-left: auto; font-size: 12px; color: #00c853; }

#grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(560px, 1fr));
        gap: 20px; padding: 20px; }

.card { background: #111; border-radius: 14px; overflow: hidden;
        border: 1px solid #1e1e1e; transition: border-color 0.2s; }
.card.approved { border-color: #00c853; opacity: 0.55; }
.card.rejected { border-color: #f44336; opacity: 0.35; }
.card.error    { border-color: #ff9800; }

.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 2px; background: #1e1e1e; }
.side { position: relative; height: 220px; background: #080808;
        display: flex; align-items: center; justify-content: center; cursor: pointer; }
.side img { max-width: 100%; max-height: 100%; object-fit: contain; }
.side:hover img { transform: scale(1.02); transition: transform .15s; }
.side-label { position: absolute; bottom: 6px; left: 8px; font-size: 10px;
              font-weight: 700; padding: 2px 8px; border-radius: 8px; }
.label-before { background: #333; color: #aaa; }
.label-after  { background: #1a3a2a; color: #00c853; }
.label-loading { background: #1a1a2a; color: #4fc3f7; }
.label-error  { background: #3a1a1a; color: #f44336; }

.info { padding: 10px 14px; }
.meta { font-size: 11px; color: #555; margin-bottom: 10px; }
.meta span { color: #888; margin-right: 12px; }

.actions { display: flex; gap: 8px; }
.btn-approve { flex: 1; background: #00c853; color: #000; border: none;
               border-radius: 8px; padding: 8px; font-size: 13px;
               font-weight: 700; cursor: pointer; }
.btn-approve:hover { background: #00e676; }
.btn-reject  { flex: 0 0 80px; background: #1a1a1a; color: #666;
               border: 1px solid #2a2a2a; border-radius: 8px;
               padding: 8px; font-size: 12px; cursor: pointer; }
.btn-reject:hover { color: #f44336; border-color: #f44336; }
.btn-retry   { flex: 0 0 80px; background: #1a1a1a; color: #666;
               border: 1px solid #2a2a2a; border-radius: 8px;
               padding: 8px; font-size: 12px; cursor: pointer; }
.btn-retry:hover { color: #4fc3f7; border-color: #4fc3f7; }

.status-msg { font-size: 11px; color: #00c853; margin-top: 6px; min-height: 14px; }

#lightbox { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.94);
            z-index: 100; align-items: center; justify-content: center; cursor: zoom-out; }
#lightbox img { max-width: 96vw; max-height: 96vh; object-fit: contain; border-radius: 4px; }

.spinner { width: 24px; height: 24px; border: 3px solid #1e1e1e;
           border-top-color: #4fc3f7; border-radius: 50%;
           animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div id="header">
  <h1>BA Generator Review</h1>
  <span class="badge">Gemini</span>
  <span id="gen-status" style="font-size:12px;color:#555;">Loading...</span>
  <span id="progress">0 / 0 approved</span>
</div>
<div id="grid"></div>
<div id="lightbox"><img id="lb-img" src=""></div>

<script>
let entries = [], approvedCount = 0;

async function load() {
  const r = await fetch('/api/entries');
  const d = await r.json();
  entries = d.entries;
  updateGenStatus(d.processing, d.done);
  render();
  if (!d.done) setTimeout(pollStatus, 3000);
}

async function pollStatus() {
  const r = await fetch('/api/entries');
  const d = await r.json();
  entries = d.entries;
  updateGenStatus(d.processing, d.done);
  render();
  if (!d.done) setTimeout(pollStatus, 3000);
}

function updateGenStatus(processing, done) {
  const el = document.getElementById('gen-status');
  const ready = entries.filter(e => e.status === 'ready').length;
  const total = entries.length;
  if (done)        el.textContent = `All ${total} generated`;
  else if (processing) el.textContent = `Generating... ${ready}/${total} ready`;
  else             el.textContent = `${ready}/${total} ready`;
}

function render() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  entries.forEach((e, i) => {
    const card = document.createElement('div');
    card.className = 'card' + (e.reviewed === 'approved' ? ' approved' : '')
                             + (e.reviewed === 'rejected' ? ' rejected' : '')
                             + (e.status === 'error'    ? ' error' : '');
    card.id = 'card-' + i;

    const beforeSrc = e.status !== 'pending'
      ? `/img/${encodeURIComponent(e.before)}` : '';
    const afterSrc  = (e.status === 'ready' || e.status === 'approved')
      ? `/img/${encodeURIComponent(e.staged)}` : '';

    const afterContent = e.status === 'ready' || e.reviewed
      ? `<img src="${afterSrc}" alt="" onclick="openLb('${encodeURIComponent(e.staged)}')">`
      : e.status === 'processing'
        ? `<div class="spinner"></div>`
        : e.status === 'error'
          ? `<span style="font-size:11px;color:#f44336;padding:8px">${e.error}</span>`
          : `<span style="font-size:11px;color:#444">pending...</span>`;

    const afterLabel = e.status === 'ready' ? 'AFTER (generated)'
      : e.status === 'processing' ? 'generating...'
      : e.status === 'error' ? 'ERROR'
      : 'pending';

    const labelClass = e.status === 'ready' ? 'label-after'
      : e.status === 'error' ? 'label-error' : 'label-loading';

    card.innerHTML = `
      <div class="pair">
        <div class="side">
          ${beforeSrc ? `<img src="${beforeSrc}" onclick="openLb('${encodeURIComponent(e.before)}')" alt="">` : ''}
          <span class="side-label label-before">BEFORE</span>
        </div>
        <div class="side">
          ${afterContent}
          <span class="side-label ${labelClass}">${afterLabel}</span>
        </div>
      </div>
      <div class="info">
        <div class="meta">
          <span>${e.niche}</span>
          <span>${e.use} / ${e.subtype}</span>
        </div>
        <div class="actions">
          <button class="btn-approve" onclick="approve(${i})"
            ${e.status !== 'ready' || e.reviewed ? 'disabled style="opacity:.4"' : ''}>
            Approve &rarr; Save
          </button>
          <button class="btn-retry" onclick="retry(${i})">Retry</button>
          <button class="btn-reject" onclick="reject(${i})">Reject</button>
        </div>
        <div class="status-msg" id="msg-${i}"></div>
      </div>
    `;
    grid.appendChild(card);
  });
  updateProgress();
}

async function approve(i) {
  const e = entries[i];
  const r = await fetch('/api/approve', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({index: i})
  });
  const res = await r.json();
  if (res.ok) {
    entries[i].reviewed = 'approved';
    document.getElementById('card-' + i).className = 'card approved';
    document.getElementById('msg-' + i).textContent = 'Saved: ' + res.saved;
    approvedCount++;
    updateProgress();
  } else {
    document.getElementById('msg-' + i).style.color = '#f44336';
    document.getElementById('msg-' + i).textContent = res.error;
  }
}

async function reject(i) {
  entries[i].reviewed = 'rejected';
  document.getElementById('card-' + i).className = 'card rejected';
  document.getElementById('msg-' + i).textContent = 'Rejected';
  await fetch('/api/reject', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({index: i})
  });
  updateProgress();
}

async function retry(i) {
  document.getElementById('msg-' + i).textContent = 'Retrying...';
  const r = await fetch('/api/retry', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({index: i})
  });
  const res = await r.json();
  if (res.ok) {
    entries[i].status = 'processing';
    entries[i].reviewed = null;
    render();
    setTimeout(pollStatus, 3000);
  }
}

function updateProgress() {
  const approved = entries.filter(e => e.reviewed === 'approved').length;
  const total    = entries.length;
  document.getElementById('progress').textContent = `${approved} / ${total} approved`;
}

function openLb(enc) {
  document.getElementById('lb-img').src = '/img/' + enc;
  document.getElementById('lightbox').style.display = 'flex';
}
document.getElementById('lightbox').addEventListener('click', () => {
  document.getElementById('lightbox').style.display = 'none';
});

load();
</script>
</body>
</html>
"""


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path):
        if not path.exists():
            self.send_response(404); self.end_headers(); return
        data = path.read_bytes()
        ext  = path.suffix.lower().lstrip(".")
        ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "png": "image/png",  "webp": "image/webp"}.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import unquote
        p = self.path.split("?")[0]

        if p == "/":
            self.send_html(HTML)

        elif p == "/api/entries":
            self.send_json({
                "entries":    state["entries"],
                "processing": state["processing"],
                "done":       state["done"],
            })

        elif p.startswith("/img/"):
            self.send_file(Path(unquote(p[5:])))

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length))
        p      = self.path

        if p == "/api/approve":
            idx   = body["index"]
            entry = state["entries"][idx]
            try:
                dest = save_approved(entry)
                entry["reviewed"] = "approved"
                self.send_json({"ok": True, "saved": dest.name})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})

        elif p == "/api/reject":
            idx   = body["index"]
            entry = state["entries"][idx]
            staged = Path(entry.get("staged", ""))
            if staged.exists():
                staged.unlink()
            entry["reviewed"] = "rejected"
            self.send_json({"ok": True})

        elif p == "/api/retry":
            idx    = body["index"]
            entry  = state["entries"][idx]
            staged = Path(entry.get("staged", ""))
            if staged.exists():
                staged.unlink()
            entry["status"]   = "pending"
            entry["reviewed"] = None
            entry["error"]    = ""
            api_key = os.getenv("OPENROUTER_API_KEY")
            if api_key:
                threading.Thread(
                    target=generate_after, args=(entry, api_key), daemon=True
                ).start()
            self.send_json({"ok": True})

        else:
            self.send_response(404); self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set in .env")
        return

    entries = load_before_photos()
    if not entries:
        print("No 'before' photos found in dataset.json")
        print("Run: python __main__.py classify  then  python tools/build_dataset_local.py")
        return

    state["entries"] = entries
    print(f"\nFound {len(entries)} before photos:")
    from collections import Counter
    c = Counter(e["niche"] for e in entries)
    for niche, count in c.most_common():
        print(f"  {niche}: {count}")

    print(f"\nModel: {MODEL}")
    print(f"Workers: {WORKERS}")
    print(f"Staging: {STAGING_DIR}/")

    # Start generation in background
    t = threading.Thread(target=process_all_async, args=(api_key,), daemon=True)
    t.start()

    import webbrowser
    server = HTTPServer(("localhost", PORT), Handler)
    print(f"\nOpen: http://localhost:{PORT}")
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
