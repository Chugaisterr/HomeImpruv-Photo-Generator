"""
AI Upscale Review Server — Tinder-style, batch processing.

Flow:
  1. Finds all photos < ai_max_mp in Media/
  2. Processes BATCH_SIZE at a time via Riverflow (AI upscale)
  3. Tinder UI: <- reject | -> approve  (per photo)
  4. After batch reviewed → auto-starts next batch
  5. Approved → saved to Media_enhanced/ (original aspect ratio, no borders)

Run:  python upscale_review_server.py
Open: http://localhost:8083
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

PORT        = 8083
MEDIA_DIR   = Path("Media")
OUTPUT_DIR  = Path("Media_enhanced")
REVIEW_BATCH = 10    # photos per review session (shown in UI at a time)
WORKERS      = 20    # concurrent API requests (2 workers x 10)
AI_MAX_MP    = 2.0
MAX_INPUT_PX = 1536
IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tiff"}

UPSCALE_PROMPT = """\
Enhance and upscale this home improvement photo professionally:
1. Increase sharpness and recover fine surface details (tiles, edges, fixtures, equipment)
2. Correct white balance to neutral daylight 5500K, lift shadows gently
3. Remove noise and JPEG compression artifacts
4. Inpaint any overlaid text, logos or watermarks naturally into the background
5. If the photo is a before/after collage with white dividing lines, grid borders or separators between sections — inpaint them out seamlessly so the image looks like one continuous scene
6. Do NOT change scene composition or add new elements
Return ONE enhanced high-quality image. Suitable for contractor portfolio and listing articles."""

# ── State ─────────────────────────────────────────────────────────────────────

state = {
    "all_photos": [],    # all paths to process
    "batches": [],       # list of batch dicts
    "current_batch": 0,
    "processing": False,
    "done": False,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_mp(path: Path) -> float:
    try:
        with Image.open(path) as img:
            return img.width * img.height / 1_000_000
    except Exception:
        return 0.0


def collect_photos() -> list[Path]:
    photos = []
    for p in sorted(MEDIA_DIR.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            if "_enhanced" not in p.stem:
                mp = get_mp(p)
                if mp < AI_MAX_MP:
                    photos.append(p)
    return photos


def dest_path(src: Path) -> Path:
    rel = src.relative_to(MEDIA_DIR)
    return OUTPUT_DIR / rel.with_suffix(".jpg")


def compress(path: Path) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_INPUT_PX:
            scale = MAX_INPUT_PX / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode()


def parse_image(rj: dict):
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


def process_photo(entry: dict, api_key: str):
    orig = Path(entry["original"])
    proc = Path(entry["processed"])
    proc.parent.mkdir(parents=True, exist_ok=True)
    entry["status"] = "processing"
    logger.info(f"  AI upscale: {orig.name}")

    # Skip if already done (resume)
    if proc.exists():
        entry["status"] = "ready"
        logger.info(f"  Resume: {orig.name} already done")
        return

    try:
        b64 = compress(orig)
        client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://homeimpruv.com",
                "X-Title": "HomeImpruv Upscale Review",
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
                            {"type": "text", "text": UPSCALE_PROMPT},
                        ]}],
                        "max_tokens": 4096,
                    }
                )
                if resp.status_code != 200:
                    err = resp.json().get("error", {}).get("message", resp.text[:80])
                    raise Exception(f"HTTP {resp.status_code}: {err}")
                img_bytes = parse_image(resp.json())
                if not img_bytes:
                    raise Exception("No image in response")
                proc.write_bytes(img_bytes)
                entry["status"] = "ready"
                logger.info(f"  Done: {orig.name}")
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
        logger.error(f"  Error {orig.name}: {e}")


def process_all_async(api_key: str):
    """Process ALL pending photos with WORKERS concurrent threads. Mark each batch as done when all its photos finish."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Collect all pending entries across all batches
    all_entries = [
        entry
        for batch in state["batches"]
        for entry in batch["photos"]
        if entry["status"] == "pending"
    ]

    logger.info(f"Starting processing pool: {len(all_entries)} photos, {WORKERS} workers")
    state["processing"] = True

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process_photo, entry, api_key): entry for entry in all_entries}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                future.result()
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)
            # Check if this entry's batch is fully done
            for batch in state["batches"]:
                if entry in batch["photos"]:
                    if all(e["status"] in ("ready", "error") for e in batch["photos"]):
                        batch["processing_done"] = True

    state["processing"] = False
    logger.info("All photos processed!")


