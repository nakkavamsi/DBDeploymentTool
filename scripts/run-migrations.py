#!/usr/bin/env python3
"""Thin wrapper — prefer `sql-mig run`. Implementation lives in this package."""
from __future__ import annotations

try:
    from sql_mig.run import main
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "sql-migration-tools is not installed.\n"
        "  pip install -e .\n"
        "  # from a database project: pip install -r requirements.txt"
    ) from exc

if __name__ == "__main__":
    raise SystemExit(main())
