#!/usr/bin/env python3
"""
Photo Review Server — Tinder-style classification review.

Run:   python review_server.py
Open:  http://localhost:8090
"""
import json
import mimetypes
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PORT = 8090
MEDIA_DIR = Path("Media_norm")
CLASSIFICATIONS = Path("classifications.json")
REVIEWS_FILE = Path("reviews.json")


# ─── Build review units from Media_norm structure ────────────────────────────

def load_classifications() -> dict:
    """Return dict: original_filename → classification record."""
    if not CLASSIFICATIONS.exists():
        return {}
    with open(CLASSIFICATIONS, encoding="utf-8") as f:
        data = json.load(f)
    return {Path(r["file"]).name: r for r in data if "error" not in r}


def parse_canonical_name(stem: str) -> dict:
    """Parse 'hvac_ba_together_001' → {niche, use, subtype, seq}."""
    parts = stem.split("_")
    if len(parts) == 4:
        return {"niche": parts[0], "use": parts[1], "subtype": parts[2], "seq": parts[3]}
    return {"niche": "?", "use": "?", "subtype": "?", "seq": "?"}


def build_review_units() -> list:
    """
    Walk Media_norm/ and build review units.
    A unit is either a single photo or a set folder.
    """
    units = []
    cls_map = load_classifications()

    def photo_info(path: Path) -> dict:
        rel = path.relative_to(MEDIA_DIR)
        meta = parse_canonical_name(path.stem)
        # Try to find quality/confidence from classifications
        cls = cls_map.get(path.name, {})
        return {
            "path": str(rel).replace("\\", "/"),
            "name": path.name,
            "niche": meta["niche"],
            "use": meta["use"],
            "subtype": meta["subtype"],
            "seq": meta["seq"],
            "quality_score": cls.get("quality_score", 0),
            "has_text_overlay": cls.get("has_text_overlay", False),
            "has_person": cls.get("has_person", False),
            "confidence": cls.get("confidence", 0),
        }

    for niche_dir in sorted(MEDIA_DIR.iterdir()):
        if not niche_dir.is_dir():
            continue
        niche = niche_dir.name.lower()

        for use_dir in sorted(niche_dir.iterdir()):
            if not use_dir.is_dir():
                continue
            use = use_dir.name.lower()

            # Collect set subfolders
            for item in sorted(use_dir.iterdir()):
                if item.is_dir() and item.name.startswith("set-"):
                    photos = sorted(item.glob("*.jpg"))
                    if not photos:
                        continue
                    units.append({
                        "id": f"{niche}/{use}/{item.name}",
                        "type": "set",
                        "niche": niche,
                        "use": use,
                        "set_name": item.name,
                        "photos": [photo_info(p) for p in photos],
                    })

            # Collect flat files
            flat = sorted(f for f in use_dir.iterdir()
                          if f.is_file() and f.suffix.lower() == ".jpg")
            for photo in flat:
                info = photo_info(photo)
                units.append({
                    "id": f"{niche}/{use}/{photo.name}",
                    "type": "single",
                    "niche": info["niche"],
                    "use": info["use"],
                    "subtype": info["subtype"],
                    "photos": [info],
                })

    return units


# ─── Reviews storage ──────────────────────────────────────────────────────────

