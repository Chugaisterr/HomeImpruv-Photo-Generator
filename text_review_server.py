"""
Text Removal Review Server — Tinder-style.

Flow:
  1. Finds photos with has_text_overlay=True from rename_log.csv
  2. Processes BATCH_SIZE at a time with Riverflow (text removal)
  3. Serves Tinder UI: ← reject | → approve
  4. Approved → replaces original in Media/
  5. Automatically processes next batch after each review round

Run:  python text_review_server.py
Open: http://localhost:8082
"""
import base64
import json
import logging
import os
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PORT       = 8082
MEDIA_DIR  = Path("Media")
BATCH_SIZE = 5          # process N at a time before showing review UI
REMOVAL_PROMPT = """\
This home improvement photo has overlaid text labels and a company logo/watermark.
Inpaint them out naturally:
1. Remove ALL overlaid text ("Before", "After", captions, titles)
2. Remove ALL logos, brand marks, watermarks by inpainting with background
3. If the photo has white dividing lines or grid borders between sections — inpaint them out seamlessly
4. Reconstruct the underlying scene naturally (siding, walls, sky, surfaces)
5. Do NOT change colors, composition or any actual construction content
Return ONE clean image without any text, logo overlays or white dividers.
"""
MAX_INPUT_PX = 1536

# ── State ─────────────────────────────────────────────────────────────────────

state = {
    "photos": [],       # list of {id, original, processed, status, approved}
    "processing": False,
    "current_batch": 0,
}

# ── Find text photos ──────────────────────────────────────────────────────────

def find_text_photos() -> list[Path]:
    """Read rename_log.csv, return paths of photos with has_text_overlay=True."""
    photos = []
    log_path = MEDIA_DIR / "rename_log.csv"
    if not log_path.exists():
        logger.warning("rename_log.csv not found, scanning classifications.json")
        return []

    import csv
    with open(log_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("has_text_overlay", "").strip().lower() == "true":
                target = Path(row["target"])
                if target.exists():
                    photos.append(target)
                else:
                    logger.warning(f"Not found: {target}")
    return photos


# ── Image helpers ─────────────────────────────────────────────────────────────

def _compress(path: Path) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_INPUT_PX:
            scale = MAX_INPUT_PX / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode()


def _parse_image(rj: dict):
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


def process_photo(photo_id: int, api_key: str):
    """Process one photo via Riverflow text removal. Updates state in-place."""
    entry = state["photos"][photo_id]
    orig_path = Path(entry["original"])
    proc_path = Path(entry["processed"])
    proc_path.parent.mkdir(parents=True, exist_ok=True)

    entry["status"] = "processing"
    logger.info(f"  Processing [{photo_id+1}]: {orig_path.name}")

    try:
        b64 = _compress(orig_path)
        client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://homeimpruv.com",
                "X-Title": "HomeImpruv Text Removal",
            },
            timeout=180.0,
        )

        for attempt in range(3):
            try:
                resp = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json={
                        "model": "sourceful/riverflow-v2-fast",
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": REMOVAL_PROMPT},
                        ]}],
                        "max_tokens": 4096,
                    }
                )
                if resp.status_code != 200:
                    err = resp.json().get("error", {}).get("message", resp.text[:60])
                    raise Exception(f"HTTP {resp.status_code}: {err}")

                img_bytes = _parse_image(resp.json())
                if not img_bytes:
                    raise Exception("No image in response")

                proc_path.write_bytes(img_bytes)
                entry["status"] = "ready"
                logger.info(f"  Done [{photo_id+1}]: {orig_path.name}")
                client.close()
                return
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"  Retry {attempt+1}: {e}")
                    time.sleep(2 ** attempt)
                else:
                    raise
        client.close()

    except Exception as e:
        entry["status"] = "error"
        entry["error"] = str(e)
        logger.error(f"  Error [{photo_id+1}] {orig_path.name}: {e}")


