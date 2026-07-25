#!/usr/bin/env python3
"""
Create a new migration script with a unique Migration-Id.

Usage:
  sql-mig new --version 2.2.0 --name dbo.person.add_status
  sql-mig new --version 2.2.0 --name dbo.person.add_status --content "ALTER TABLE ..."

The generated filename format is:
  {sequence:02d}_{migration_id}_{name}.sql

Example:
  Deployments/Migrations/2.2.0/01_20260522143000_a3f9b2c1_dbo.person.add_status.sql
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sql_mig.migration_id import (
    SEMVER_FOLDER_PATTERN,
    build_migration_header,
    generate_migration_id,
    next_sequence_number,
    slugify_name,
)

DEFAULT_TEMPLATE = """-- TODO: add migration SQL
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a new migration SQL file.")
    parser.add_argument(
        "--version",
        required=True,
        help="Semantic version folder (MAJOR.MINOR.PATCH), e.g. 2.2.0",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Logical migration name, e.g. dbo.person.add_status",
    )
    parser.add_argument(
        "--content",
        help="Optional SQL body. If omitted, a TODO template is used.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Database project root (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the path and content without writing a file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not SEMVER_FOLDER_PATTERN.fullmatch(args.version):
        print(
            "ERROR: --version must be MAJOR.MINOR.PATCH (example: 2.2.0)",
            file=sys.stderr,
        )
        return 1

    try:
        slug = slugify_name(args.name)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    migrations_dir = args.project_root / "Deployments" / "Migrations" / args.version
    migrations_dir.mkdir(parents=True, exist_ok=True)

    sequence = next_sequence_number(migrations_dir)
    migration_id = generate_migration_id()
    filename = f"{sequence:02d}_{migration_id}_{slug}.sql"
    output_path = migrations_dir / filename

    if output_path.exists():
        print(f"ERROR: File already exists: {output_path}", file=sys.stderr)
        return 1

    body = args.content if args.content is not None else DEFAULT_TEMPLATE
    if not body.endswith("\n"):
        body += "\n"

    header = build_migration_header(migration_id)
    content = header + body

    if args.dry_run:
        print(f"Would create: {output_path}")
        print()
        print(content)
        return 0

    output_path.write_text(content, encoding="utf-8")
    print(f"Created migration: {output_path.relative_to(args.project_root)}")
    print(f"Migration-Id: {migration_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
