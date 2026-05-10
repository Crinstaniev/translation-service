#!/usr/bin/env python3
from __future__ import annotations

import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Entry point for the dedicated manga translation pipeline."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="input/manga",
        help="Directory containing manga source files.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="out/manga",
        help="Directory for manga translation outputs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("Manga pipeline is separated but not implemented yet.")
    print(f"input_dir={args.input_dir}")
    print(f"output_dir={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
