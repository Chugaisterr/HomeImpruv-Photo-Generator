"""
Entry point: python -m processor
"""
import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

try:
    from processor.pipeline import run_pipeline, run_batch
except ImportError:
    run_pipeline = run_batch = None
from processor.ai_generate import generate_image
from utils.naming import build_filename

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


def cmd_single(args):
    run_pipeline(
        input_path=Path(args.input),
        output_dir=Path(args.output),
        steps=args.steps.split(",") if args.steps else None,
        template=args.template,
    )


def cmd_batch(args):
    run_batch(
        input_dir=Path(args.input),
        output_dir=Path(args.output),
        steps=args.steps.split(",") if args.steps else None,
        template=args.template,
    )


def cmd_generate(args):
    name = build_filename(args.category, args.subfolder, args.descriptor, 1, "generated")
    out = Path(args.output) / name
    generate_image(
        output_path=out,
        prompt=args.prompt,
        reference_image=Path(args.reference) if args.reference else None,
    )
    print(f"Generated: {out}")


def cmd_classify(args):
    """Classify all images in media-dir using GPT-4o Mini via OpenRouter."""
    from processor.classifier import classify_all, print_summary

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set in .env")
        return

    media_dir = Path(args.media_dir)
    output = Path(args.output)

    results = classify_all(
        media_dir=media_dir,
        output_path=output,
        api_key=api_key,
        workers=args.workers,
        model=args.model,
        resume=not args.no_resume,
    )
    print_summary(results)
    print(f"Classifications saved to: {output}")


