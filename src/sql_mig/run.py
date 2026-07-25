#!/usr/bin/env python3
"""
Apply pre-deployment and migration scripts to a SQL Server database.

Pre-deployment scripts (Deployments/pre-deployments/**/*.sql) run first when
--pre-deployments is set. They are tracked in dbo.__PreDeploymentHistory by
relative path. Optional header -- SqlCmd-Database: master runs a script against
another database (history is recorded in that database).

Migrations (Deployments/Migrations/) are tracked in dbo.__MigrationHistory by
-- Migration-Id headers.

Requires sqlcmd (SQL Server command-line tools / mssql-tools).

Examples:
  sql-mig run -S localhost -d MyDb -U sa -P 'secret' -C --pre-deployments
  sql-mig run -S localhost -d MyDb -U sa -P 'secret' -C --status
  sql-mig run --list-files
  sql-mig run --list-pre-deployments
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sql_mig.migration_id import (
    SEMVER_FOLDER_PATTERN,
    extract_migration_id,
    parse_version_from_path,
)

SQLCMD_DATABASE_HEADER = re.compile(
    r"^--\s*SqlCmd-Database:\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

CREATE_MIGRATION_HISTORY_TABLE_SQL = """
SET NOCOUNT ON;
IF OBJECT_ID(N'[dbo].[__MigrationHistory]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[__MigrationHistory] (
        [MigrationId]     NVARCHAR(64)  NOT NULL,
        [VersionFolder]   NVARCHAR(20)  NOT NULL,
        [ScriptName]      NVARCHAR(260) NOT NULL,
        [AppliedUtc]      DATETIME2(7)  NOT NULL
            CONSTRAINT [DF___MigrationHistory_AppliedUtc] DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT [PK___MigrationHistory] PRIMARY KEY CLUSTERED ([MigrationId] ASC)
    );
END;
""".strip()

CREATE_PRE_DEPLOYMENT_HISTORY_TABLE_SQL = """
SET NOCOUNT ON;
IF OBJECT_ID(N'[dbo].[__PreDeploymentHistory]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[__PreDeploymentHistory] (
        [ScriptPath]  NVARCHAR(500) NOT NULL,
        [AppliedUtc]  DATETIME2(7)  NOT NULL
            CONSTRAINT [DF___PreDeploymentHistory_AppliedUtc] DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT [PK___PreDeploymentHistory] PRIMARY KEY CLUSTERED ([ScriptPath] ASC)
    );
