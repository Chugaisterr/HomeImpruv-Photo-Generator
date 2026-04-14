"""
Media file organizer — applies classification results to rename and move files.

Reads classifications.json, builds target paths using the canonical naming
convention, and either dry-runs (prints plan) or executes (moves + converts).

Naming convention:  {niche}_{use}_{subtype}_{seq:03d}.jpg
Target structure:   Media/{niche}/{use}/{niche}_{use}_{subtype}_{seq:03d}.jpg

Usage:
  python -m processor organize --classifications classifications.json --media-dir Media --dry-run
  python -m processor organize --classifications classifications.json --media-dir Media
  python -m processor organize --classifications classifications.json --media-dir Media --min-quality 5
"""
import csv
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image

from utils.naming import build_media_filename

logger = logging.getLogger(__name__)

# Files we skip — classification failed or niche unknown
SKIP_NICHES = {"unknown"}
SKIP_USES = {"unknown"}

# Source formats that need conversion to JPEG
CONVERT_FORMATS = {".webp", ".avif", ".png", ".bmp", ".tiff"}


@dataclass
class RenameOp:
    source: Path
    target: Path
    needs_convert: bool
    classification: dict
    seq: int


@dataclass
class OrganizerPlan:
    ops: list[RenameOp] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def _target_dir(media_dir: Path, niche: str, use: str) -> Path:
    return media_dir / niche / use


def build_plan(
    classifications: list[dict],
    media_dir: Path,
    min_quality: int = 1,
) -> OrganizerPlan:
    """
    Build rename plan from classification results.

    - Groups by (niche, use, subtype) and assigns sequential numbers.
    - Skips entries with errors, unknown niche/use, or quality below threshold.
    - Sorts each group by original filename for deterministic sequencing.
    """
    plan = OrganizerPlan()

    # Group valid entries
    groups: dict[tuple, list[dict]] = {}
    for r in classifications:
        if "error" in r:
            plan.skipped.append({**r, "reason": "classification_error"})
            continue
        niche = r.get("niche", "unknown")
        use = r.get("use", "unknown")
        subtype = r.get("subtype", "unknown")
        quality = r.get("quality_score", 0)

        if niche in SKIP_NICHES:
            plan.skipped.append({**r, "reason": "unknown_niche"})
            continue
        if use in SKIP_USES:
            plan.skipped.append({**r, "reason": "unknown_use"})
            continue
        if quality < min_quality:
            plan.skipped.append({**r, "reason": f"quality_too_low ({quality})"})
            continue

        key = (niche, use, subtype)
        groups.setdefault(key, []).append(r)

    # Build ops with sequential numbering
    for (niche, use, subtype), entries in sorted(groups.items()):
        entries_sorted = sorted(entries, key=lambda r: r["file"])
        for seq, r in enumerate(entries_sorted, start=1):
            source = media_dir / r["file"]
            if not source.exists():
                plan.skipped.append({**r, "reason": "source_missing"})
                continue

            needs_convert = source.suffix.lower() in CONVERT_FORMATS
            filename = build_media_filename(niche, use, subtype, seq)
            target = _target_dir(media_dir, niche, use) / filename

            plan.ops.append(RenameOp(
                source=source,
                target=target,
                needs_convert=needs_convert,
                classification=r,
                seq=seq,
            ))

    return plan


def print_plan(plan: OrganizerPlan, media_dir: Path) -> None:
    """Print dry-run summary."""
    print(f"\n{'='*60}")
    print(f"ORGANIZE PLAN - {len(plan.ops)} files to process")
    print(f"{'='*60}\n")

    current_group = None
    for op in sorted(plan.ops, key=lambda o: o.target):
        r = op.classification
        group = f"{r['niche']}/{r['use']}/{r['subtype']}"
        if group != current_group:
            print(f"  [{group}]")
            current_group = group

        convert_tag = " [convert]" if op.needs_convert else ""
        text_tag = " [has_text]" if r.get("has_text_overlay") else ""
        src_rel = op.source.relative_to(media_dir)
        tgt_rel = op.target.relative_to(media_dir)
        print(f"    {src_rel}")
        print(f"    -> {tgt_rel}{convert_tag}{text_tag}")
        print()

    if plan.skipped:
        print(f"\n  SKIPPED ({len(plan.skipped)}):")
        reasons: dict[str, int] = {}
        for s in plan.skipped:
            r = s.get("reason", "?")
            reasons[r] = reasons.get(r, 0) + 1
        for reason, count in sorted(reasons.items()):
            print(f"    {reason}: {count}")
    print()


def execute_plan(
    plan: OrganizerPlan,
    media_dir: Path,
    log_path: Optional[Path] = None,
) -> dict:
    """
    Execute the rename plan: move + convert files.

    Returns summary dict. Saves rename_log.csv if log_path provided.
    """
    log_rows = []
    success = 0
    failed = 0

    for op in plan.ops:
        op.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if op.needs_convert:
                _convert_and_save(op.source, op.target)
                op.source.unlink()  # remove original after successful conversion
                action = "converted+moved"
            else:
                shutil.move(str(op.source), str(op.target))
                action = "moved"

            logger.info(f"  OK {op.source.name} -> {op.target.name}")
            log_rows.append({
                "source": str(op.source),
                "target": str(op.target),
                "action": action,
                "niche": op.classification.get("niche"),
                "use": op.classification.get("use"),
                "subtype": op.classification.get("subtype"),
                "quality_score": op.classification.get("quality_score"),
                "has_text_overlay": op.classification.get("has_text_overlay"),
                "status": "ok",
                "error": "",
            })
            success += 1

        except Exception as e:
            logger.error(f"  ✗ {op.source.name}: {e}")
            log_rows.append({
                "source": str(op.source),
                "target": str(op.target),
                "action": "failed",
                "status": "error",
                "error": str(e),
            })
            failed += 1

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["source", "target", "action", "niche", "use", "subtype",
                  "quality_score", "has_text_overlay", "status", "error"]
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(log_rows)
        logger.info(f"Rename log saved: {log_path}")

    summary = {
        "total": len(plan.ops),
        "success": success,
        "failed": failed,
        "skipped": len(plan.skipped),
    }
    logger.info(
        f"Done — {success} moved, {failed} failed, {len(plan.skipped)} skipped"
    )
    return summary


def _convert_and_save(source: Path, target: Path) -> None:
    """Open source image, save as JPEG to target path."""
    with Image.open(source) as img:
        img = img.convert("RGB")
        img.save(target, format="JPEG", quality=92)