def cmd_organize(args):
    """Rename and move media files based on classifications.json."""
    from processor.organizer import build_plan, print_plan, execute_plan

    classifications_path = Path(args.classifications)
    media_dir = Path(args.media_dir)

    if not classifications_path.exists():
        print(f"ERROR: {classifications_path} not found. Run 'classify' first.")
        return

    import json
    with open(classifications_path, encoding="utf-8") as f:
        classifications = json.load(f)

    plan = build_plan(
        classifications=classifications,
        media_dir=media_dir,
        min_quality=args.min_quality,
    )

    print_plan(plan, media_dir)

    if args.dry_run:
        print("DRY RUN — no files were moved. Remove --dry-run to execute.")
        return

    confirm = input(f"Move {len(plan.ops)} files? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    log_path = media_dir / "rename_log.csv"
    summary = execute_plan(plan, media_dir, log_path=log_path)
    print(f"\nDone: {summary['success']} moved, {summary['failed']} failed, "
          f"{summary['skipped']} skipped")
    print(f"Rename log: {log_path}")


def cmd_enhance(args):
    """AI-enhance all photos via Gemini, then normalize to 1920×1080."""
    from processor.enhancer import enhance_batch, print_summary

    source = Path(args.source)
    output = Path(args.output)

    results = enhance_batch(
        source_dir=source,
        output_dir=output,
        model=args.model,
        normalize=not args.no_normalize,
        norm_width=args.width,
        norm_height=args.height,
        norm_quality=args.quality,
        workers=args.workers,
        resume=not args.no_resume,
    )
    print_summary(results)
    print(f"\nEnhanced photos saved to: {output}")


def cmd_normalize(args):
    """Normalize all images to gold standard 1920×1080 with smart fill."""
    from processor.normalizer import normalize_batch

    source = Path(args.source)
    output = Path(args.output) if args.output else None

    results = normalize_batch(
        source_dir=source,
        output_dir=output,
        width=args.width,
        height=args.height,
        quality=args.quality,
        in_place=args.in_place,
        output_suffix=args.suffix,
        workers=args.workers,
    )
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nNormalized: {ok}/{len(results)} images to {args.width}x{args.height}")
    if output:
        print(f"Output dir: {output}")


def main():
    parser = argparse.ArgumentParser(prog="processor")
    sub = parser.add_subparsers()

    p_single = sub.add_parser("single")
    p_single.add_argument("--input", required=True)
    p_single.add_argument("--output", required=True)
    p_single.add_argument("--steps")
    p_single.add_argument("--template")
    p_single.set_defaults(func=cmd_single)

    p_batch = sub.add_parser("batch")
    p_batch.add_argument("--input", required=True)
    p_batch.add_argument("--output", required=True)
    p_batch.add_argument("--steps")
    p_batch.add_argument("--template")
    p_batch.set_defaults(func=cmd_batch)

    p_gen = sub.add_parser("generate")
    p_gen.add_argument("--prompt", required=True)
    p_gen.add_argument("--output", required=True)
    p_gen.add_argument("--category", required=True)
    p_gen.add_argument("--subfolder", required=True)
    p_gen.add_argument("--descriptor", required=True)
    p_gen.add_argument("--reference")
    p_gen.set_defaults(func=cmd_generate)

    # ── classify: analyze all images with GPT-4o Mini
    p_cls = sub.add_parser(
        "classify",
        help="Classify all media images using GPT-4o Mini vision",
    )
    p_cls.add_argument(
        "--media-dir", default="Media",
        help="Root media folder to scan (default: Media)",
    )
    p_cls.add_argument(
        "--output", default="classifications.json",
        help="Output JSON file path (default: classifications.json)",
    )
    p_cls.add_argument(
        "--workers", type=int, default=5,
        help="Parallel workers (default: 5)",
    )
    p_cls.add_argument(
        "--model", default="openai/gpt-4o-mini",
        help="Vision model via OpenRouter (default: openai/gpt-4o-mini)",
    )
    p_cls.add_argument(
        "--no-resume", action="store_true",
        help="Re-classify all files, ignoring existing results",
    )
    p_cls.set_defaults(func=cmd_classify)

    # ── organize: rename + move files based on classifications
    p_org = sub.add_parser(
        "organize",
        help="Rename and move media files based on classifications.json",
    )
    p_org.add_argument(
        "--classifications", default="classifications.json",
        help="Classification JSON file (default: classifications.json)",
    )
    p_org.add_argument(
        "--media-dir", default="Media",
        help="Root media folder (default: Media)",
    )
    p_org.add_argument(
        "--dry-run", action="store_true",
        help="Print plan without moving any files",
    )
    p_org.add_argument(
        "--min-quality", type=int, default=1,
        help="Skip files with quality score below this (default: 1)",
    )
    p_org.set_defaults(func=cmd_organize)

    # ── enhance: AI quality enhancement via Gemini
    p_enh = sub.add_parser(
        "enhance",
        help="AI-enhance photos via Gemini (remove watermarks, color grade, sharpen) then normalize to 1920x1080",
    )
    p_enh.add_argument(
        "source",
        help="Source folder (e.g. Media or Media/hvac/hero)",
    )
    p_enh.add_argument(
        "--output", "-o", default="Media_enhanced",
        help="Output folder (default: Media_enhanced)",
    )
    p_enh.add_argument(
        "--model", default="google/gemini-3.1-flash-image-preview",
        help="Gemini model via OpenRouter (default: gemini-3.1-flash-image-preview)",
    )
    p_enh.add_argument(
        "--workers", type=int, default=2,
        help="Parallel workers — keep low for rate limits (default: 2)",
    )
    p_enh.add_argument(
        "--no-normalize", action="store_true",
        help="Skip 1920x1080 normalization after enhancement",
    )
    p_enh.add_argument(
        "--width", type=int, default=1920,
        help="Normalize width (default: 1920)",
    )
    p_enh.add_argument(
        "--height", type=int, default=1080,
        help="Normalize height (default: 1080)",
    )
    p_enh.add_argument(
        "--quality", type=int, default=92,
        help="JPEG quality (default: 92)",
    )
    p_enh.add_argument(
        "--no-resume", action="store_true",
        help="Re-enhance all files, ignoring existing results",
    )
    p_enh.set_defaults(func=cmd_enhance)

    # ── normalize: gold standard 1920x1080 normalization
    p_norm = sub.add_parser(
        "normalize",
        help="Normalize images to gold standard 1920x1080 with smart fill",
    )
    p_norm.add_argument(
        "source",
        help="Source folder (e.g. Media/flooring/hero or Media)",
    )
    p_norm.add_argument(
        "--output", "-o", default=None,
        help="Output folder. If omitted, saves next to original with suffix.",
    )
    p_norm.add_argument(
        "--width", type=int, default=1920,
        help="Target width in px (default: 1920)",
    )
    p_norm.add_argument(
        "--height", type=int, default=1080,
        help="Target height in px (default: 1080)",
    )
    p_norm.add_argument(
        "--quality", type=int, default=92,
        help="JPEG quality 1-100 (default: 92)",
    )
    p_norm.add_argument(
        "--in-place", action="store_true",
        help="Overwrite originals (converts webp/png to jpg in-place)",
    )
    p_norm.add_argument(
        "--suffix", default="_norm",
        help="Filename suffix when not using --output or --in-place (default: _norm)",
    )
    p_norm.add_argument(
        "--workers", type=int, default=4,
        help="Parallel workers (default: 4)",
    )
    p_norm.set_defaults(func=cmd_normalize)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
