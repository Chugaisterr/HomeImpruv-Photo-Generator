"""
One-time structure fix script.

Fixes:
1. Invalid use codes (use=process, use=result) → Media/bucket/
2. Groups BA series (same original basename) → niche/ba/set-NNN/ subfolders
3. Moves lone single-file subtypes that don't belong (bathroom/process, shower/result)
"""
import json
import re
import shutil
from pathlib import Path
from collections import defaultdict

MEDIA_DIR = Path("Media")
BUCKET_DIR = MEDIA_DIR / "bucket"


# ─────────────────────────────────────────────────────────
# Step 1: Fix invalid use codes → bucket
# ─────────────────────────────────────────────────────────
VALID_USES = {"hero", "ba", "project"}

def fix_invalid_uses():
    print("\n[1/3] Fixing invalid use codes → bucket")
    moved = 0

    # Check for folders with invalid use codes
    invalid_use_folders = []
    for niche_dir in MEDIA_DIR.iterdir():
        if not niche_dir.is_dir() or niche_dir.name in ("bucket",):
            continue
        for use_dir in niche_dir.iterdir():
            if not use_dir.is_dir():
                continue
            if use_dir.name not in VALID_USES:
                invalid_use_folders.append(use_dir)

    for folder in invalid_use_folders:
        files = list(folder.rglob("*.jpg")) + list(folder.rglob("*.png")) + list(folder.rglob("*.webp"))
        niche = folder.parent.name
        dest_dir = BUCKET_DIR / f"invalid-use_{folder.name}" / niche
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            dest = dest_dir / f.name
            shutil.move(str(f), str(dest))
            print(f"  BUCKET [{niche}/{folder.name}] {f.name}")
            moved += 1
        # Remove empty folder
        try:
            folder.rmdir()
        except OSError:
            pass

    print(f"  → Moved {moved} files to bucket")
    return moved


# ─────────────────────────────────────────────────────────
# Step 2: Detect BA series and group into set-NNN subfolders
# ─────────────────────────────────────────────────────────

def _series_key(original_filename: str) -> str:
    """
    Extract normalized series key from original filename.
    'Floring Before Story 1 (2).webp' → 'floring story 1'
    'HVAC Before After Story 1-2.webp' → 'hvac after story 1'
    'Shower Before 2.jpg' → 'shower'
    """
    stem = Path(original_filename).stem.lower()
    # Remove trailing number patterns: (N), -N, _N, NNN
    stem = re.sub(r'[\s\-_]*[\(\[]?\d+[\)\]]?\s*$', '', stem).strip()
    # Normalize separators
    stem = re.sub(r'[\s_-]+', ' ', stem).strip()
    # Remove "before" / "after" to unify series
    stem = re.sub(r'\b(before|after)\b', '', stem)
    stem = re.sub(r'\s+', ' ', stem).strip()
    return stem