END;
""".strip()


@dataclass(frozen=True)
class MigrationScript:
    path: Path
    migration_id: str
    version_folder: str
    script_name: str


@dataclass(frozen=True)
class PreDeploymentScript:
    path: Path
    script_path: str
    target_database: str | None


def parse_semver_folder(name: str) -> tuple[int, int, int] | None:
    match = SEMVER_FOLDER_PATTERN.fullmatch(name)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def normalize_script_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def extract_sqlcmd_database(content: str) -> str | None:
    match = SQLCMD_DATABASE_HEADER.search(content)
    return match.group(1) if match else None


def discover_pre_deployment_scripts(pre_deployments_root: Path) -> list[PreDeploymentScript]:
    if not pre_deployments_root.is_dir():
        return []

    scripts: list[PreDeploymentScript] = []
    for sql_file in sorted(pre_deployments_root.rglob("*.sql")):
        content = sql_file.read_text(encoding="utf-8")
        scripts.append(
            PreDeploymentScript(
                path=sql_file,
                script_path=normalize_script_path(sql_file, pre_deployments_root),
                target_database=extract_sqlcmd_database(content),
            )
        )
    return scripts


def discover_migration_scripts(
    migrations_root: Path,
    *,
    exclude_versions: set[str],
    up_to_version: str | None,
) -> list[MigrationScript]:
    if not migrations_root.is_dir():
        raise FileNotFoundError(f"Migrations folder not found: {migrations_root}")

    up_to_tuple: tuple[int, int, int] | None = None
    if up_to_version is not None:
        up_to_tuple = parse_semver_folder(up_to_version)
        if up_to_tuple is None:
            raise ValueError(
                f"--up-to-version must be MAJOR.MINOR.PATCH (got {up_to_version!r})"
            )

    version_dirs: list[Path] = []
    invalid_dirs: list[str] = []

    for entry in migrations_root.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if parse_semver_folder(entry.name) is None:
            invalid_dirs.append(entry.name)
            continue
        if entry.name in exclude_versions:
            continue
        if up_to_tuple is not None:
            folder_tuple = parse_semver_folder(entry.name)
            if folder_tuple is not None and folder_tuple > up_to_tuple:
                continue
        version_dirs.append(entry)

    if invalid_dirs:
        names = ", ".join(sorted(invalid_dirs))
        raise ValueError(
            "Migration folders must use semantic versioning (MAJOR.MINOR.PATCH). "
            f"Invalid folder(s): {names}"
        )

    version_dirs.sort(key=lambda path: parse_semver_folder(path.name) or (0, 0, 0))

    scripts: list[MigrationScript] = []
    seen_ids: dict[str, Path] = {}

    for version_dir in version_dirs:
        for sql_file in sorted(version_dir.glob("*.sql")):
            content = sql_file.read_text(encoding="utf-8")
            migration_id = extract_migration_id(content)
            if not migration_id:
                raise ValueError(
                    f"Missing -- Migration-Id header: {sql_file}. "
                    "Run `sql-mig sync` or `sql-mig stamp --all` to stamp it."
                )

            if migration_id in seen_ids:
                raise ValueError(
                    f"Duplicate Migration-Id {migration_id!r} in "
                    f"{sql_file} and {seen_ids[migration_id]}"
                )
            seen_ids[migration_id] = sql_file

            version_folder = parse_version_from_path(sql_file)
            if version_folder is None:
                raise ValueError(f"Could not parse version folder for {sql_file}")

            scripts.append(
                MigrationScript(
                    path=sql_file,
                    migration_id=migration_id,
                    version_folder=version_folder,
                    script_name=sql_file.name,
                )
            )

    return scripts


def escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


class SqlCmdRunner:
    def __init__(
        self,
        *,
        sqlcmd: str,
        server: str | None,
        database: str,
        user: str | None,
        password: str | None,
        trusted: bool,
        trust_server_cert: bool,
        extra_args: list[str],
    ) -> None:
        self.sqlcmd = sqlcmd
        self.server = server
        self.default_database = database
        self.user = user
        self.password = password
        self.trusted = trusted
        self.trust_server_cert = trust_server_cert
        self.extra_args = extra_args

    def base_cmd(self, database: str | None = None) -> list[str]:
        cmd = [self.sqlcmd]
        if self.server:
            cmd.extend(["-S", self.server])
        cmd.extend(["-d", database or self.default_database])
        if self.trusted:
            cmd.append("-E")
        else:
            if not self.user:
                raise ValueError("Provide --user/-U and --password/-P, or use --trusted/-E")
            cmd.extend(["-U", self.user])
            if self.password is not None:
                cmd.extend(["-P", self.password])
        if self.trust_server_cert:
            cmd.append("-C")
        cmd.extend(self.extra_args)
        return cmd

    def run_query(self, sql: str, *, database: str | None = None) -> subprocess.CompletedProcess[str]:
        cmd = self.base_cmd(database) + ["-b", "-Q", sql]
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def run_file(self, sql_file: Path, *, database: str | None = None) -> subprocess.CompletedProcess[str]:
        cmd = self.base_cmd(database) + ["-b", "-i", str(sql_file)]
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def ensure_migration_history_table(self, database: str | None = None) -> None:
        result = self.run_query(CREATE_MIGRATION_HISTORY_TABLE_SQL, database=database)
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to create dbo.__MigrationHistory:\n"
                + (result.stderr or result.stdout or "").strip()
            )

    def ensure_pre_deployment_history_table(self, database: str | None = None) -> None:
        result = self.run_query(CREATE_PRE_DEPLOYMENT_HISTORY_TABLE_SQL, database=database)
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to create dbo.__PreDeploymentHistory:\n"
                + (result.stderr or result.stdout or "").strip()
            )

    def fetch_applied_migration_ids(self, database: str | None = None) -> set[str]:
        self.ensure_migration_history_table(database)
        sql = "SET NOCOUNT ON; SELECT [MigrationId] FROM [dbo].[__MigrationHistory];"
        result = self.run_query(sql, database=database)
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to read dbo.__MigrationHistory:\n"
                + (result.stderr or result.stdout or "").strip()
            )

        applied: set[str] = set()
        for line in result.stdout.splitlines():
            value = line.strip()
            if not value or value.startswith("-") or value.lower() == "migrationid":
                continue
            applied.add(value)
        return applied

    def fetch_applied_pre_deployment_paths(self, database: str | None = None) -> set[str]:
        self.ensure_pre_deployment_history_table(database)
        sql = "SET NOCOUNT ON; SELECT [ScriptPath] FROM [dbo].[__PreDeploymentHistory];"
        result = self.run_query(sql, database=database)
        if result.returncode != 0:
            raise RuntimeError(
                "Failed to read dbo.__PreDeploymentHistory:\n"
                + (result.stderr or result.stdout or "").strip()
            )

        applied: set[str] = set()
        for line in result.stdout.splitlines():
            value = line.strip()
            if not value or value.startswith("-") or value.lower() == "scriptpath":
                continue
            applied.add(value)
        return applied

    def record_migration(self, script: MigrationScript) -> None:
        sql = (
            "SET NOCOUNT ON; "
            "INSERT INTO [dbo].[__MigrationHistory] "
            "([MigrationId], [VersionFolder], [ScriptName]) VALUES ("
            f"N'{escape_sql_literal(script.migration_id)}', "
            f"N'{escape_sql_literal(script.version_folder)}', "
            f"N'{escape_sql_literal(script.script_name)}'"
            ");"
        )
        result = self.run_query(sql)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to record migration {script.migration_id}:\n"
                + (result.stderr or result.stdout or "").strip()
            )

    def record_pre_deployment(self, script: PreDeploymentScript) -> None:
        database = script.target_database or self.default_database
        sql = (
            "SET NOCOUNT ON; "
            "INSERT INTO [dbo].[__PreDeploymentHistory] ([ScriptPath]) VALUES ("
            f"N'{escape_sql_literal(script.script_path)}'"
            ");"
        )
        result = self.run_query(sql, database=database)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to record pre-deployment {script.script_path}:\n"
                + (result.stderr or result.stdout or "").strip()
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply pre-deployment and migration SQL scripts with dbo history tracking."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Database project root (default: current directory)",
    )
    parser.add_argument(
        "--sqlcmd",
        default="sqlcmd",
        help="Path to sqlcmd executable (default: sqlcmd on PATH)",
    )
    parser.add_argument("--server", "-S", help="SQL Server instance name or host")
    parser.add_argument("--database", "-d", help="Target database name")
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
        "--pre-deployments",
        action="store_true",
        help="Run pending scripts from Deployments/pre-deployments/ before migrations",
    )
    parser.add_argument(
        "--pre-deployments-only",
        action="store_true",
        help="Run only pre-deployment scripts (skip migrations)",
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
        help="Only consider migrations up to and including this version",
    )
    parser.add_argument(
        "--list-files",
        action="store_true",
        help="List migration files in deploy order (no database connection)",
    )
    parser.add_argument(
        "--list-pre-deployments",
        action="store_true",
        help="List pre-deployment files in deploy order (no database connection)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show applied and pending scripts, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show pending scripts without executing them",
    )
    return parser.parse_args()


def validate_connection_args(args: argparse.Namespace) -> None:
    if not args.database:
        print("ERROR: --database/-d is required.", file=sys.stderr)
        raise SystemExit(2)
    if not args.server:
        print("ERROR: --server/-S is required.", file=sys.stderr)
        raise SystemExit(2)
    if not args.trusted and not args.user:
        print(
            "ERROR: Provide --trusted/-E or --user/-U (and password).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def resolve_target_database(script: PreDeploymentScript, default_database: str) -> str:
    return script.target_database or default_database


def collect_pending_pre_deployments(
    runner: SqlCmdRunner,
    all_pre_scripts: list[PreDeploymentScript],
    default_database: str,
) -> list[PreDeploymentScript]:
    pending: list[PreDeploymentScript] = []
    cache: dict[str, set[str]] = {}

    for script in all_pre_scripts:
        database = resolve_target_database(script, default_database)
        if database not in cache:
            cache[database] = runner.fetch_applied_pre_deployment_paths(database)
        if script.script_path not in cache[database]:
            pending.append(script)
    return pending


def print_pre_deployment_status(
    all_scripts: list[PreDeploymentScript],
    runner: SqlCmdRunner,
    default_database: str,
    project_root: Path,
) -> None:
    cache: dict[str, set[str]] = {}
    applied_count = 0
    pending_count = 0

    print("Pre-deployments:")
    for script in all_scripts:
        database = resolve_target_database(script, default_database)
        if database not in cache:
            cache[database] = runner.fetch_applied_pre_deployment_paths(database)
        rel = script.path.relative_to(project_root)
        target = f" db={database}" if database != default_database else ""
        if script.script_path in cache[database]:
            applied_count += 1
            print(f"  [applied] {rel}{target}")
        else:
            pending_count += 1
            print(f"  [pending] {rel}{target}")

    print(f"Pre-deployment summary: {applied_count} applied, {pending_count} pending")


def print_migration_status(
    all_scripts: list[MigrationScript],
    applied_ids: set[str],
    project_root: Path,
) -> None:
    applied_scripts = [script for script in all_scripts if script.migration_id in applied_ids]
    pending_scripts = [script for script in all_scripts if script.migration_id not in applied_ids]

    print("Migrations:")
    for script in applied_scripts:
        rel = script.path.relative_to(project_root)
        print(f"  [applied] {rel}  ({script.migration_id})")
    for script in pending_scripts:
        rel = script.path.relative_to(project_root)
        print(f"  [pending] {rel}  ({script.migration_id})")

    print(
        f"Migration summary: {len(applied_scripts)} applied, {len(pending_scripts)} pending"
    )


def apply_pre_deployments(
    runner: SqlCmdRunner,
    pending_scripts: list[PreDeploymentScript],
    default_database: str,
    project_root: Path,
) -> int:
    if not pending_scripts:
        print("No pending pre-deployment scripts.")
        return 0

    print(f"Applying {len(pending_scripts)} pending pre-deployment script(s)...")
    for script in pending_scripts:
        database = resolve_target_database(script, default_database)
        rel = script.path.relative_to(project_root)
        target = f" (database={database})" if database != default_database else ""
        print(f"-> {rel}{target}")
        runner.ensure_pre_deployment_history_table(database)
        result = runner.run_file(script.path, database=database)
        if result.returncode != 0:
            print(
                f"ERROR: Pre-deployment script failed: {rel}\n"
                + (result.stderr or result.stdout or "").strip(),
                file=sys.stderr,
            )
            return 1
        try:
            runner.record_pre_deployment(script)
        except RuntimeError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(f"   recorded {script.script_path}")

    print("All pending pre-deployment scripts applied.")
    return 0


def apply_migrations(
    runner: SqlCmdRunner,
    pending_scripts: list[MigrationScript],
    project_root: Path,
) -> int:
    if not pending_scripts:
        print("No pending migrations.")
        return 0

    print(f"Applying {len(pending_scripts)} pending migration(s)...")
    for script in pending_scripts:
        rel = script.path.relative_to(project_root)
        print(f"-> {rel}")
        result = runner.run_file(script.path)
        if result.returncode != 0:
            print(
                f"ERROR: Migration failed: {rel}\n"
                + (result.stderr or result.stdout or "").strip(),
                file=sys.stderr,
            )
            return 1
        try:
            runner.record_migration(script)
        except RuntimeError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(f"   recorded {script.migration_id}")

    print("All pending migrations applied.")
    return 0


def main() -> int:
    args = parse_args()

    if args.pre_deployments and args.pre_deployments_only:
        print(
            "ERROR: Use either --pre-deployments or --pre-deployments-only, not both.",
            file=sys.stderr,
        )
        return 1

    pre_deployments_root = args.project_root / "Deployments" / "pre-deployments"
    migrations_root = args.project_root / "Deployments" / "Migrations"

    all_pre_scripts = discover_pre_deployment_scripts(pre_deployments_root)
    try:
        all_migration_scripts = discover_migration_scripts(
            migrations_root,
            exclude_versions=set(args.exclude_versions),
            up_to_version=args.up_to_version,
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.list_pre_deployments:
        for script in all_pre_scripts:
            rel = script.path.relative_to(args.project_root)
            target = script.target_database or args.database or "<default-db>"
            print(f"{rel}\t{script.script_path}\t{target}")
        return 0

    if args.list_files:
        for script in all_migration_scripts:
            rel = script.path.relative_to(args.project_root)
            print(f"{rel}\t{script.migration_id}")
        return 0

    if shutil.which(args.sqlcmd) is None:
        print(
            f"ERROR: sqlcmd not found ({args.sqlcmd!r}). "
            "Install SQL Server command-line tools (mssql-tools).",
            file=sys.stderr,
        )
        return 1

    validate_connection_args(args)

    runner = SqlCmdRunner(
        sqlcmd=args.sqlcmd,
        server=args.server,
        database=args.database,
        user=args.user,
        password=args.password,
        trusted=args.trusted,
        trust_server_cert=args.trust_server_cert,
        extra_args=args.sqlcmd_args,
    )

    run_pre = args.pre_deployments or args.pre_deployments_only
    run_migrations_flag = not args.pre_deployments_only

    try:
        pending_pre = (
            collect_pending_pre_deployments(runner, all_pre_scripts, args.database)
            if run_pre
            else []
        )
        applied_migration_ids = (
            runner.fetch_applied_migration_ids() if run_migrations_flag or args.status else set()
        )
        pending_migrations = (
            [
                script
                for script in all_migration_scripts
                if script.migration_id not in applied_migration_ids
            ]
            if run_migrations_flag
            else []
        )
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.status:
        if all_pre_scripts:
            print_pre_deployment_status(
                all_pre_scripts, runner, args.database, args.project_root
            )
        else:
            print("Pre-deployments: none found")
        print_migration_status(all_migration_scripts, applied_migration_ids, args.project_root)
        return 0

    if args.dry_run:
        if run_pre:
            print(f"Would apply {len(pending_pre)} pre-deployment script(s):")
            for script in pending_pre:
                rel = script.path.relative_to(args.project_root)
                database = resolve_target_database(script, args.database)
                target = f" (database={database})" if database != args.database else ""
                print(f"  {rel}{target}")
        if run_migrations_flag:
            print(f"Would apply {len(pending_migrations)} migration(s):")
            for script in pending_migrations:
                rel = script.path.relative_to(args.project_root)
                print(f"  {rel}  ({script.migration_id})")
        if not pending_pre and not pending_migrations:
            print("Nothing pending.")
        return 0

    if run_pre:
        result = apply_pre_deployments(
            runner, pending_pre, args.database, args.project_root
        )
        if result != 0:
            return result

    if run_migrations_flag:
        return apply_migrations(runner, pending_migrations, args.project_root)

    print("Nothing to apply. Use --pre-deployments and/or migrations (default).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
