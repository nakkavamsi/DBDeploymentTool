#!/usr/bin/env python3
"""
Ensure a migration SQL file has a unique -- Migration-Id header.

Used by:
  - manual repair: sql-mig stamp path/to/file.sql
  - batch repair:  sql-mig stamp --all
  - Cursor hook afterFileEdit for new files under Deployments/Migrations/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sql_mig.migration_id import (
    extract_migration_id,
    generate_migration_id,
    has_migration_id,
    prepend_migration_header,
)

MIGRATIONS_SUFFIX = f"{Path('Deployments') / 'Migrations'}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stamp migration files with unique IDs.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Migration .sql file path(s) to stamp",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Stamp every migration under Deployments/Migrations/ missing an id",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Database project root (default: current directory)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print when a file is changed",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rewrite headers to Migration-Id only (removes version/created lines)",
    )
    return parser.parse_args()


def is_migration_path(path: Path) -> bool:
    normalized = path.as_posix()
    return MIGRATIONS_SUFFIX in normalized and path.suffix.lower() == ".sql"


def stamp_file(path: Path, quiet: bool = False) -> bool:
    if not path.is_file():
        return False

    content = path.read_text(encoding="utf-8")
    if has_migration_id(content):
        if not quiet:
            existing = extract_migration_id(content)
            print(f"Skip (already stamped): {path} [{existing}]")
        return False

    migration_id = generate_migration_id()
    updated = prepend_migration_header(content, migration_id)
    path.write_text(updated, encoding="utf-8")
    print(f"Stamped: {path} -> Migration-Id: {migration_id}")
    return True


def refresh_file(path: Path, quiet: bool = False) -> bool:
    if not path.is_file():
        return False

    content = path.read_text(encoding="utf-8")
    migration_id = extract_migration_id(content)
    if not migration_id:
        if not quiet:
            print(f"Skip (no Migration-Id): {path}")
        return False

    updated = prepend_migration_header(content, migration_id)
    if updated == content:
        if not quiet:
            print(f"Skip (already normalized): {path}")
        return False

    path.write_text(updated, encoding="utf-8")
    print(f"Refreshed: {path}")
    return True


def main() -> int:
    args = parse_args()
    project_root = args.project_root
    targets: list[Path] = [Path(p).resolve() for p in args.paths]

    if args.all:
        migrations_root = project_root / "Deployments" / "Migrations"
        if not migrations_root.exists():
            print(f"ERROR: Missing migrations folder: {migrations_root}", file=sys.stderr)
            return 1
        targets.extend(sorted(migrations_root.glob("*/*.sql")))

    if not targets:
        print("ERROR: Provide file path(s) or use --all", file=sys.stderr)
        return 1

    changed = 0
    for path in targets:
        if not is_migration_path(path):
            if not args.quiet:
                print(f"Skip (not a migration path): {path}")
            continue
        if args.refresh:
            if refresh_file(path, quiet=args.quiet):
                changed += 1
        elif stamp_file(path, quiet=args.quiet):
            changed += 1

    if not args.quiet:
        action = "refreshed" if args.refresh else "stamped"
        print(f"Done. {changed} file(s) {action}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
