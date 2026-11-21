#!/usr/bin/env python3
"""Compatibility wrapper delegating to scripts/verify_stage11_schema.py."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "scripts"))
import verify_stage11_schema

if __name__ == "__main__":
    sys.exit(verify_stage11_schema.main())
