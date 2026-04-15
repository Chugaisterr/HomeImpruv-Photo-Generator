"""
HomeIQ Photo Processor — CLI entry point.

Commands:
  classify  — AI classify + caption all photos via GPT-4o Mini
  organize  — rename + move files based on classifications.json
  enhance   — AI-enhance photos via Riverflow/Gemini (upscale, clean)
"""
import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


def cmd_classify(args):
    """Classify + caption all images via GPT-4o Mini."""
    from processor.classifier import classify_all, print_summary

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set in .env")
        return

    results = classify_all(
        media_dir=Path(args.media_dir),
        output_path=Path(args.output),
        api_key=api_key,
        workers=args.workers,
        model=args.model,
        resume=not args.no_resume,
    )
    print_summary(results)
    print(f"Classifications saved to: {args.output}")


def cmd_organize(args):
    """Rename and move files based on classifications.json."""
    from processor.organizer import build_plan, print_plan, execute_plan
    import json

    classifications_path = Path(args.classifications)
    media_dir = Path(args.media_dir)

    if not classifications_path.exists():
        print(f"ERROR: {classifications_path} not found. Run 'classify' first.")
        return

    with open(classifications_path, encoding="utf-8") as f:
        classifications = json.load(f)

    plan = build_plan(classifications=classifications, media_dir=media_dir, min_quality=args.min_quality)
    print_plan(plan, media_dir)

    if args.dry_run:
        print("DRY RUN — no files moved. Remove --dry-run to execute.")
        return

    confirm = input(f"Move {len(plan.ops)} files? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    log_path = media_dir / "rename_log.csv"
    summary = execute_plan(plan, media_dir, log_path=log_path)
    print(f"\nDone: {summary['success']} moved, {summary['failed']} failed, {summary['skipped']} skipped")
    print(f"Rename log: {log_path}")


def cmd_enhance(args):
    """AI-enhance photos via Riverflow/Gemini."""
    from processor.enhancer import enhance_batch, print_summary

    results = enhance_batch(
        source_dir=Path(args.source),
        output_dir=Path(args.output),
        model=args.model,
        normalize=not args.no_normalize,
        norm_width=args.width,
        norm_height=args.height,
        norm_quality=args.quality,
        workers=args.workers,
        resume=not args.no_resume,
        ai_max_mp=args.ai_max_mp,
        ai_only=args.ai_only,
    )
    print_summary(results)
    print(f"\nEnhanced photos saved to: {args.output}")


def main():
    parser = argparse.ArgumentParser(
        prog="python __main__.py",
        description="HomeIQ Photo Processor",
    )
    sub = parser.add_subparsers(title="commands")

    # ── classify ──────────────────────────────────────────────────────────
    p_cls = sub.add_parser("classify", help="AI classify + caption all photos (GPT-4o Mini)")
    p_cls.add_argument("--media-dir", default="Media", help="Root media folder (default: Media)")
    p_cls.add_argument("--output", default="classifications.json", help="Output JSON (default: classifications.json)")
    p_cls.add_argument("--workers", type=int, default=5, help="Parallel workers (default: 5)")
    p_cls.add_argument("--model", default="openai/gpt-4o-mini", help="Vision model via OpenRouter")
    p_cls.add_argument("--no-resume", action="store_true", help="Re-classify all, ignore existing results")
    p_cls.set_defaults(func=cmd_classify)

    # ── organize ──────────────────────────────────────────────────────────
    p_org = sub.add_parser("organize", help="Rename + move files based on classifications.json")
    p_org.add_argument("--classifications", default="classifications.json")
    p_org.add_argument("--media-dir", default="Media")
    p_org.add_argument("--dry-run", action="store_true", help="Preview without moving files")
    p_org.add_argument("--min-quality", type=int, default=1)
    p_org.set_defaults(func=cmd_organize)

    # ── enhance ───────────────────────────────────────────────────────────
    p_enh = sub.add_parser("enhance", help="AI-enhance photos via Riverflow/Gemini")
    p_enh.add_argument("source", help="Source folder (e.g. Media or Media/hvac/hero)")
    p_enh.add_argument("--output", "-o", default="Media_enhanced")
    p_enh.add_argument("--model", default="sourceful/riverflow-v2-fast",
                       help="Model (default: sourceful/riverflow-v2-fast)")
    p_enh.add_argument("--workers", type=int, default=2)
    p_enh.add_argument("--no-normalize", action="store_true", help="Skip 1920x1080 resize")
    p_enh.add_argument("--width", type=int, default=1920)
    p_enh.add_argument("--height", type=int, default=1080)
    p_enh.add_argument("--quality", type=int, default=92)
    p_enh.add_argument("--ai-max-mp", type=float, default=2.0,
                       help="Photos below this MP get AI-enhanced (default: 2.0)")
    p_enh.add_argument("--no-resume", action="store_true")
    p_enh.add_argument("--ai-only", action="store_true",
                       help="Only process small photos, skip large ones entirely")
    p_enh.set_defaults(func=cmd_enhance)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