def process_batch(batch_ids: list[int], api_key: str):
    """Process a batch of photos in parallel threads."""
    state["processing"] = True
    threads = []
    for pid in batch_ids:
        t = threading.Thread(target=process_photo, args=(pid, api_key), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    state["processing"] = False
    logger.info(f"Batch complete: {len(batch_ids)} photos processed")


# ── HTML UI ───────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Text Removal Review</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d0d; color: #fff; font-family: system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

/* Top bar */
#topbar { display: flex; align-items: center; justify-content: space-between; padding: 12px 20px; background: #111; border-bottom: 1px solid #1e1e1e; flex-shrink: 0; }
#filename { font-size: 13px; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 400px; }
#counter-badge { background: #1e1e1e; border-radius: 20px; padding: 4px 14px; font-size: 13px; color: #aaa; }
#progress-bar { flex: 1; height: 3px; background: #1e1e1e; border-radius: 2px; margin: 0 16px; }
#progress-fill { height: 3px; background: linear-gradient(90deg, #00c853, #00bcd4); border-radius: 2px; transition: width 0.4s ease; }

/* Main layout */
#main { flex: 1; display: flex; gap: 2px; overflow: hidden; position: relative; }

.panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.panel-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 14px; background: #111; flex-shrink: 0; }
.panel-title { font-size: 10px; letter-spacing: 2.5px; text-transform: uppercase; font-weight: 600; }
.panel-title.orig { color: #ef5350; }
.panel-title.proc { color: #00c853; }
.panel-meta { font-size: 11px; color: #444; }

.img-area { flex: 1; background: #0a0a0a; display: flex; align-items: center; justify-content: center; cursor: zoom-in; overflow: hidden; position: relative; }
.img-area img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; transition: transform 0.2s; }
.img-area img.zoomed { transform: scale(2); cursor: zoom-out; }
.spinner { width: 36px; height: 36px; border: 3px solid #222; border-top-color: #00c853; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.error-msg { color: #f44336; font-size: 12px; padding: 20px; text-align: center; }

.divider { width: 2px; background: #1a1a1a; flex-shrink: 0; }

/* Swipe overlays */
#ov-reject  { position: absolute; inset: 0; background: rgba(239,83,80,.12); border: 3px solid #ef5350; pointer-events: none; display: none; z-index: 10; }
#ov-approve { position: absolute; inset: 0; background: rgba(0,200,83,.12); border: 3px solid #00c853; pointer-events: none; display: none; z-index: 10; }

/* Bottom bar */
#bottombar { display: flex; align-items: center; justify-content: center; gap: 24px; padding: 14px 20px; background: #111; border-top: 1px solid #1e1e1e; flex-shrink: 0; }
.btn-decide { width: 60px; height: 60px; border-radius: 50%; border: 2px solid; font-size: 22px; cursor: pointer; transition: all 0.15s; display: flex; align-items: center; justify-content: center; background: transparent; }
#btn-reject  { border-color: #ef5350; color: #ef5350; }
#btn-approve { border-color: #00c853; color: #00c853; }
#btn-reject:hover  { background: #ef5350; color: #fff; transform: scale(1.12); }
#btn-approve:hover { background: #00c853; color: #fff; transform: scale(1.12); }
.btn-label { font-size: 10px; color: #333; text-align: center; line-height: 1.4; }
.btn-group { display: flex; flex-direction: column; align-items: center; gap: 6px; }
#hint-key { font-size: 11px; color: #2a2a2a; }

/* Done screen */
#done-screen { display: none; flex-direction: column; align-items: center; justify-content: center; gap: 16px; flex: 1; }
#done-screen h2 { font-size: 26px; }
#done-screen .sub { color: #666; font-size: 14px; }
#results-list { list-style: none; font-size: 13px; max-height: 280px; overflow-y: auto; width: 400px; }
#results-list li { padding: 6px 12px; border-bottom: 1px solid #1a1a1a; display: flex; gap: 10px; align-items: center; }
#results-list li.approved { color: #00c853; }
#results-list li.rejected { color: #444; text-decoration: line-through; }
#results-list .res-icon { font-size: 14px; }
</style>
</head>
<body>

<div id="topbar">
  <div id="filename">Loading...</div>
  <div id="progress-bar"><div id="progress-fill" style="width:0%"></div></div>
  <div id="counter-badge">0 / 0</div>
</div>

<div id="main">
  <div class="panel" id="panel-orig">
    <div class="panel-header">
      <span class="panel-title orig">ORIGINAL</span>
      <span class="panel-meta" id="meta-orig">—</span>
    </div>
    <div class="img-area" id="orig-area"><div class="spinner"></div></div>
  </div>

  <div class="divider"></div>

  <div class="panel" id="panel-proc">
    <div class="panel-header">
      <span class="panel-title proc">TEXT REMOVED</span>
      <span class="panel-meta" id="meta-proc">—</span>
    </div>
    <div class="img-area" id="proc-area"><div class="spinner"></div></div>
  </div>

  <div id="ov-reject"></div>
  <div id="ov-approve"></div>
</div>

<div id="bottombar">
  <div class="btn-group">
    <button class="btn-decide" id="btn-reject">&#x2715;</button>
    <span class="btn-label">Reject<br><span style="color:#2a2a2a">&#8592; arrow</span></span>
  </div>
  <span id="hint-key">Click image to zoom</span>
  <div class="btn-group">
    <button class="btn-decide" id="btn-approve">&#x2713;</button>
    <span class="btn-label">Approve<br><span style="color:#2a2a2a">&#8594; arrow</span></span>
  </div>
</div>

<div id="done-screen">
  <h2>Review complete</h2>
  <p class="sub" id="done-stats"></p>
  <ul id="results-list"></ul>
</div>

<script>
let photos = [], current = 0;

async function loadPhotos() {
  const r = await fetch('/api/photos');
  photos = await r.json();
  showCurrent();
  pollProcessing();
}

function pollProcessing() {
  if (!photos.some(p => p.status === 'processing' || p.status === 'pending')) return;
  setTimeout(async () => {
    const r = await fetch('/api/photos');
    const updated = await r.json();
    // merge status only
    updated.forEach((u, i) => { if (photos[i]) photos[i].status = u.status; });
    if (photos[current] && (photos[current].status === 'ready' || photos[current].status === 'error')) showCurrent();
    pollProcessing();
  }, 1500);
}

async function fetchInfo(path) {
  try {
    const r = await fetch('/api/info/' + encodeURIComponent(path));
    if (r.ok) return await r.json();
  } catch(e) {}
  return null;
}

function fmt(info) {
  if (!info) return '—';
  const mp = (info.w * info.h / 1e6).toFixed(1);
  const kb = info.kb >= 1024 ? (info.kb/1024).toFixed(1) + ' MB' : info.kb + ' KB';
  return `${info.w} × ${info.h}px  •  ${mp}MP  •  ${kb}`;
}

function makeZoomable(area) {
  area.addEventListener('click', () => {
    const img = area.querySelector('img');
    if (!img) return;
    img.classList.toggle('zoomed');
  });
}

function showCurrent() {
  if (current >= photos.length) { showDone(); return; }
  const p = photos[current];
  const name = p.original.split(/[\\/]/).pop();

  document.getElementById('filename').textContent = name;
  document.getElementById('counter-badge').textContent = `${current + 1} / ${photos.length}`;
  document.getElementById('progress-fill').style.width = `${(current / photos.length) * 100}%`;
  document.getElementById('ov-reject').style.display = 'none';
  document.getElementById('ov-approve').style.display = 'none';
  document.getElementById('meta-orig').textContent = '—';
  document.getElementById('meta-proc').textContent = '—';

  const origArea = document.getElementById('orig-area');
  const procArea = document.getElementById('proc-area');

  // Original
  origArea.innerHTML = `<img src="/img/original/${encodeURIComponent(p.original)}" alt="orig">`;
  makeZoomable(origArea);
  fetchInfo(p.original).then(info => {
    document.getElementById('meta-orig').textContent = fmt(info);
  });

  // Processed
  if (p.status === 'ready') {
    procArea.innerHTML = `<img src="/img/processed/${encodeURIComponent(p.processed)}?t=${Date.now()}" alt="proc">`;
    makeZoomable(procArea);
    fetchInfo(p.processed).then(info => {
      document.getElementById('meta-proc').textContent = fmt(info);
    });
  } else if (p.status === 'error') {
    procArea.innerHTML = `<div class="error-msg">Error: ${p.error || 'failed'}<br><small>Will be skipped</small></div>`;
  } else {
    procArea.innerHTML = `<div class="spinner"></div>`;
    const poll = setInterval(async () => {
      const r = await fetch('/api/photos');
      const updated = await r.json();
      const cur = updated[current];
      if (cur && (cur.status === 'ready' || cur.status === 'error')) {
        clearInterval(poll);
        photos[current] = cur;
        showCurrent();
      }
    }, 1500);
  }
}

function showDone() {
  document.getElementById('topbar').style.display = 'none';
  document.getElementById('main').style.display = 'none';
  document.getElementById('bottombar').style.display = 'none';
  document.getElementById('done-screen').style.display = 'flex';
  const approved = photos.filter(p => p.approved === true).length;
  const rejected = photos.filter(p => p.approved === false).length;
  document.getElementById('done-stats').textContent = `${approved} approved  •  ${rejected} rejected`;
  const list = document.getElementById('results-list');
  photos.forEach(p => {
    const li = document.createElement('li');
    li.className = p.approved ? 'approved' : 'rejected';
    const name = p.original.split(/[\\/]/).pop();
    li.innerHTML = `<span class="res-icon">${p.approved ? '&#x2713;' : '&#x2715;'}</span> ${name}`;
    list.appendChild(li);
  });
}

async function decide(approve) {
  const p = photos[current];
  if (!p || p.status === 'processing' || p.status === 'pending') return;
  const ovl = document.getElementById(approve ? 'ov-approve' : 'ov-reject');
  ovl.style.display = 'block';
  await fetch(`/api/decide/${p.id}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({approve})
  });
  photos[current].approved = approve;
  current++;
  setTimeout(() => { ovl.style.display = 'none'; showCurrent(); pollProcessing(); }, 220);
}

document.getElementById('btn-reject').addEventListener('click', () => decide(false));
document.getElementById('btn-approve').addEventListener('click', () => decide(true));
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight') decide(true);
  if (e.key === 'ArrowLeft') decide(false);
});

loadPhotos();
</script>
</body>
</html>
"""

# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress request logs

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
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
        ext = path.suffix.lower()
        ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                 "webp": "image/webp"}.get(ext.lstrip("."), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/":
            self.send_html(HTML)

        elif path == "/api/photos":
            self.send_json([{
                "id": i,
                "original": str(p["original"]),
                "processed": str(p["processed"]),
                "status": p["status"],
                "approved": p.get("approved"),
                "error": p.get("error"),
            } for i, p in enumerate(state["photos"])])

        elif path.startswith("/img/original/"):
            rel = path[len("/img/original/"):]
            from urllib.parse import unquote
            self.send_file(Path(unquote(rel)))

        elif path.startswith("/img/processed/"):
            rel = path[len("/img/processed/"):]
            from urllib.parse import unquote
            self.send_file(Path(unquote(rel)))

        elif path.startswith("/api/info/"):
            from urllib.parse import unquote
            rel = unquote(path[len("/api/info/"):])
            p = Path(rel)
            if p.exists():
                try:
                    with Image.open(p) as img:
                        w, h = img.size
                    kb = p.stat().st_size // 1024
                    self.send_json({"w": w, "h": h, "kb": kb})
                except Exception as e:
                    self.send_json({"error": str(e)}, 500)
            else:
                self.send_json({"error": "not found"}, 404)

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        path = self.path

        if path.startswith("/api/decide/"):
            photo_id = int(path.split("/")[-1])
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            approve = body.get("approve", False)
            entry = state["photos"][photo_id]
            entry["approved"] = approve

            if approve and entry["status"] == "ready":
                # Replace original with processed
                proc = Path(entry["processed"])
                orig = Path(entry["original"])
                shutil.copy2(proc, orig)
                logger.info(f"  APPROVED → replaced {orig.name}")
            else:
                logger.info(f"  REJECTED → kept {Path(entry['original']).name}")

            self.send_json({"ok": True})
        else:
            self.send_response(404); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set in .env")
        return

    # Find text photos
    text_photos = find_text_photos()
    if not text_photos:
        print("No photos with has_text_overlay=True found in rename_log.csv")
        return

    print(f"\nFound {len(text_photos)} photos with text overlays:")
    for p in text_photos:
        print(f"  {p}")

    # Setup state
    proc_dir = Path("Media_text_removed")
    proc_dir.mkdir(exist_ok=True)

    for i, orig in enumerate(text_photos):
        proc = proc_dir / orig.relative_to(MEDIA_DIR).with_suffix(".jpg")
        state["photos"].append({
            "original": str(orig),
            "processed": str(proc),
            "status": "ready" if proc.exists() else "pending",
            "approved": None,
        })

    # Start processing in background
    def bg_process():
        pending = [i for i, p in enumerate(state["photos"]) if p["status"] == "pending"]
        if pending:
            logger.info(f"\nProcessing {len(pending)} photos in background...")
            process_batch(pending, api_key)

    threading.Thread(target=bg_process, daemon=True).start()

    # Start server
    server = HTTPServer(("localhost", PORT), Handler)
    print(f"\n{'='*50}")
    print(f"  Text Removal Review — Tinder Mode")
    print(f"  http://localhost:{PORT}")
    print(f"  <- Reject | -> Approve")
    print(f"  Ctrl+C to stop")
    print(f"{'='*50}\n")

    import webbrowser
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
