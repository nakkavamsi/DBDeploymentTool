#!/usr/bin/env python3
"""Unified CLI for sql-migration-tools (`sql-mig`)."""

from __future__ import annotations

import sys


COMMANDS = {
    "sync": "sql_mig.sync",
    "run": "sql_mig.run",
    "new": "sql_mig.new_migration",
    "stamp": "sql_mig.stamp",
    "bootstrap": "sql_mig.bootstrap",
}


def _print_help() -> None:
    print(
        """sql-mig — shared SQL Server migration tooling

Usage:
  sql-mig <command> [options]

Commands:
  sync       Sync SchemaModel/ from Deployments/Migrations/
  run        Apply pending migrations (sqlcmd + history tables)
  new        Scaffold a new migration script
  stamp      Stamp -- Migration-Id headers
  bootstrap  Split a baseline SQL export into migration files

Examples:
  sql-mig sync
  sql-mig new --version 2.3.0 --name dbo.person.add_status
  sql-mig run -S localhost -d MyDb -U sa -C --status
  sql-mig stamp --all
  sql-mig bootstrap --input baseline.sql --version 1.0.0 --sync

Install into a database project:
  pip install -e /path/to/sql-migration-tools
  # or from GitHub:
  pip install "sql-migration-tools @ git+https://github.com/<org>/sql-migration-tools.git"
"""
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        _print_help()
        return 0

    command = args[0]
    if command not in COMMANDS:
        print(f"ERROR: Unknown command {command!r}. Try: sql-mig --help", file=sys.stderr)
        return 2

    module_name = COMMANDS[command]
    module = __import__(module_name, fromlist=["main"])
    # Pass remaining args to the subcommand by temporarily rewriting argv
    original = sys.argv[:]
    try:
        sys.argv = [f"sql-mig {command}", *args[1:]]
        return int(module.main())
    finally:
        sys.argv = original


if __name__ == "__main__":
    raise SystemExit(main())
