"""
Entry point: python -m processor
"""
import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from processor.pipeline import run_pipeline, run_batch
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

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
