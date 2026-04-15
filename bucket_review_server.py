"""
Bucket Review Server — two modes:

  Mode 1 (default): review unsorted photos from Media/bucket/
  Mode 2 --fix-unknowns: fix photos where niche/use=unknown in classifications.json

Run:  python bucket_review_server.py
      python bucket_review_server.py --fix-unknowns
Open: http://localhost:8084
"""
import argparse
import json
import shutil
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading, webbrowser

PORT           = 8084
MEDIA_DIR      = Path("Media")
BUCKET         = Path("Media/bucket")
CLS_FILE       = Path("classifications.json")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

NICHES    = ["bathroom", "shower", "flooring", "hvac", "siding", "security"]
USE_TYPES = ["ba", "hero", "project"]
SUBTYPES  = {
    "ba":      ["before", "after", "together", "story", "process"],
    "hero":    ["result", "worker", "product", "team"],
    "project": ["wide", "detail", "process", "worker"],
}

# Set by main() based on --fix-unknowns flag
FIX_MODE = False


def collect_bucket():
    return sorted(
        p for p in BUCKET.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def collect_unknowns():
    """Collect photos with niche=unknown or use=unknown from classifications.json."""
    if not CLS_FILE.exists():
        return []
    with open(CLS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    paths = []
    for entry in data:
        if entry.get("niche") == "unknown" or entry.get("use") == "unknown":
            p = MEDIA_DIR / entry["file"].replace("\\", "/")
            if p.exists():
                paths.append(p)
    return sorted(paths)


def collect():
    return collect_unknowns() if FIX_MODE else collect_bucket()


def load_classifications():
    if not CLS_FILE.exists():
        return []
    with open(CLS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_classifications(data):
    with open(CLS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bucket Review</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d0d0d; color: #fff; font-family: system-ui, sans-serif; min-height: 100vh; }

#header { padding: 16px 24px; background: #111; border-bottom: 1px solid #1e1e1e; display: flex; align-items: center; gap: 16px; }
#header h1 { font-size: 16px; font-weight: 600; }
#header .sub { font-size: 12px; color: #555; }
#mode-badge { font-size: 11px; padding: 3px 10px; border-radius: 10px; font-weight: 700; }
#mode-badge.bucket { background: #1a3a2a; color: #00c853; }
#mode-badge.fix { background: #3a1a1a; color: #ff9800; }
#progress { font-size: 12px; color: #00c853; margin-left: auto; }

#grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; padding: 20px; }

.card { background: #111; border-radius: 12px; overflow: hidden; border: 1px solid #1e1e1e; transition: border-color 0.2s; }
.card.sorted { border-color: #00c853; opacity: 0.6; }
.card.deleted { border-color: #f44336; opacity: 0.4; }

.thumb { position: relative; height: 200px; background: #080808; display: flex; align-items: center; justify-content: center; cursor: pointer; }
.thumb img { max-width: 100%; max-height: 100%; object-fit: contain; }
.thumb:hover img { transform: scale(1.02); transition: transform 0.15s; }
.badge { position: absolute; top: 8px; right: 8px; color: #000; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 10px; display: none; }
.badge.ok { background: #00c853; }
.badge.del { background: #f44336; color: #fff; }

.info { padding: 10px 12px; }
.fname { font-size: 11px; color: #555; margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cur-labels { font-size: 10px; color: #ff9800; margin-bottom: 8px; }

.selects { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
select { background: #1a1a1a; color: #ccc; border: 1px solid #2a2a2a; border-radius: 6px; padding: 5px 8px; font-size: 12px; width: 100%; }
select:focus { outline: none; border-color: #444; }

.actions { display: flex; gap: 6px; }
.btn-sort { flex: 1; background: #00c853; color: #000; border: none; border-radius: 6px; padding: 7px; font-size: 12px; font-weight: 600; cursor: pointer; }
.btn-sort:hover { background: #00e676; }
.btn-keep { flex: 0; background: #1a1a1a; color: #666; border: 1px solid #2a2a2a; border-radius: 6px; padding: 7px 10px; font-size: 12px; cursor: pointer; }
.btn-keep:hover { color: #ffb300; border-color: #ffb300; }
.btn-del { flex: 0; background: #1a1a1a; color: #666; border: 1px solid #2a2a2a; border-radius: 6px; padding: 7px 10px; font-size: 12px; cursor: pointer; }
.btn-del:hover { color: #f44336; border-color: #f44336; }

.status-msg { font-size: 11px; color: #00c853; margin-top: 6px; min-height: 14px; }

#lightbox { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.92); z-index: 100; align-items: center; justify-content: center; cursor: zoom-out; }
#lightbox img { max-width: 95vw; max-height: 95vh; object-fit: contain; border-radius: 4px; }
</style>
</head>
<body>

<div id="header">
  <h1>Bucket Review</h1>
  <span id="mode-badge">...</span>
  <span class="sub" id="mode-desc"></span>
  <span id="progress">0 / 0</span>
</div>

<div id="grid"></div>
<div id="lightbox"><img id="lb-img" src=""></div>

<script>
const NICHES   = ["bathroom","shower","flooring","hvac","siding","security"];
const USE_TYPES= ["ba","hero","project"];
const SUBTYPES = {
  ba:      ["before","after","together","story","process"],
  hero:    ["result","worker","product","team"],
  project: ["wide","detail","process","worker"],
};

let photos = [], sortedCount = 0, fixMode = false;

async function load() {
  const r = await fetch('/api/photos');
  const data = await r.json();
  photos = data.photos;
  fixMode = data.fix_mode;

  const badge = document.getElementById('mode-badge');
  const desc  = document.getElementById('mode-desc');
  if (fixMode) {
    badge.textContent = 'FIX UNKNOWNS';
    badge.className = 'mode-badge fix';
    desc.textContent = 'Виправляємо фото з unknown niche/use';
  } else {
    badge.textContent = 'BUCKET';
    badge.className = 'mode-badge bucket';
    desc.textContent = 'Сортуй фото або залиш в bucket';
  }
  render();
}

function render() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  photos.forEach((p, i) => {
    const name = p.path.split(/[\\/]/).pop();
    const card = document.createElement('div');
    card.className = 'card' + (p.sorted ? ' sorted' : '') + (p.deleted ? ' deleted' : '');
    card.id = 'card-' + i;

    const subtypeOpts = (use) => (SUBTYPES[use]||[]).map(s =>
      `<option value="${s}">${s}</option>`).join('');

    // Pre-select current values if known
    const curNiche   = p.current_niche || '';
    const curUse     = p.current_use   || '';
    const nicheOpts  = NICHES.map(n =>
      `<option value="${n}" ${n===curNiche?'selected':''}>${n}</option>`).join('');
    const useOpts    = USE_TYPES.map(u =>
      `<option value="${u}" ${u===curUse?'selected':''}>${u}</option>`).join('');

    const curLabel = (p.current_niche || p.current_use)
      ? `<div class="cur-labels">now: ${p.current_niche||'?'} / ${p.current_use||'?'} / ${p.current_subtype||'?'}</div>`
      : '';

    const actionLabel = fixMode ? 'Fix' : 'Move';

    card.innerHTML = `
      <div class="thumb" onclick="openLb('${encodeURIComponent(p.path)}')">
        <img src="/img/${encodeURIComponent(p.path)}" alt="">
        <span class="badge ok" id="badge-${i}">FIXED</span>
      </div>
      <div class="info">
        <div class="fname">${name}</div>
        ${curLabel}
        <div class="selects">
          <select id="niche-${i}" onchange="updateSubtypes(${i})">
            <option value="">-- niche --</option>
            ${nicheOpts}
          </select>
          <select id="use-${i}" onchange="updateSubtypes(${i})">
            ${useOpts}
          </select>
          <select id="sub-${i}">
            ${subtypeOpts(curUse || 'hero')}
          </select>
        </div>
        <div class="actions">
          <button class="btn-sort" onclick="sortPhoto(${i})">${actionLabel} &rarr;</button>
          <button class="btn-keep" onclick="keepBucket(${i})" title="Keep as-is">&#128193;</button>
          <button class="btn-del" onclick="deletePhoto(${i})" title="Delete">&#128465;</button>
        </div>
        <div class="status-msg" id="msg-${i}"></div>
      </div>
    `;
    grid.appendChild(card);
  });
  updateProgress();
}

function updateSubtypes(i) {
  const use = document.getElementById('use-'+i).value;
  const sub = document.getElementById('sub-'+i);
  sub.innerHTML = (SUBTYPES[use]||[]).map(s=>`<option value="${s}">${s}</option>`).join('');
}

async function sortPhoto(i) {
  const p = photos[i];
  const niche   = document.getElementById('niche-'+i).value;
  const use     = document.getElementById('use-'+i).value;
  const subtype = document.getElementById('sub-'+i).value;
  if (!niche) { document.getElementById('msg-'+i).textContent = 'Choose niche!'; return; }

  const endpoint = fixMode ? '/api/fix' : '/api/sort';
  const r = await fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({path: p.path, niche, use, subtype})
  });
  const res = await r.json();
  if (res.ok) {
    photos[i].sorted = true;
    document.getElementById('card-'+i).className = 'card sorted';
    const badge = document.getElementById('badge-'+i);
    badge.textContent = fixMode ? 'FIXED' : 'SORTED';
    badge.style.display = 'block';
    document.getElementById('msg-'+i).textContent = res.new_path
      ? res.new_path.split(/[\\/]/).pop()
      : 'Updated in classifications.json';
    sortedCount++;
    updateProgress();
  } else {
    document.getElementById('msg-'+i).style.color = '#f44336';
    document.getElementById('msg-'+i).textContent = res.error;
  }
}

async function keepBucket(i) {
  photos[i].sorted = true;
  document.getElementById('card-'+i).className = 'card sorted';
  const badge = document.getElementById('badge-'+i);
  badge.textContent = 'KEPT'; badge.className = 'badge ok'; badge.style.display = 'block';
  document.getElementById('msg-'+i).textContent = fixMode ? 'Skipped' : 'Left in bucket';
  sortedCount++;
  updateProgress();
}

async function deletePhoto(i) {
  if (!confirm('Delete file?')) return;
  const r = await fetch('/api/delete', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({path: photos[i].path})
  });
  const res = await r.json();
  if (res.ok) {
    photos[i].deleted = true;
    document.getElementById('card-'+i).className = 'card deleted';
    const badge = document.getElementById('badge-'+i);
    badge.textContent = 'DELETED'; badge.className = 'badge del'; badge.style.display = 'block';
    sortedCount++;
    updateProgress();
  }
}

function updateProgress() {
  const done = photos.filter(p => p.sorted || p.deleted).length;
  document.getElementById('progress').textContent = `${done} / ${photos.length} done`;
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
        ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "png": "image/png", "webp": "image/webp"}.get(ext, "application/octet-stream")
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

        elif path == "/api/photos":
            if FIX_MODE:
                cls_data = load_classifications()
                cls_by_file = {e["file"].replace("\\", "/"): e for e in cls_data}
                photos = []
                for p in collect():
                    rel = str(p.relative_to(MEDIA_DIR)).replace("\\", "/")
                    full_rel = str(p).replace("\\", "/")
                    cls = cls_by_file.get(rel, {})
                    photos.append({
                        "path": str(p),
                        "sorted": False,
                        "deleted": False,
                        "current_niche":   cls.get("niche", ""),
                        "current_use":     cls.get("use", ""),
                        "current_subtype": cls.get("subtype", ""),
                    })
            else:
                photos = [{"path": str(p), "sorted": False, "deleted": False,
                           "current_niche": "", "current_use": "", "current_subtype": ""}
                          for p in collect()]
            self.send_json({"photos": photos, "fix_mode": FIX_MODE})

        elif path.startswith("/img/"):
            from urllib.parse import unquote
            rel = unquote(path[5:])
            self.send_file(Path(rel))

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        path = self.path

        if path == "/api/sort":
            # Bucket mode: move file to correct folder
            src = Path(body["path"])
            niche, use, subtype = body["niche"], body["use"], body["subtype"]
            dest_dir = MEDIA_DIR / niche / use
            dest_dir.mkdir(parents=True, exist_ok=True)
            prefix = f"{niche}_{use}_{subtype}_"
            existing = list(dest_dir.glob(f"{prefix}*.jpg"))
            seq = len(existing) + 1
            dest = dest_dir / f"{prefix}{seq:03d}.jpg"
            while dest.exists():
                seq += 1
                dest = dest_dir / f"{prefix}{seq:03d}.jpg"
            try:
                from PIL import Image
                with Image.open(src) as img:
                    img.convert("RGB").save(dest, "JPEG", quality=95)
                src.unlink()
                self.send_json({"ok": True, "new_path": str(dest)})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})

        elif path == "/api/fix":
            # Fix mode: update classifications.json only, no file move
            src = Path(body["path"])
            niche, use, subtype = body["niche"], body["use"], body["subtype"]
            try:
                cls_data = load_classifications()
                rel = str(src.relative_to(MEDIA_DIR)).replace("\\", "/")
                updated = False
                for entry in cls_data:
                    entry_file = entry.get("file", "").replace("\\", "/")
                    if entry_file == rel or Path(entry_file).name == src.name:
                        entry["niche"]   = niche
                        entry["use"]     = use
                        entry["subtype"] = subtype
                        updated = True
                        break
                if not updated:
                    # Add new entry if not found
                    cls_data.append({
                        "file": rel, "niche": niche,
                        "use": use, "subtype": subtype,
                    })
                save_classifications(cls_data)
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})

        elif path == "/api/delete":
            src = Path(body["path"])
            try:
                src.unlink()
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})

        else:
            self.send_response(404); self.end_headers()


def main():
    global FIX_MODE
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix-unknowns", action="store_true",
                        help="Fix photos with unknown niche/use in classifications.json")
    args = parser.parse_args()
    FIX_MODE = args.fix_unknowns

    photos = collect()
    mode_label = "FIX UNKNOWNS" if FIX_MODE else "BUCKET"
    print(f"\n[{mode_label}] {len(photos)} photos")
    for p in photos:
        print(f"  {p}")

    server = HTTPServer(("localhost", PORT), Handler)
    print(f"\nOpen: http://localhost:{PORT}")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