# Keep for compatibility (called from /api/batch/<n>/done — no-op now)
def process_batch_async(batch_index: int, api_key: str):
    pass


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upscale Review</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d0d; color: #fff; font-family: system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

#topbar { display: flex; align-items: center; gap: 12px; padding: 10px 20px; background: #111; border-bottom: 1px solid #1e1e1e; flex-shrink: 0; }
#filename { font-size: 12px; color: #555; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
#batch-badge { background: #1a1a1a; border-radius: 20px; padding: 3px 12px; font-size: 11px; color: #666; white-space: nowrap; }
#total-badge { background: #1a1a1a; border-radius: 20px; padding: 3px 12px; font-size: 11px; color: #444; white-space: nowrap; }
#progress-bar { width: 160px; height: 3px; background: #1e1e1e; border-radius: 2px; flex-shrink: 0; }
#progress-fill { height: 3px; background: linear-gradient(90deg,#00c853,#00bcd4); border-radius: 2px; transition: width .4s; }

#main { flex: 1; display: flex; gap: 2px; overflow: hidden; position: relative; }
.panel { flex: 1; display: flex; flex-direction: column; }
.panel-header { display: flex; align-items: center; justify-content: space-between; padding: 7px 14px; background: #111; flex-shrink: 0; }
.panel-title { font-size: 10px; letter-spacing: 2px; text-transform: uppercase; font-weight: 600; }
.panel-title.orig { color: #ef5350; }
.panel-title.proc { color: #00c853; }
.panel-meta { font-size: 11px; color: #444; font-variant-numeric: tabular-nums; }
.img-area { flex: 1; background: #080808; display: flex; align-items: center; justify-content: center; cursor: zoom-in; overflow: hidden; }
.img-area img { max-width: 100%; max-height: 100%; object-fit: contain; transition: transform .2s; }
.img-area img.zoomed { transform: scale(2.2); cursor: zoom-out; }
.spinner { width: 32px; height: 32px; border: 3px solid #1e1e1e; border-top-color: #00c853; border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.error-msg { color: #f44336; font-size: 12px; padding: 20px; text-align: center; }
.divider { width: 2px; background: #1a1a1a; flex-shrink: 0; }
#ov-reject  { position: absolute; inset: 0; background: rgba(239,83,80,.1); border: 3px solid #ef5350; pointer-events: none; display: none; z-index: 10; }
#ov-approve { position: absolute; inset: 0; background: rgba(0,200,83,.1); border: 3px solid #00c853; pointer-events: none; display: none; z-index: 10; }

#bottombar { display: flex; align-items: center; justify-content: center; gap: 20px; padding: 12px 20px; background: #111; border-top: 1px solid #1e1e1e; flex-shrink: 0; }
.btn-group { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.btn-decide { width: 56px; height: 56px; border-radius: 50%; border: 2px solid; font-size: 20px; cursor: pointer; transition: all .15s; display: flex; align-items: center; justify-content: center; background: transparent; }
#btn-reject  { border-color: #ef5350; color: #ef5350; }
#btn-approve { border-color: #00c853; color: #00c853; }
#btn-reject:hover  { background: #ef5350; color: #fff; transform: scale(1.1); }
#btn-approve:hover { background: #00c853; color: #fff; transform: scale(1.1); }
.btn-label { font-size: 10px; color: #333; text-align: center; }

/* Processing screen */
#processing-screen { display: none; flex-direction: column; align-items: center; justify-content: center; gap: 20px; flex: 1; }
#processing-screen .big-spinner { width: 56px; height: 56px; border: 4px solid #1e1e1e; border-top-color: #00c853; border-radius: 50%; animation: spin .8s linear infinite; }
#processing-screen p { color: #555; font-size: 14px; }
#proc-filelist { font-size: 12px; color: #333; list-style: none; text-align: center; }
#proc-filelist li.done { color: #00c853; }
#proc-filelist li.processing { color: #ffb300; }

/* Done screen */
#done-screen { display: none; flex-direction: column; align-items: center; justify-content: center; gap: 16px; flex: 1; }
#done-screen h2 { font-size: 26px; }
#done-screen .sub { color: #666; font-size: 14px; }
#results-list { list-style: none; font-size: 12px; max-height: 300px; overflow-y: auto; width: 440px; }
#results-list li { padding: 5px 10px; border-bottom: 1px solid #1a1a1a; display: flex; gap: 8px; }
#results-list li.approved { color: #00c853; }
#results-list li.rejected { color: #333; text-decoration: line-through; }
</style>
</head>
<body>

<div id="topbar">
  <div id="filename">Loading...</div>
  <div id="progress-bar"><div id="progress-fill" style="width:0%"></div></div>
  <div id="batch-badge">Batch 0 / 0</div>
  <div id="total-badge">0 / 0 total</div>
</div>

<div id="main" style="display:none">
  <div class="panel">
    <div class="panel-header">
      <span class="panel-title orig">ORIGINAL</span>
      <span class="panel-meta" id="meta-orig">—</span>
    </div>
    <div class="img-area" id="orig-area"><div class="spinner"></div></div>
  </div>
  <div class="divider"></div>
  <div class="panel">
    <div class="panel-header">
      <span class="panel-title proc">AI ENHANCED</span>
      <span class="panel-meta" id="meta-proc">—</span>
    </div>
    <div class="img-area" id="proc-area"><div class="spinner"></div></div>
  </div>
  <div id="ov-reject"></div>
  <div id="ov-approve"></div>
</div>

<div id="bottombar" style="display:none">
  <div class="btn-group">
    <button class="btn-decide" id="btn-reject">&#x2715;</button>
    <span class="btn-label">Reject &#8592;</span>
  </div>
  <span style="font-size:11px;color:#2a2a2a">Click image to zoom</span>
  <div class="btn-group">
    <button class="btn-decide" id="btn-approve">&#x2713;</button>
    <span class="btn-label">Approve &#8594;</span>
  </div>
</div>

<div id="processing-screen">
  <div class="big-spinner"></div>
  <p id="proc-label">Processing batch...</p>
  <ul id="proc-filelist"></ul>
</div>

<div id="done-screen">
  <h2>All done!</h2>
  <p class="sub" id="done-stats"></p>
  <ul id="results-list"></ul>
</div>

<script>
let state = null;
let batchIndex = 0;
let photoIndex = 0;  // within current batch
let totalApproved = 0, totalReviewed = 0;

function showMain()       { document.getElementById('main').style.display='flex'; document.getElementById('bottombar').style.display='flex'; document.getElementById('processing-screen').style.display='none'; }
function showProcessing() { document.getElementById('main').style.display='none'; document.getElementById('bottombar').style.display='none'; document.getElementById('processing-screen').style.display='flex'; }
function showDone()       { document.getElementById('main').style.display='none'; document.getElementById('bottombar').style.display='none'; document.getElementById('processing-screen').style.display='none'; document.getElementById('done-screen').style.display='flex'; }

async function loadState() {
  const r = await fetch('/api/state');
  state = await r.json();
  // find first unreviewed batch
  batchIndex = state.batches.findIndex(b => !b.review_done);
  if (batchIndex === -1) { renderDone(); return; }
  photoIndex = 0;
  checkBatchReady();
}

function checkBatchReady() {
  const batch = state.batches[batchIndex];
  if (!batch) { renderDone(); return; }
  const allReady = batch.photos.every(p => p.status === 'ready' || p.status === 'error');
  if (allReady) {
    showMain();
    renderPhoto();
  } else {
    showProcessing();
    renderProcessingScreen(batch);
    setTimeout(async () => {
      const r = await fetch('/api/state');
      state = await r.json();
      checkBatchReady();
    }, 1500);
  }
}

function renderProcessingScreen(batch) {
  const total = state.total;
  const done_count = state.batches.slice(0, batchIndex).reduce((s,b) => s + b.photos.length, 0);
  document.getElementById('proc-label').textContent =
    `Processing batch ${batchIndex + 1} / ${state.batches.length}  (${done_count} / ${total} total done)`;
  const list = document.getElementById('proc-filelist');
  list.innerHTML = batch.photos.map(p => {
    const name = p.original.split(/[\\/]/).pop();
    const cls = p.status === 'ready' ? 'done' : (p.status === 'processing' ? 'processing' : '');
    const icon = p.status === 'ready' ? '✓ ' : (p.status === 'processing' ? '⟳ ' : '· ');
    return `<li class="${cls}">${icon}${name}</li>`;
  }).join('');
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
  const kb = info.kb >= 1024 ? (info.kb/1024).toFixed(1)+' MB' : info.kb+' KB';
  return `${info.w} x ${info.h}  •  ${mp}MP  •  ${kb}`;
}

function makeZoom(area) {
  area.addEventListener('click', () => {
    const img = area.querySelector('img');
    if (img) img.classList.toggle('zoomed');
  });
}

function renderPhoto() {
  const batch = state.batches[batchIndex];
  if (!batch || photoIndex >= batch.photos.length) {
    // batch fully reviewed — mark done and move to next
    markBatchDone();
    return;
  }
  const p = batch.photos[photoIndex];
  const name = p.original.split(/[\\/]/).pop();
  const batchDone = state.batches.slice(0, batchIndex).reduce((s,b) => s + b.photos.length, 0) + photoIndex;
  const total = state.total;

  document.getElementById('filename').textContent = name;
  document.getElementById('batch-badge').textContent = `Batch ${batchIndex+1} / ${state.batches.length}  (${photoIndex+1}/${batch.photos.length})`;
  document.getElementById('total-badge').textContent = `${batchDone+1} / ${total} total`;
  document.getElementById('progress-fill').style.width = `${((batchDone) / total) * 100}%`;
  document.getElementById('ov-reject').style.display = 'none';
  document.getElementById('ov-approve').style.display = 'none';
  document.getElementById('meta-orig').textContent = '—';
  document.getElementById('meta-proc').textContent = '—';

  const origArea = document.getElementById('orig-area');
  const procArea = document.getElementById('proc-area');

  origArea.innerHTML = `<img src="/img/original/${encodeURIComponent(p.original)}" alt="orig">`;
  makeZoom(origArea);
  fetchInfo(p.original).then(i => { document.getElementById('meta-orig').textContent = fmt(i); });

  if (p.status === 'ready') {
    procArea.innerHTML = `<img src="/img/processed/${encodeURIComponent(p.processed)}?t=${Date.now()}" alt="proc">`;
    makeZoom(procArea);
    fetchInfo(p.processed).then(i => { document.getElementById('meta-proc').textContent = fmt(i); });
  } else if (p.status === 'error') {
    procArea.innerHTML = `<div class="error-msg">Error: ${p.error||'failed'}</div>`;
  }
}

async function markBatchDone() {
  await fetch(`/api/batch/${batchIndex}/done`, { method: 'POST' });
  batchIndex++;
  photoIndex = 0;
  if (batchIndex >= state.batches.length) { renderDone(); return; }
  // reload state (next batch might be processing)
  const r = await fetch('/api/state');
  state = await r.json();
  checkBatchReady();
}

async function decide(approve) {
  const batch = state.batches[batchIndex];
  if (!batch) return;
  const p = batch.photos[photoIndex];
  if (!p || p.status === 'processing' || p.status === 'pending') return;

  const ovl = document.getElementById(approve ? 'ov-approve' : 'ov-reject');
  ovl.style.display = 'block';

  await fetch(`/api/decide`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ batch: batchIndex, photo: photoIndex, approve })
  });

  if (approve) totalApproved++;
  totalReviewed++;
  photoIndex++;

  setTimeout(() => {
    ovl.style.display = 'none';
    renderPhoto();
  }, 200);
}

function renderDone() {
  showDone();
  fetch('/api/state').then(r => r.json()).then(s => {
    let approved = 0, rejected = 0;
    s.batches.forEach(b => b.photos.forEach(p => {
      if (p.approved === true) approved++;
      else if (p.approved === false) rejected++;
    }));
    document.getElementById('done-stats').textContent = `${approved} approved  •  ${rejected} rejected  •  saved to Media_enhanced/`;
    const list = document.getElementById('results-list');
    s.batches.forEach(b => b.photos.forEach(p => {
      const li = document.createElement('li');
      li.className = p.approved ? 'approved' : 'rejected';
      const name = p.original.split(/[\\/]/).pop();
      li.textContent = (p.approved ? '✓ ' : '✗ ') + name;
      list.appendChild(li);
    }));
  });
}

document.getElementById('btn-reject').addEventListener('click', () => decide(false));
document.getElementById('btn-approve').addEventListener('click', () => decide(true));
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight') decide(true);
  if (e.key === 'ArrowLeft') decide(false);
});

loadState();
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
        ext = path.suffix.lower().lstrip(".")
        ctype = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"}.get(ext,"application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import unquote
        path = self.path.split("?")[0]

        if path == "/":
            self.send_html(HTML)

        elif path == "/api/state":
            self.send_json({
                "total": len(state["all_photos"]),
                "current_batch": state["current_batch"],
                "processing": state["processing"],
                "batches": [{
                    "index": i,
                    "review_done": b.get("review_done", False),
                    "processing_done": b.get("processing_done", False),
                    "photos": [{
                        "original": e["original"],
                        "processed": e["processed"],
                        "status": e["status"],
                        "approved": e.get("approved"),
                        "error": e.get("error"),
                    } for e in b["photos"]]
                } for i, b in enumerate(state["batches"])]
            })

        elif path.startswith("/img/original/"):
            self.send_file(Path(unquote(path[len("/img/original/"):])))

        elif path.startswith("/img/processed/"):
            self.send_file(Path(unquote(path[len("/img/processed/"):])))

        elif path.startswith("/api/info/"):
            p = Path(unquote(path[len("/api/info/"):]))
            if p.exists():
                try:
                    with Image.open(p) as img:
                        w, h = img.size
                    self.send_json({"w": w, "h": h, "kb": p.stat().st_size // 1024})
                except Exception as e:
                    self.send_json({"error": str(e)}, 500)
            else:
                self.send_json({"error": "not found"}, 404)

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        path = self.path

        if path == "/api/decide":
            bi, pi, approve = body["batch"], body["photo"], body["approve"]
            entry = state["batches"][bi]["photos"][pi]
            entry["approved"] = approve
            if approve and entry["status"] == "ready":
                import shutil
                proc = Path(entry["processed"])
                # Save as-is (no normalize — original aspect ratio)
                logger.info(f"  APPROVED: {Path(entry['original']).name} -> {proc}")
            else:
                # Remove processed file (rejected)
                proc = Path(entry["processed"])
                if proc.exists():
                    proc.unlink()
                logger.info(f"  REJECTED: {Path(entry['original']).name}")
            self.send_json({"ok": True})

        elif path.startswith("/api/batch/") and path.endswith("/done"):
            bi = int(path.split("/")[3])
            state["batches"][bi]["review_done"] = True
            # Trigger next batch processing
            next_bi = bi + 1
            if next_bi < len(state["batches"]):
                api_key = os.getenv("OPENROUTER_API_KEY")
                threading.Thread(
                    target=process_batch_async,
                    args=(next_bi, api_key),
                    daemon=True
                ).start()
                logger.info(f"Started processing batch {next_bi + 1}")
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
        print("ERROR: OPENROUTER_API_KEY not set in .env"); return

    photos = collect_photos()
    if not photos:
        print("No photos found under 2MP in Media/"); return

    OUTPUT_DIR.mkdir(exist_ok=True)

    n_batches = (len(photos) + REVIEW_BATCH - 1) // REVIEW_BATCH
    print(f"\nFound {len(photos)} photos to upscale (< {AI_MAX_MP}MP)")
    print(f"Review batch: {REVIEW_BATCH} photos | Total batches: {n_batches} | Workers: {WORKERS}")

    # Build batches
    state["all_photos"] = [str(p) for p in photos]
    for i in range(0, len(photos), REVIEW_BATCH):
        chunk = photos[i:i + REVIEW_BATCH]
        batch_photos = []
        for p in chunk:
            dp = dest_path(p)
            batch_photos.append({
                "original": str(p),
                "processed": str(dp),
                "status": "ready" if dp.exists() else "pending",
                "approved": None,
            })
        state["batches"].append({
            "photos": batch_photos,
            "processing_done": all(e["status"] == "ready" for e in batch_photos),
            "review_done": False,
        })

    # Skip already-reviewed batches
    for b in state["batches"]:
        if all(e.get("approved") is not None for e in b["photos"]):
            b["review_done"] = True

    # Launch ALL photos at once with WORKERS concurrent threads
    threading.Thread(target=process_all_async, args=(api_key,), daemon=True).start()

    server = HTTPServer(("localhost", PORT), Handler)
    print(f"\n{'='*52}")
    print(f"  Upscale Review — Tinder Mode")
    print(f"  http://localhost:{PORT}")
    print(f"  <- Reject  |  -> Approve")
    print(f"  Ctrl+C to stop")
    print(f"{'='*52}\n")

    import webbrowser
    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
