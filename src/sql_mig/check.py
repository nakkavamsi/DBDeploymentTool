#!/usr/bin/env python3
"""
Compile every migration script and report SQL problems.

Migrations are excluded from the dacpac build, so `dotnet build` does not
compile Deployments/Migrations/*.sql. This command sends each script to
sqlcmd in deploy order:

  1. Version folders from lowest MAJOR.MINOR.PATCH to highest.
  2. Filenames A to Z inside each folder (01_, 02_, ...).

The Migration-Id does not change that order. `sql-mig run` uses the same order.

Parse and compile both run unless one is stopped:

  1. Parse each file with SET PARSEONLY ON and report every syntax error.
  2. Compile the chain on a throwaway database, then drop it. A later script
     sees objects created by earlier scripts, which proves primary keys,
     foreign keys, and procedures.

Nothing is written to dbo.__MigrationHistory.

  sql-mig check -S localhost -U sa -C
  sql-mig check -S localhost -U sa -C --skip-parse
  sql-mig check -S localhost -U sa -C --skip-compile
  sql-mig check -S localhost -d MyDb -U sa -C --noexec
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sql_mig.run import (
    MigrationScript,
    SqlCmdRunner,
    discover_migration_scripts,
)

SQL_ERROR_PATTERN = re.compile(
    r"^Msg\s+\d+,\s+Level\s+(\d+)",
    re.IGNORECASE | re.MULTILINE,
)


def script_has_sql_error(stdout: str, stderr: str, returncode: int) -> bool:
    if returncode != 0:
        return True
    text = f"{stdout}\n{stderr}"
    for match in SQL_ERROR_PATTERN.finditer(text):
        if int(match.group(1)) >= 11:
            return True
    return False


def scratch_database_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"sqlmig_check_{stamp}_{uuid.uuid4().hex[:6]}"


def quote_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def compile_script(
    runner: SqlCmdRunner,
    script: MigrationScript,
    *,
    database: str,
    mode: str,
):
    if mode == "execute":
        return runner.run_file(script.path, database=database)

    setting = "SET NOEXEC ON;" if mode == "noexec" else "SET PARSEONLY ON;"
    content = script.path.read_text(encoding="utf-8")
    if content and not content.endswith("\n"):
        content += "\n"
    wrapped = f"{setting}\nGO\n{content}"
    temp_path = script.path.with_name(f".{script.path.stem}.sqlmig-check.sql")
    temp_path.write_text(wrapped, encoding="utf-8")
    try:
        return runner.run_file(temp_path, database=database)
    finally:
        temp_path.unlink(missing_ok=True)


def create_scratch_database(runner: SqlCmdRunner, name: str) -> None:
    sql = f"IF DB_ID(N'{name}') IS NULL CREATE DATABASE {quote_ident(name)};"
    result = runner.run_query(sql, database="master")
    if script_has_sql_error(result.stdout or "", result.stderr or "", result.returncode):
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to create scratch database {name}:\n{detail}")


def drop_scratch_database(runner: SqlCmdRunner, name: str) -> bool:
    ident = quote_ident(name)
    sql = (
        f"IF DB_ID(N'{name}') IS NOT NULL "
        "BEGIN "
        f"ALTER DATABASE {ident} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
        f"DROP DATABASE {ident}; "
        "END"
    )
    result = runner.run_query(sql, database="master")
    if script_has_sql_error(result.stdout or "", result.stderr or "", result.returncode):
        detail = (result.stderr or result.stdout or "").strip()
        print(f"WARNING: Could not drop scratch database {name}:\n{detail}", file=sys.stderr)
        return False
    return True


def format_failure(result) -> str:
    return (result.stderr or result.stdout or "").strip()


def compile_plan_lines(
    scripts: list[MigrationScript],
    *,
    project_root: Path,
    do_parse: bool,
    do_compile: bool,
    compile_mode: str,
    parse_database: str | None,
    compile_database: str | None,
    parse_stopped_by: str | None,
    compile_stopped_by: str | None,
) -> list[str]:
    """Text printed before sqlcmd runs: which passes are on, then deploy order."""
    count = len(scripts)
    lines = [
        "Script order: version folders from lowest MAJOR.MINOR.PATCH to highest,",
        "then filenames A to Z inside each folder. check and run use this order.",
        "The Migration-Id does not change it.",
        "",
    ]
    if do_parse and do_compile:
        lines.append("Parse and compile both run. Stop one with --skip-parse or --skip-compile.")
    elif do_parse:
        lines.append(f"Parse is running. Compile is stopped ({compile_stopped_by}).")
    else:
        lines.append(f"Compile is running. Parse is stopped ({parse_stopped_by}).")
    lines.append("")

    if do_parse and parse_database is not None:
        lines.extend(
            [
                f"Parse: SET PARSEONLY ON for each of {count} script(s) against {parse_database}.",
                "Each file is parsed on its own. Every syntax error is reported.",
                "",
            ]
        )
    if do_compile and compile_mode == "execute" and compile_database is not None:
        lines.extend(
            [
                f"Compile: execute {count} script(s) on scratch database {compile_database}, then drop it.",
                "Each script runs after the ones above it, so a foreign key or procedure",
                "can use objects those earlier scripts created. Stops at the first error.",
                "dbo.__MigrationHistory is not written.",
                "",
            ]
        )
    elif do_compile and compile_database is not None:
        lines.extend(
            [
                f"Compile: SET NOEXEC ON for each of {count} script(s) against {compile_database}.",
                "Each file is compiled on its own and nothing is changed. Every syntax error is reported.",
                "A foreign key parent and a procedure column are proved when compile executes the chain.",
                "",
            ]
        )

    lines.append("Order:")
    current_version: str | None = None
    for index, script in enumerate(scripts, start=1):
        if script.version_folder != current_version:
            current_version = script.version_folder
            lines.append(f"  {current_version}")
        rel = script.path.relative_to(project_root).as_posix()
        lines.append(f"    {index:>3}  {rel}")
    return lines


def print_compile_plan(
    scripts: list[MigrationScript],
    **kwargs,
) -> None:
    print("\n".join(compile_plan_lines(scripts, **kwargs)))


def check_scripts(
    runner: SqlCmdRunner,
    scripts: list[MigrationScript],
    *,
    database: str,
    mode: str,
    project_root: Path,
    stop_on_error: bool,
    phase: str,
) -> int:
    failed = 0
    for index, script in enumerate(scripts, start=1):
        rel = script.path.relative_to(project_root)
        print(f"-> [{index}/{len(scripts)}] {rel}")
        result = compile_script(runner, script, database=database, mode=mode)
        if script_has_sql_error(result.stdout or "", result.stderr or "", result.returncode):
            failed += 1
            print(f"ERROR: {rel}\n{format_failure(result)}", file=sys.stderr)
            if stop_on_error:
                print(
                    f"{phase}: {index} of {len(scripts)} migration script(s) failed.",
                    file=sys.stderr,
                )
                return 1
        else:
            print("   ok")

    if failed:
        print(
            f"{phase}: {len(scripts)} migration script(s), {failed} failed.",
            file=sys.stderr,
        )
        return 1

    print(f"{phase}: {len(scripts)} migration script(s), no issues.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse and compile Deployments/Migrations scripts with sqlcmd, in "
            "deploy order (version folder, then filename). Both passes run "
            "unless --skip-parse or --skip-compile stops one."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Script order\n"
            "  Version folders run from lowest MAJOR.MINOR.PATCH to highest.\n"
            "  Inside a folder, files run in filename order (01_, 02_, ...).\n"
            "  This is the same order as sql-mig run.\n"
            "\n"
            "Passes (both run unless you stop one)\n"
            "  parse         SET PARSEONLY ON for every file. Reports every syntax error.\n"
            "  compile       Execute the chain on a new database, then drop it.\n"
            "                A later script sees objects from earlier scripts.\n"
            "                This pass proves foreign keys and procedures.\n"
            "  --skip-parse  Stop the parse pass.\n"
            "  --skip-compile\n"
            "                Stop the compile pass.\n"
            "  --noexec      Compile stays on, but each file uses SET NOEXEC ON\n"
            "                against -d and changes nothing. Parse still runs.\n"
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Database project root (default: current directory)",
    )
    parser.add_argument("--sqlcmd", default="sqlcmd", help="Path to sqlcmd (default: sqlcmd)")
    parser.add_argument("--server", "-S", help="SQL Server instance name or host")
    parser.add_argument(
        "--database",
        "-d",
        help="Existing database for --noexec, or for a parse-only run",
    )
    parser.add_argument("--user", "-U", help="SQL login user name")
    parser.add_argument("--password", "-P", help="SQL login password")
    parser.add_argument(
        "--trusted",
        "-E",
        action="store_true",
        help="Use trusted (integrated) authentication",
    )
    parser.add_argument(
        "-C",
        "--trust-server-cert",
        action="store_true",
        help="Trust server certificate (sqlcmd -C)",
    )
    parser.add_argument(
        "--sqlcmd-arg",
        action="append",
        default=[],
        dest="sqlcmd_args",
        metavar="ARG",
        help="Extra argument passed to sqlcmd (repeatable)",
    )
    parser.add_argument(
        "--exclude-version",
        action="append",
        default=[],
        dest="exclude_versions",
        metavar="MAJOR.MINOR.PATCH",
        help="Skip a semver migration folder (repeatable)",
    )
    parser.add_argument(
        "--up-to-version",
        metavar="MAJOR.MINOR.PATCH",
        help="Only check migrations up to and including this version",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Stop the parse pass. Compile still runs",
    )
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help="Stop the compile pass. Parse still runs",
    )
    parser.add_argument(
        "--noexec",
        action="store_true",
        help=(
            "Keep compile on, but use SET NOEXEC ON against -d instead of "
            "executing the chain. Parse still runs"
        ),
    )
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Stop the compile pass. Same as --skip-compile",
    )
    return parser.parse_args()


def selected_passes(args: argparse.Namespace) -> tuple[bool, bool, str, str | None]:
    """Return do_parse, do_compile, compile_mode, and how compile was stopped."""
    compile_stopped_by: str | None = None
    if args.parse_only:
        compile_stopped_by = "--parse-only"
    elif args.skip_compile:
        compile_stopped_by = "--skip-compile"
    do_parse = not args.skip_parse
    do_compile = compile_stopped_by is None
    compile_mode = "noexec" if args.noexec else "execute"
    return do_parse, do_compile, compile_mode, compile_stopped_by


def validate_args(args: argparse.Namespace) -> int | None:
    do_parse, do_compile, compile_mode, _stopped_by = selected_passes(args)
    if not args.server:
        print("ERROR: --server/-S is required.", file=sys.stderr)
        return 2
    if not args.trusted and not args.user:
        print(
            "ERROR: Provide --trusted/-E or --user/-U (and password).",
            file=sys.stderr,
        )
        return 2
    if args.noexec and not do_compile:
        print(
            "ERROR: --noexec keeps the compile pass on. "
            "Drop --skip-compile / --parse-only, or drop --noexec.",
            file=sys.stderr,
        )
        return 2
    if not do_parse and not do_compile:
        print(
            "ERROR: Parse and compile are both stopped. "
            "Leave at least one of them running.",
            file=sys.stderr,
        )
        return 2
    if do_compile and compile_mode == "noexec" and not args.database:
        print("ERROR: --database/-d is required with --noexec.", file=sys.stderr)
        return 2
    if do_compile and compile_mode == "execute" and args.database:
        print(
            "ERROR: The compile pass creates and drops its own database. "
            "Omit --database/-d, or pass --noexec to compile against an existing database.",
            file=sys.stderr,
        )
        return 2
    return None


def main() -> int:
    args = parse_args()
    usage_error = validate_args(args)
    if usage_error is not None:
        return usage_error

    migrations_root = args.project_root / "Deployments" / "Migrations"
    try:
        scripts = discover_migration_scripts(
            migrations_root,
            exclude_versions=set(args.exclude_versions),
            up_to_version=args.up_to_version,
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if not scripts:
        print("No migration scripts to check.")
        return 0

    if shutil.which(args.sqlcmd) is None:
        print(
            f"ERROR: sqlcmd not found ({args.sqlcmd!r}). "
            "Install SQL Server command-line tools (mssql-tools).",
            file=sys.stderr,
        )
        return 1

    do_parse, do_compile, compile_mode, compile_stopped_by = selected_passes(args)
    parse_database = args.database or "master"
    compile_database = args.database
    scratch: str | None = None
    if do_compile and compile_mode == "execute":
        scratch = scratch_database_name()
        compile_database = scratch

    runner = SqlCmdRunner(
        sqlcmd=args.sqlcmd,
        server=args.server,
        database=args.database or "master",
        user=args.user,
        password=args.password,
        trusted=args.trusted,
        trust_server_cert=args.trust_server_cert,
        extra_args=args.sqlcmd_args,
    )

    print_compile_plan(
        scripts,
        project_root=args.project_root,
        do_parse=do_parse,
        do_compile=do_compile,
        compile_mode=compile_mode,
        parse_database=parse_database if do_parse else None,
        compile_database=compile_database if do_compile else None,
        parse_stopped_by="--skip-parse" if args.skip_parse else None,
        compile_stopped_by=compile_stopped_by,
    )
    print()

    exit_code = 0
    if do_parse:
        print(f"Parse: SET PARSEONLY ON against {parse_database}.")
        print()
        if check_scripts(
            runner,
            scripts,
            database=parse_database,
            mode="parse",
            project_root=args.project_root,
            stop_on_error=False,
            phase="Parse",
        ) != 0:
            exit_code = 1
        if do_compile:
            print()

    if not do_compile:
        return exit_code

    if compile_mode == "noexec":
        print(f"Compile: SET NOEXEC ON against {compile_database}.")
        print()
        if check_scripts(
            runner,
            scripts,
            database=compile_database or "",
            mode="noexec",
            project_root=args.project_root,
            stop_on_error=False,
            phase="Compile",
        ) != 0:
            exit_code = 1
        return exit_code

    print(f"Compile: execute on scratch database {scratch}.")
    print()
    try:
        create_scratch_database(runner, scratch or "")
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    try:
        if check_scripts(
            runner,
            scripts,
            database=scratch or "",
            mode="execute",
            project_root=args.project_root,
            stop_on_error=True,
            phase="Compile",
        ) != 0:
            exit_code = 1
    finally:
        if scratch and drop_scratch_database(runner, scratch):
            print(f"Dropped scratch database {scratch}.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