def group_ba_series():
    """
    For each niche/ba/ folder, detect files that come from the same original
    series (same base name in classifications.json) and move them to set-NNN/.
    Only creates set folders for groups of 2+ files with mixed subtypes
    (before+after, or story+together, etc.) — not for uniform groups.
    """
    print("\n[2/3] Grouping BA series into set-NNN subfolders")

    with open("classifications.json", encoding="utf-8") as f:
        all_cls = json.load(f)

    # Build lookup: current filename → classification
    # We match by reconstructed target path
    cls_by_file = {r["file"].replace("\\", "/"): r for r in all_cls}

    total_moved = 0

    for niche_dir in sorted(MEDIA_DIR.iterdir()):
        if not niche_dir.is_dir() or niche_dir.name == "bucket":
            continue
        ba_dir = niche_dir / "ba"
        if not ba_dir.exists():
            continue

        # Collect flat BA files (not already in subfolders)
        flat_files = [f for f in ba_dir.iterdir() if f.is_file() and f.suffix.lower() in (".jpg", ".png")]
        if not flat_files:
            continue

        # Find original filename for each current file via classifications
        # Match by niche + use=ba + sequential number
        niche = niche_dir.name.lower()
        ba_cls = [
            r for r in all_cls
            if r.get("niche") == niche
            and r.get("use") == "ba"
            and "error" not in r
        ]

        # Group ba_cls by series key from original filename
        series_groups: dict[str, list] = defaultdict(list)
        for r in ba_cls:
            orig_name = Path(r["file"]).name
            key = _series_key(orig_name)
            series_groups[key].append(r)

        # Find series with 2+ files AND mixed subtypes (real series, not just duplicates)
        set_idx = 1
        for key, members in sorted(series_groups.items()):
            if len(members) < 2:
                continue
            subtypes = {r.get("subtype") for r in members}
            # Only group if there are mixed subtypes (real series) or explicit story format
            is_real_series = (
                len(subtypes) > 1
                or "story" in subtypes
                or "process" in subtypes
                or any("story" in Path(r["file"]).name.lower() for r in members)
            )
            if not is_real_series:
                continue

            set_dir = ba_dir / f"set-{set_idx:03d}"
            set_dir.mkdir(exist_ok=True)

            # Find current files for these classifications
            # Use subtype + rough ordering to match current renamed files
            moved_count = 0
            for r in sorted(members, key=lambda x: x.get("subtype", "")):
                orig_name = Path(r["file"]).name
                # Try to find the file in ba_dir by subtype
                subtype = r.get("subtype", "unknown")
                candidates = [f for f in flat_files if f"_{subtype}_" in f.name]
                if candidates:
                    src = candidates[0]
                    flat_files.remove(src)
                    # Rename inside set dir: set-001/niche_ba_subtype_NNN.jpg
                    dest = set_dir / src.name
                    shutil.move(str(src), str(dest))
                    print(f"  SET-{set_idx:03d} [{niche}/{key}] {src.name}")
                    moved_count += 1

            if moved_count > 0:
                # Rename set dir to include file count
                final_dir = ba_dir / f"set-{set_idx:03d}_{moved_count}files"
                if not final_dir.exists():
                    set_dir.rename(final_dir)
                total_moved += moved_count
                set_idx += 1
            else:
                # Nothing matched, remove empty dir
                try:
                    set_dir.rmdir()
                except OSError:
                    pass

    print(f"  → Grouped {total_moved} files into series sets")
    return total_moved


# ─────────────────────────────────────────────────────────
# Step 3: Bucket for remaining unknowns in old folders
# ─────────────────────────────────────────────────────────

def bucket_orphans():
    """Move files still sitting in old unstructured subfolders → bucket."""
    print("\n[3/3] Moving orphan files from old folders → bucket")

    OLD_PATTERN = re.compile(
        r"(Befor After|Hero Page|Project|before after|hero main photo|"
        r"projects|Before-after|BeforeAfter|HeroPage|Before after|Hero page|"
        r"security home|bathroom remodelin|floring|— )",
        re.IGNORECASE
    )

    moved = 0
    for path in sorted(MEDIA_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".avif"}:
            continue
        # Skip files already in clean structure
        parts = path.relative_to(MEDIA_DIR).parts
        if len(parts) >= 2 and parts[0] in {"hvac","bathroom","shower","flooring","siding","security","bucket"}:
            if parts[1] in {"ba","hero","project"} or parts[1].startswith("set-"):
                continue

        # Check if in old-style folder
        rel = str(path.relative_to(MEDIA_DIR))
        if OLD_PATTERN.search(rel):
            # Determine niche from path
            top = parts[0].lower() if parts else "unknown"
            dest_dir = BUCKET_DIR / "unclassified" / top
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / path.name
            if not dest.exists():
                shutil.move(str(path), str(dest))
                print(f"  BUCKET [unclassified/{top}] {path.name}")
                moved += 1

    print(f"  → Moved {moved} orphan files to bucket")
    return moved


if __name__ == "__main__":
    BUCKET_DIR.mkdir(parents=True, exist_ok=True)
    fix_invalid_uses()
    group_ba_series()
    bucket_orphans()

    print("\n\nFinal structure:")
    for p in sorted(MEDIA_DIR.rglob("*")):
        if p.is_dir():
            count = len(list(p.glob("*.jpg"))) + len(list(p.glob("*.png")))
            if count > 0:
                print(f"  {p.relative_to(MEDIA_DIR)}/ ({count} files)")
