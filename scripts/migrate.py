#!/usr/bin/env python3
"""Apply schema.sql to DATABASE_URL. Idempotent."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / '.env', interpolate=False)
load_dotenv(ROOT / '.env.local', interpolate=False, override=True)

import db  # noqa: E402


def main() -> int:
    if not db.configured():
        print('DATABASE_URL is not set (add Neon from the Vercel Marketplace, then `vercel env pull`).')
        return 1
    db.apply_schema()
    print('schema applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
