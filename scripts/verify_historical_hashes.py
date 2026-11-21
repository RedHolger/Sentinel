#!/usr/bin/env python3
"""Verify immutable baseline artifact digests."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("handoff/00/artifact-manifest.sha256"),
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest

    if not manifest.is_file():
        print(f"manifest not found: {manifest}", file=sys.stderr)
        return 1

    verified = 0
    failures: list[str] = []
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            failures.append(f"{manifest}:{line_number}: malformed line")
            continue
        expected, relative = parts
        path = root / relative
        if not path.is_file():
            failures.append(f"missing: {relative}")
            continue
        actual = sha256(path)
        if actual.lower() != expected.lower():
            failures.append(f"corrupt: {relative} (expected {expected}, actual {actual})")
            continue
        verified += 1

    if failures:
        print(f"historical manifest: {verified} verified, {len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"historical manifest: {verified} verified, 0 failure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