def load_reviews() -> dict:
    if REVIEWS_FILE.exists():
        with open(REVIEWS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_review(review: dict):
    reviews = load_reviews()
    reviews[review["id"]] = {**review, "reviewed_at": datetime.now().isoformat()}
    with open(REVIEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(reviews, f, indent=2, ensure_ascii=False)


# ─── HTML page ────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Photo Review</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0d0f14; --card: #171a23; --border: #252936;
    --text: #e2e8f0; --muted: #64748b; --accent: #3b82f6;
    --green: #22c55e; --red: #ef4444; --yellow: #eab308;
  }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif;
    height: 100vh; display: flex; flex-direction: column; overflow: hidden; user-select: none; }

  /* ── Header ── */
  .header { display: flex; align-items: center; gap: 16px; padding: 10px 20px;
    background: var(--card); border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .header h1 { font-size: 15px; font-weight: 700; color: #fff; }
  .progress-wrap { flex: 1; height: 4px; background: var(--border); border-radius: 2px; }
  .progress-bar { height: 100%; background: var(--accent); border-radius: 2px; transition: width 0.3s; }
  .progress-label { font-size: 12px; color: var(--muted); white-space: nowrap; }
  .filter-btns { display: flex; gap: 6px; }
  .filter-btn { padding: 4px 10px; border-radius: 12px; border: 1px solid var(--border);
    background: transparent; color: var(--muted); font-size: 11px; cursor: pointer; transition: all 0.15s; }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

  /* ── Main card area ── */
  .stage { flex: 1; display: flex; align-items: stretch; overflow: hidden; position: relative; }

  /* ── Card ── */
  .card { position: absolute; inset: 0; display: flex; flex-direction: column;
    transition: transform 0.35s cubic-bezier(.4,0,.2,1), opacity 0.3s;
    will-change: transform; }
  .card.leaving-left  { transform: translateX(-120%) rotate(-8deg); opacity: 0; }
  .card.leaving-right { transform: translateX(120%) rotate(8deg); opacity: 0; }
  .card.entering { transform: translateX(0) scale(0.95); animation: pop-in 0.3s forwards; }
  @keyframes pop-in { to { transform: translateX(0) scale(1); } }

  /* Swipe overlay */
  .swipe-hint { position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
    font-size: 48px; font-weight: 900; letter-spacing: 3px; opacity: 0;
    pointer-events: none; transition: opacity 0.15s; z-index: 10; border: 4px solid;
    padding: 8px 20px; border-radius: 8px; }
  .swipe-hint.approve { color: var(--green); border-color: var(--green); }
  .swipe-hint.reject  { color: var(--red); border-color: var(--red); }

  /* ── Photo area ── */
  .photo-area { flex: 1; display: flex; align-items: center; justify-content: center;
    padding: 12px; gap: 8px; min-height: 0; overflow: hidden; }

  /* Single photo */
  .photo-area.single .photo-wrap { height: 100%; display: flex; align-items: center; }
  .photo-area.single img { max-width: 100%; max-height: 100%; object-fit: contain;
    border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); cursor: zoom-in; }

  /* Set photos */
  .photo-area.set { flex-wrap: wrap; justify-content: center; }
  .photo-area.set .photo-wrap { flex: 1 1 calc(50% - 8px); max-width: calc(50% - 8px);
    max-height: 50%; display: flex; align-items: center; justify-content: center;
    position: relative; }
  .photo-area.set .photo-wrap img { max-width: 100%; max-height: 100%; object-fit: contain;
    border-radius: 6px; box-shadow: 0 4px 16px rgba(0,0,0,0.4); cursor: zoom-in;
    transition: transform 0.15s; }
  .photo-area.set .photo-wrap img:hover { transform: scale(1.02); }
  .photo-wrap .photo-label { position: absolute; bottom: 4px; left: 50%; transform: translateX(-50%);
    background: rgba(0,0,0,0.75); color: #fff; font-size: 10px; padding: 2px 6px;
    border-radius: 4px; white-space: nowrap; pointer-events: none; }

  /* ── Meta panel ── */
  .meta { flex-shrink: 0; padding: 10px 16px 6px; background: var(--card);
    border-top: 1px solid var(--border); }
  .meta-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
  .meta-id { font-size: 12px; color: var(--muted); font-family: monospace; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge-niche   { background: #1e3a5f; color: #60a5fa; }
  .badge-use     { background: #1a3a1a; color: #4ade80; }
  .badge-subtype { background: #2d1f3a; color: #c084fc; }
  .badge-text    { background: #3a1a0a; color: #fb923c; }
  .badge-set     { background: #1a2a3a; color: #7dd3fc; }
  .badge-q { font-size: 11px; color: var(--muted); }

  /* ── Comment area ── */
  .comment-wrap { padding: 6px 16px 8px; background: var(--card); flex-shrink: 0; }
  .comment-input { width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text); padding: 8px 12px; font-size: 13px;
    resize: none; font-family: inherit; outline: none; line-height: 1.4; }
  .comment-input:focus { border-color: var(--accent); }
  .comment-input::placeholder { color: var(--muted); }

  .suggest-row { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
  .suggest-row label { font-size: 11px; color: var(--muted); align-self: center; }
  .suggest-select { background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 3px 6px; border-radius: 6px; font-size: 11px; outline: none; cursor: pointer; }
  .suggest-select:focus { border-color: var(--accent); }

  /* ── Action buttons ── */
  .actions { display: flex; justify-content: center; align-items: center; gap: 16px;
    padding: 8px 16px 12px; background: var(--card); flex-shrink: 0; }
  .btn { border: none; border-radius: 50px; font-weight: 700; cursor: pointer;
    transition: transform 0.1s, box-shadow 0.1s; display: flex; align-items: center; gap: 6px; }
  .btn:active { transform: scale(0.95); }
  .btn-reject { background: #3a1010; color: var(--red); padding: 10px 24px; font-size: 14px;
    border: 2px solid var(--red); }
  .btn-reject:hover { background: var(--red); color: #fff; box-shadow: 0 0 20px #ef444440; }
  .btn-skip { background: var(--border); color: var(--muted); padding: 8px 18px; font-size: 13px; }
  .btn-skip:hover { background: #2d3748; color: var(--text); }
  .btn-approve { background: #0f2a1a; color: var(--green); padding: 10px 24px; font-size: 14px;
    border: 2px solid var(--green); }
  .btn-approve:hover { background: var(--green); color: #fff; box-shadow: 0 0 20px #22c55e40; }

  .key-hint { font-size: 10px; color: var(--muted); margin-top: 3px; text-align: center; }

  /* ── Lightbox ── */
  .lightbox { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.92);
    z-index: 100; align-items: center; justify-content: center; cursor: zoom-out; }
  .lightbox.open { display: flex; }
  .lightbox img { max-width: 95vw; max-height: 95vh; object-fit: contain; border-radius: 8px; }

  /* ── Done / empty ── */
  .done-screen { flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 16px; }
  .done-screen h2 { font-size: 24px; color: var(--green); }
  .done-screen p { color: var(--muted); font-size: 14px; text-align: center; }
  .btn-export { background: var(--accent); color: #fff; padding: 12px 32px; font-size: 15px;
    border: none; border-radius: 8px; cursor: pointer; font-weight: 600; margin-top: 8px; }
  .btn-export:hover { background: #2563eb; }

  /* ── Already reviewed badge ── */
  .reviewed-badge { display: none; position: absolute; top: 12px; right: 12px; z-index: 5;
    background: rgba(34,197,94,0.2); border: 1px solid var(--green); color: var(--green);
    padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }

  /* ── Stats bar ── */
  .stats { display: flex; gap: 16px; padding: 0 20px; }
  .stat { font-size: 11px; color: var(--muted); }
  .stat b { font-weight: 700; }
  .stat.ok b { color: var(--green); }
  .stat.bad b { color: var(--red); }
  .stat.skip b { color: var(--yellow); }
</style>
</head>
<body>

<div class="header">
  <h1>📸 Review</h1>
  <div class="stats">
    <div class="stat ok"><b id="stat-ok">0</b> approved</div>
    <div class="stat bad"><b id="stat-bad">0</b> rejected</div>
    <div class="stat skip"><b id="stat-skip">0</b> skipped</div>
  </div>
  <div class="progress-wrap"><div class="progress-bar" id="progress-bar"></div></div>
  <div class="progress-label" id="progress-label">0 / 0</div>
  <div class="filter-btns">
    <button class="filter-btn active" onclick="setFilter('all')">All</button>
    <button class="filter-btn" onclick="setFilter('pending')">Pending</button>
    <button class="filter-btn" onclick="setFilter('rejected')">Rejected</button>
  </div>
</div>

<div class="stage" id="stage">
  <!-- Cards rendered here -->
  <div class="done-screen" id="done-screen" style="display:none;">
    <h2>✓ Review complete!</h2>
    <p id="done-stats"></p>
    <button class="btn-export" onclick="exportReviews()">⬇ Download reviews.json</button>
  </div>
</div>

<div id="meta-panel">
  <div class="meta" id="meta-panel-inner">
    <div class="meta-row" id="meta-badges"></div>
    <div class="meta-row">
      <span class="meta-id" id="meta-id"></span>
    </div>
  </div>

  <div class="comment-wrap">
    <textarea class="comment-input" id="comment" rows="2"
      placeholder="Comment / correction… (e.g. 'wrong niche, this is bathroom not flooring')"></textarea>
    <div class="suggest-row">
      <label>Fix to:</label>
      <select class="suggest-select" id="fix-niche">
        <option value="">niche?</option>
        <option>hvac</option><option>bathroom</option><option>shower</option>
        <option>flooring</option><option>siding</option><option>security</option>
      </select>
      <select class="suggest-select" id="fix-use">
        <option value="">use?</option>
        <option>hero</option><option>ba</option><option>project</option>
      </select>
      <select class="suggest-select" id="fix-subtype">
        <option value="">subtype?</option>
        <option>worker</option><option>result</option><option>product</option><option>team</option>
        <option>together</option><option>before</option><option>after</option>
        <option>story</option><option>process</option>
        <option>wide</option><option>detail</option>
      </select>
      <label style="margin-left:auto;font-size:11px;color:#4b5563;">↑↓ navigate · ← reject · → approve · Space skip</label>
    </div>
  </div>

  <div class="actions">
    <div style="text-align:center;">
      <button class="btn btn-reject" onclick="doAction('reject')">✗ Wrong</button>
      <div class="key-hint">← arrow</div>
    </div>
    <div style="text-align:center;">
      <button class="btn btn-skip" onclick="doAction('skip')">⏭ Skip</button>
      <div class="key-hint">Space</div>
    </div>
    <div style="text-align:center;">
      <button class="btn btn-approve" onclick="doAction('approve')">✓ Correct</button>
      <div class="key-hint">→ arrow</div>
    </div>
  </div>
</div>

<!-- Lightbox -->
<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <img id="lightbox-img" src="" alt="">
</div>

<script>
let units = [];
let reviews = {};
let filteredUnits = [];
let currentIdx = 0;
let filter = 'all';
let isDragging = false, dragStartX = 0, dragDelta = 0;

// ── Load data ──────────────────────────────────────────────────────────────

async function init() {
  const [dataRes, reviewRes] = await Promise.all([
    fetch('/api/data'),
    fetch('/api/reviews').catch(() => ({ json: async () => ({}) }))
  ]);
  units = await dataRes.json();
  reviews = await reviewRes.json();
  applyFilter();
  renderStats();
}

function applyFilter() {
  if (filter === 'all') {
    filteredUnits = [...units];
  } else if (filter === 'pending') {
    filteredUnits = units.filter(u => !reviews[u.id] || reviews[u.id].action === 'skip');
  } else if (filter === 'rejected') {
    filteredUnits = units.filter(u => reviews[u.id]?.action === 'reject');
  }
  currentIdx = 0;
  renderCard();
}

function setFilter(f) {
  filter = f;
  document.querySelectorAll('.filter-btn').forEach((b,i) => {
    b.classList.toggle('active', ['all','pending','rejected'][i] === f);
  });
  applyFilter();
}

// ── Render ─────────────────────────────────────────────────────────────────

function renderCard() {
  const stage = document.getElementById('stage');
  // Remove old card
  const old = stage.querySelector('.card');
  if (old) old.remove();

  if (currentIdx >= filteredUnits.length) {
    document.getElementById('done-screen').style.display = 'flex';
    document.getElementById('meta-panel').style.display = 'none';
    const r = Object.values(reviews);
    document.getElementById('done-stats').textContent =
      `${r.filter(x=>x.action==='approve').length} approved · ${r.filter(x=>x.action==='reject').length} rejected · ${r.filter(x=>x.action==='skip').length} skipped`;
    return;
  }

  document.getElementById('done-screen').style.display = 'none';
  document.getElementById('meta-panel').style.display = 'block';

  const unit = filteredUnits[currentIdx];
  const rev = reviews[unit.id];

  // Build card
  const card = document.createElement('div');
  card.className = 'card entering';

  // Reviewed badge
  if (rev) {
    const badge = document.createElement('div');
    badge.className = 'reviewed-badge';
    badge.style.display = 'block';
    badge.textContent = rev.action === 'approve' ? '✓ Approved' : rev.action === 'reject' ? '✗ Rejected' : '⏭ Skipped';
    badge.style.borderColor = rev.action === 'approve' ? 'var(--green)' : rev.action === 'reject' ? 'var(--red)' : 'var(--yellow)';
    badge.style.color = rev.action === 'approve' ? 'var(--green)' : rev.action === 'reject' ? 'var(--red)' : 'var(--yellow)';
    card.appendChild(badge);
  }

  // Swipe hints
  const hintL = document.createElement('div');
  hintL.className = 'swipe-hint reject'; hintL.textContent = 'WRONG';
  const hintR = document.createElement('div');
  hintR.className = 'swipe-hint approve'; hintR.textContent = 'CORRECT';
  card.appendChild(hintL); card.appendChild(hintR);

  // Photo area
  const photoArea = document.createElement('div');
  photoArea.className = `photo-area ${unit.type === 'set' ? 'set' : 'single'}`;

  unit.photos.forEach(photo => {
    const wrap = document.createElement('div');
    wrap.className = 'photo-wrap';
    const img = document.createElement('img');
    img.src = `/media/${photo.path}`;
    img.alt = photo.name;
    img.loading = 'lazy';
    img.onclick = () => openLightbox(img.src);
    wrap.appendChild(img);

    if (unit.type === 'set') {
      const lbl = document.createElement('div');
      lbl.className = 'photo-label';
      lbl.textContent = `${photo.subtype}`;
      wrap.appendChild(lbl);
    }
    photoArea.appendChild(wrap);
  });

  card.appendChild(photoArea);

  // Touch/mouse drag
  card.addEventListener('mousedown', onDragStart);
  card.addEventListener('touchstart', e => onDragStart(e.touches[0]), {passive: true});

  stage.insertBefore(card, stage.firstChild);

  // Update meta
  renderMeta(unit, rev);
  updateProgress();
}

function renderMeta(unit, rev) {
  const photos = unit.photos;
  const p0 = photos[0];

  // Badges
  const badges = document.getElementById('meta-badges');
  badges.innerHTML = '';

  const addBadge = (cls, text) => {
    const b = document.createElement('span');
    b.className = `badge ${cls}`;
    b.textContent = text;
    badges.appendChild(b);
  };

  addBadge('badge-niche', p0.niche);
  addBadge('badge-use', p0.use);
  addBadge('badge-subtype', p0.subtype);

  if (unit.type === 'set') addBadge('badge-set', `set · ${photos.length} photos`);
  if (p0.has_text_overlay) addBadge('badge-text', '⚠ has text/logo');
  if (p0.quality_score) {
    const b = document.createElement('span');
    b.className = 'badge-q';
    b.innerHTML = `Q: <b style="color:${p0.quality_score>=7?'#4ade80':p0.quality_score>=5?'#eab308':'#ef4444'}">${p0.quality_score}</b>/10`;
    badges.appendChild(b);
  }

  // ID
  document.getElementById('meta-id').textContent = unit.id;

  // Restore comment and fix if previously reviewed
  document.getElementById('comment').value = rev?.comment || '';
  document.getElementById('fix-niche').value = rev?.fix_niche || '';
  document.getElementById('fix-use').value = rev?.fix_use || '';
  document.getElementById('fix-subtype').value = rev?.fix_subtype || '';
}

function updateProgress() {
  const total = filteredUnits.length;
  const done = Math.min(currentIdx, total);
  document.getElementById('progress-bar').style.width = `${total ? done/total*100 : 0}%`;
  document.getElementById('progress-label').textContent = `${done} / ${total}`;
}

function renderStats() {
  const vals = Object.values(reviews);
  document.getElementById('stat-ok').textContent = vals.filter(r=>r.action==='approve').length;
  document.getElementById('stat-bad').textContent = vals.filter(r=>r.action==='reject').length;
  document.getElementById('stat-skip').textContent = vals.filter(r=>r.action==='skip').length;
}

// ── Actions ────────────────────────────────────────────────────────────────

function doAction(action) {
  if (currentIdx >= filteredUnits.length) return;
  const unit = filteredUnits[currentIdx];
  const card = document.querySelector('.card');

  const review = {
    id: unit.id,
    action,
    comment: document.getElementById('comment').value.trim(),
    fix_niche: document.getElementById('fix-niche').value || null,
    fix_use: document.getElementById('fix-use').value || null,
    fix_subtype: document.getElementById('fix-subtype').value || null,
    unit_type: unit.type,
    photos: unit.photos.map(p => p.name),
  };

  // Animate
  if (card) {
    card.classList.add(action === 'approve' ? 'leaving-right' : action === 'reject' ? 'leaving-left' : '');
  }

  // Save
  fetch('/api/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(review)
  });
  reviews[unit.id] = { ...review, reviewed_at: new Date().toISOString() };

  renderStats();
  setTimeout(() => {
    currentIdx++;
    renderCard();
  }, action === 'skip' ? 0 : 300);
}

// ── Drag to swipe ──────────────────────────────────────────────────────────

function onDragStart(e) {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  isDragging = true;
  dragStartX = e.clientX;
  dragDelta = 0;
  window.addEventListener('mousemove', onDragMove);
  window.addEventListener('mouseup', onDragEnd);
  window.addEventListener('touchmove', e => onDragMove(e.touches[0]), {passive: true});
  window.addEventListener('touchend', e => onDragEnd(e.changedTouches[0]), {once: true});
}

function onDragMove(e) {
  if (!isDragging) return;
  dragDelta = e.clientX - dragStartX;
  const card = document.querySelector('.card');
  if (!card) return;
  const rot = dragDelta * 0.05;
  card.style.transform = `translateX(${dragDelta}px) rotate(${rot}deg)`;
  card.style.transition = 'none';

  const hintR = card.querySelector('.swipe-hint.approve');
  const hintL = card.querySelector('.swipe-hint.reject');
  if (hintR) hintR.style.opacity = dragDelta > 40 ? Math.min((dragDelta-40)/80, 1) : 0;
  if (hintL) hintL.style.opacity = dragDelta < -40 ? Math.min((-dragDelta-40)/80, 1) : 0;
}

function onDragEnd(e) {
  isDragging = false;
  window.removeEventListener('mousemove', onDragMove);
  window.removeEventListener('mouseup', onDragEnd);
  const card = document.querySelector('.card');
  if (!card) return;
  card.style.transition = '';

  if (dragDelta > 100) {
    doAction('approve');
  } else if (dragDelta < -100) {
    doAction('reject');
  } else {
    card.style.transform = '';
    const hintR = card.querySelector('.swipe-hint.approve');
    const hintL = card.querySelector('.swipe-hint.reject');
    if (hintR) hintR.style.opacity = 0;
    if (hintL) hintL.style.opacity = 0;
  }
}

// ── Keyboard ───────────────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowRight') { e.preventDefault(); doAction('approve'); }
  if (e.key === 'ArrowLeft')  { e.preventDefault(); doAction('reject'); }
  if (e.key === ' ')          { e.preventDefault(); doAction('skip'); }
  if (e.key === 'ArrowUp')    { e.preventDefault(); if (currentIdx > 0) { currentIdx--; renderCard(); } }
  if (e.key === 'ArrowDown')  { e.preventDefault(); currentIdx++; renderCard(); }
});

// ── Lightbox ───────────────────────────────────────────────────────────────

function openLightbox(src) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeLightbox();
});

// ── Export ─────────────────────────────────────────────────────────────────

function exportReviews() {
  const blob = new Blob([JSON.stringify(reviews, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'reviews.json';
  a.click();
}

init();
</script>
</body>
</html>"""


# ─── HTTP Server ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # Silence default logs

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            self.send_html(HTML)

        elif path == "/api/data":
            self.send_json(build_review_units())

        elif path == "/api/reviews":
            self.send_json(load_reviews())

        elif path.startswith("/media/"):
            rel = unquote(path[7:])
            file_path = Path(rel)
            if file_path.exists() and file_path.is_file():
                mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                data = file_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/review":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            save_review(body)
            self.send_json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("localhost", PORT), Handler)
    print(f"Review server running at http://localhost:{PORT}")
    print(f"Media dir: {MEDIA_DIR.resolve()}")
    print(f"Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
