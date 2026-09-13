#!/usr/bin/env python3
"""
Bootstrap Deployments/Migrations/ from a baseline SQL script exported from an existing database.

Usage:
  sql-mig bootstrap --input baseline.sql --version 1.0.0
  sql-mig bootstrap --input baseline.sql --version 1.0.0 --sync
  sql-mig bootstrap --input baseline.sql --version 1.0.0 --dry-run

Expects schema-only SQL (SSMS Generate Scripts, SqlPackage script, mssql-scripter, etc.).
Splits objects into migration files, wraps idempotent guards, stamps Migration-Ids,
and optionally runs schema sync.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sql_mig.migration_id import (
    SEMVER_FOLDER_PATTERN,
    build_migration_header,
    generate_migration_id,
    next_sequence_number,
    slugify_name,
)


@dataclass(frozen=True)
class MigrationChunk:
    kind: str
    sort_key: tuple
    name_slug: str
    sql: str


@dataclass(frozen=True)
class ForeignKeyAlter:
    constraint_name: str
    from_table: tuple[str, str]
    referenced_table: tuple[str, str]
    alter_sql: str
    name_slug: str


FK_REFERENCES_PATTERN = re.compile(
    r"\bREFERENCES\s+(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))\s*\(",
    re.IGNORECASE,
)

CONSTRAINT_NAME_PATTERN = re.compile(
    r"\bCONSTRAINT\s+(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

COLUMN_NAME_PATTERN = re.compile(
    r"^(\[(?:[^\]]+)\]|(?:\w+))\s+",
    re.IGNORECASE,
)


KIND_ORDER = {
    "schema": 10,
    "type": 20,
    "sequence": 25,
    "assembly": 26,
    "table": 30,
    "alter_table": 40,
    "index": 50,
    "statistics": 60,
    "routine": 70,
    "synonym": 80,
    "role": 90,
    "role_membership": 95,
    "unknown": 99,
}

NOISE_LINE_PATTERN = re.compile(
    r"^\s*(?:"
    r"USE\s+\["
    r"|SET\s+(?:ANSI_NULLS|QUOTED_IDENTIFIER|NUMERIC_ROUNDABORT|ANSI_PADDING|"
    r"ANSI_WARNINGS|CONCAT_NULL_YIELDS_NULL|ARITHABORT|XACT_ABORT)\s+(?:ON|OFF)"
    r")\b",
    re.IGNORECASE,
)

BLOCK_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)


def load_sync_module():
    from sql_mig import sync as sync_module
    return sync_module


def normalize_baseline_sql(content: str) -> str:
    content = BLOCK_COMMENT_PATTERN.sub("", content)
    kept_lines: list[str] = []
    for line in content.splitlines():
        if NOISE_LINE_PATTERN.match(line):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip()


def escape_for_exec_n(sql: str) -> str:
    return sql.replace("'", "''")


def indent_sql(sql: str, spaces: int = 4) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else line for line in sql.splitlines())


def to_create_or_alter(sql: str) -> str:
    if re.search(r"\bCREATE\s+OR\s+ALTER\s+", sql, re.IGNORECASE):
        return sql.strip()
    return re.sub(
        r"\bCREATE\s+",
        "CREATE OR ALTER ",
        sql.strip(),
        count=1,
        flags=re.IGNORECASE,
    )


def wrap_schema(sync, sql: str) -> str:
    match = sync.CREATE_SCHEMA_PATTERN.search(sql)
    if not match:
        return sql.strip()
    schema = sync.strip_brackets(match.group(1))
    statement = sql.strip().rstrip(";") + ";"
    return (
        f"IF SCHEMA_ID(N'{schema}') IS NULL\n"
        f"BEGIN\n"
        f"    EXEC(N'{escape_for_exec_n(statement)}');\n"
        f"END;"
    )


def wrap_table(sync, sql: str) -> str:
    parsed = sync.parse_create_table(sql)
    if not parsed:
        return sql.strip()
    schema, table, _ = parsed
    statement = sql.strip()
    if not statement.endswith(";"):
        statement += ";"
    return (
        f"IF OBJECT_ID(N'[{schema}].[{table}]', N'U') IS NULL\n"
        f"BEGIN\n"
        f"{indent_sql(statement)}\n"
        f"END;"
    )


def wrap_synonym(sync, sql: str) -> str:
    match = sync.CREATE_PATTERN.search(sql)
    if not match or match.group(1).upper() != "SYNONYM":
        return sql.strip()
    schema = sync.strip_brackets(match.group(2))
    name = sync.strip_brackets(match.group(3))
    statement = sql.strip()
    if not statement.endswith(";"):
        statement += ";"
    return (
        f"IF OBJECT_ID(N'[{schema}].[{name}]', N'SN') IS NULL\n"
        f"BEGIN\n"
        f"{indent_sql(statement)}\n"
        f"END;"
    )


def wrap_role(sync, sql: str) -> str:
    match = sync.CREATE_ROLE_PATTERN.search(sql)
    if not match:
        return sql.strip()
    role = sync.strip_brackets(match.group(1))
    statement = sql.strip()
    if not statement.endswith(";"):
        statement += ";"
    return (
        f"IF DATABASE_PRINCIPAL_ID(N'{role}') IS NULL\n"
        f"BEGIN\n"
        f"{indent_sql(statement)}\n"
        f"END;"
    )


def wrap_sequence(sync, sql: str) -> str:
    match = sync.CREATE_SEQUENCE_PATTERN.search(sql)
    if not match:
        return sql.strip()
    schema = sync.strip_brackets(match.group(1))
    sequence = sync.strip_brackets(match.group(2))
    statement = sql.strip()
    if not statement.endswith(";"):
        statement += ";"
    return (
        f"IF OBJECT_ID(N'[{schema}].[{sequence}]', N'SO') IS NULL\n"
        f"BEGIN\n"
        f"{indent_sql(statement)}\n"
        f"END;"
    )


def wrap_alter_table(sync, schema: str, table: str, action: str, rest: str) -> str:
    schema_name = sync.strip_brackets(schema)
    table_name = sync.strip_brackets(table)
    return (
        f"ALTER TABLE [{schema_name}].[{table_name}] "
        f"{action.upper()} {rest.strip().rstrip(';')};"
    )


def schema_slug(schema: str) -> str:
    return schema.lower()


def object_slug(schema: str, name: str) -> str:
    return f"{schema.lower()}.{name}"


def table_identity(schema: str, table: str) -> tuple[str, str]:
    return schema.lower(), table.lower()


def extract_fk_reference(sql: str) -> tuple[str, str] | None:
    match = FK_REFERENCES_PATTERN.search(sql)
    if not match:
        return None
    return match.group(1).strip("[]"), match.group(2).strip("[]")


def extract_constraint_name(sql: str) -> str | None:
    match = CONSTRAINT_NAME_PATTERN.search(sql)
    if not match:
        return None
    return match.group(1).strip("[]")


def split_create_table_fks(sync, statement: str) -> tuple[str, list[ForeignKeyAlter]]:
    parsed = sync.parse_create_table(statement)
    if not parsed:
        return statement, []

    schema, table, body = parsed
    kept_columns: list[str] = []
    kept_constraints: list[str] = []
    fk_alters: list[ForeignKeyAlter] = []

    for item in sync.split_table_body(body):
        item_name, definition, is_constraint = sync.parse_table_item(item)

        if is_constraint:
            if "FOREIGN KEY" in definition.upper():
                reference = extract_fk_reference(definition)
                if not reference:
                    kept_constraints.append(definition)
                    continue
                ref_schema, ref_table = reference
                constraint_name = extract_constraint_name(definition) or item_name
                fk_alters.append(
                    ForeignKeyAlter(
                        constraint_name=constraint_name,
                        from_table=table_identity(schema, table),
                        referenced_table=table_identity(ref_schema, ref_table),
                        alter_sql=wrap_alter_table(
                            sync,
                            schema,
                            table,
                            "ADD",
                            definition.strip().rstrip(",").rstrip(";"),
                        ),
                        name_slug=(
                            f"{object_slug(schema, table)}."
                            f"add_{constraint_name}"
                        ),
                    )
                )
            else:
                kept_constraints.append(definition)
            continue

        if re.search(r"\bREFERENCES\b", definition, re.IGNORECASE):
            column_match = COLUMN_NAME_PATTERN.match(definition)
            if not column_match:
                kept_columns.append(definition)
                continue
            column_name = column_match.group(1).strip("[]")
            parts = re.split(
                r"\s+REFERENCES\b",
                definition,
                maxsplit=1,
                flags=re.IGNORECASE,
            )
            if len(parts) != 2:
                kept_columns.append(definition)
                continue
            column_prefix, reference_clause = parts
            reference = extract_fk_reference(f"REFERENCES {reference_clause}")
            if not reference:
                kept_columns.append(definition)
                continue
            ref_schema, ref_table = reference
            constraint_name = f"FK_{table}_{column_name}"
            constraint_sql = (
                f"CONSTRAINT [{constraint_name}] FOREIGN KEY ([{column_name}]) "
                f"REFERENCES {reference_clause.strip().rstrip(';').rstrip(',')}"
            )
            fk_alters.append(
                ForeignKeyAlter(
                    constraint_name=constraint_name,
                    from_table=table_identity(schema, table),
                    referenced_table=table_identity(ref_schema, ref_table),
                    alter_sql=wrap_alter_table(
                        sync,
                        schema,
                        table,
                        "ADD",
                        constraint_sql,
                    ),
                    name_slug=f"{object_slug(schema, table)}.add_{constraint_name}",
                )
            )
            kept_columns.append(column_prefix.strip().rstrip(","))
            continue

        kept_columns.append(definition)

    if not fk_alters:
        return statement, []

    all_items = kept_columns + kept_constraints
    if not all_items:
        return statement, fk_alters

    body_sql = ",\n    ".join(all_items)
    create_sql = f"CREATE TABLE [{schema}].[{table}] (\n    {body_sql}\n);"
    return create_sql, fk_alters


def order_table_chunks(
    table_chunks: list[MigrationChunk],
    dependencies: list[tuple[tuple[str, str], tuple[str, str]]],
) -> tuple[list[MigrationChunk], bool]:
    chunk_by_key: dict[tuple[str, str], MigrationChunk] = {}
    for chunk in table_chunks:
        if len(chunk.sort_key) < 3:
            continue
        key = (chunk.sort_key[1], chunk.sort_key[2])
        chunk_by_key[key] = chunk

    if not chunk_by_key:
        return table_chunks, False

    keys = list(chunk_by_key.keys())
    in_degree = {key: 0 for key in keys}
    graph: dict[tuple[str, str], list[tuple[str, str]]] = {key: [] for key in keys}

    for dependent, referenced in dependencies:
        if dependent not in chunk_by_key or referenced not in chunk_by_key:
            continue
        if dependent == referenced:
            continue
        in_degree[dependent] += 1
        graph[referenced].append(dependent)

    ready = sorted(key for key in keys if in_degree[key] == 0)
    ordered_keys: list[tuple[str, str]] = []

    while ready:
        current = ready.pop(0)
        ordered_keys.append(current)
        for dependent in sorted(graph[current]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
                ready.sort()

    if len(ordered_keys) != len(keys):
        return table_chunks, True

    ordered_chunks: list[MigrationChunk] = []
    for index, key in enumerate(ordered_keys):
        chunk = chunk_by_key[key]
        ordered_chunks.append(
            MigrationChunk(
                kind=chunk.kind,
                sort_key=(KIND_ORDER["table"], index, key[0], key[1]),
                name_slug=chunk.name_slug,
                sql=chunk.sql,
            )
        )
    return ordered_chunks, False


def normalize_migration_sql(sql: str) -> str:
    return sql.strip()


def parse_baseline(content: str, sync) -> list[MigrationChunk]:
    content = normalize_baseline_sql(content)
    if not content:
        return []

    chunks: list[MigrationChunk] = []
    seen_keys: set[str] = set()

    def add_chunk(kind: str, sort_key: tuple, name_slug: str, sql: str) -> None:
        normalized = normalize_migration_sql(sql)
        if not normalized:
            return
        dedupe_key = f"{kind}:{sync.sql_dedupe_key(normalized)}"
        if dedupe_key in seen_keys:
            return
        seen_keys.add(dedupe_key)
        chunks.append(
            MigrationChunk(
                kind=kind,
                sort_key=(KIND_ORDER[kind], *sort_key),
                name_slug=slugify_name(name_slug),
                sql=normalized,
            )
        )

    for statement in sync.extract_schema_statements(content):
        match = sync.CREATE_SCHEMA_PATTERN.search(statement)
        if not match:
            continue
        schema = sync.strip_brackets(match.group(1))
        add_chunk(
            "schema",
            (schema.lower(),),
            f"CS_{schema}",
            wrap_schema(sync, statement),
        )

    for statement in sync.extract_type_statements(content):
        match = sync.CREATE_TYPE_HEADER_PATTERN.search(statement)
        if not match:
            continue
        schema = sync.strip_brackets(match.group(1))
        name = sync.strip_brackets(match.group(2))
        add_chunk(
            "type",
            (schema.lower(), name.lower()),
            object_slug(schema, name),
            to_create_or_alter(statement),
        )

    for statement in sync.extract_sequence_statements(content):
        match = sync.CREATE_SEQUENCE_PATTERN.search(statement)
        if not match:
            continue
        schema = sync.strip_brackets(match.group(1))
        name = sync.strip_brackets(match.group(2))
        add_chunk(
            "sequence",
            (schema.lower(), name.lower()),
            object_slug(schema, name),
            wrap_sequence(sync, statement),
        )

    for statement in sync.extract_assembly_statements(content):
        match = sync.CREATE_ASSEMBLY_PATTERN.search(statement)
        if not match:
            continue
        name = sync.strip_brackets(match.group(1))
        add_chunk(
            "assembly",
            (name.lower(),),
            f"assembly.{name}",
            statement.strip(),
        )

    table_chunks: list[MigrationChunk] = []
    fk_dependencies: list[tuple[tuple[str, str], tuple[str, str]]] = []
    deferred_fk_alters: list[ForeignKeyAlter] = []

    for statement in sync.extract_create_table_statements(content):
        parsed = sync.parse_create_table(statement)
        if not parsed:
            continue
        schema, table, _ = parsed
        create_sql, fk_alters = split_create_table_fks(sync, statement)
        table_chunks.append(
            MigrationChunk(
                kind="table",
                sort_key=(KIND_ORDER["table"], schema.lower(), table.lower()),
                name_slug=object_slug(schema, table),
                sql=wrap_table(sync, create_sql),
            )
        )
        for fk_alter in fk_alters:
            deferred_fk_alters.append(fk_alter)
            fk_dependencies.append((fk_alter.from_table, fk_alter.referenced_table))

    ordered_tables, has_cycle = order_table_chunks(table_chunks, fk_dependencies)
    if has_cycle:
        print(
            "WARN: Circular foreign-key dependencies detected among tables; "
            "using original table order.",
            file=sys.stderr,
        )
        ordered_tables = table_chunks

    table_order_index = {
        (chunk.sort_key[2], chunk.sort_key[3]): chunk.sort_key[1]
        for chunk in ordered_tables
        if len(chunk.sort_key) >= 4
    }

    for table_chunk in ordered_tables:
        add_chunk(
            table_chunk.kind,
            table_chunk.sort_key[1:],
            table_chunk.name_slug,
            table_chunk.sql,
        )

    alter_table_index = 0
    for schema, table, action, rest in sync.extract_alter_table_statements(content):
        schema_name = sync.strip_brackets(schema)
        table_name = sync.strip_brackets(table)
        alter_sql = wrap_alter_table(sync, schema, table, action, rest)
        slug_suffix = "alter"
        rest_upper = rest.strip().upper()
        if rest_upper.startswith("CONSTRAINT"):
            constraint_match = re.match(
                r"CONSTRAINT\s+(\[(?:[^\]]+)\]|(?:\w+))",
                rest.strip(),
                re.IGNORECASE,
            )
            if constraint_match:
                slug_suffix = f"alter_{sync.strip_brackets(constraint_match.group(1))}"
        table_key = table_identity(schema_name, table_name)
        table_index = table_order_index.get(table_key, len(table_order_index))
        sub_order = 0
        if action.upper() == "ADD" and "FOREIGN KEY" in rest_upper:
            reference = extract_fk_reference(rest)
            if reference:
                fk_dependencies.append((table_key, table_identity(reference[0], reference[1])))
                sub_order = 2
        elif rest_upper.startswith("CONSTRAINT"):
            sub_order = 1
        add_chunk(
            "alter_table",
            (
                table_index,
                sub_order,
                alter_table_index,
                table_key[0],
                table_key[1],
                slug_suffix.lower(),
            ),
            f"{object_slug(schema_name, table_name)}.{slug_suffix}",
            alter_sql,
        )
        alter_table_index += 1

    for fk_alter in deferred_fk_alters:
        table_index = table_order_index.get(
            fk_alter.from_table,
            len(table_order_index),
        )
        add_chunk(
            "alter_table",
            (
                table_index,
                1,
                fk_alter.constraint_name.lower(),
                fk_alter.from_table[0],
                fk_alter.from_table[1],
            ),
            fk_alter.name_slug,
            fk_alter.alter_sql,
        )

    for statement in sync.extract_index_statements(content):
        target = sync.parse_create_index_target(statement)
        if not target:
            continue
        index_name, schema, table = target
        add_chunk(
            "index",
            (schema.lower(), table.lower(), index_name.lower()),
            f"{object_slug(schema, table)}.add_{index_name}",
            statement,
        )

    for statement in sync.extract_statistics_statements(content):
        target = sync.parse_create_statistics_target(statement)
        if not target:
            continue
        statistic_name, schema, table = target
        add_chunk(
            "statistics",
            (schema.lower(), table.lower(), statistic_name.lower()),
            f"{object_slug(schema, table)}.add_{statistic_name}",
            statement,
        )

    for statement in sync.extract_routine_statements(content):
        match = sync.CREATE_PATTERN.search(statement)
        if not match:
            continue
        schema = sync.strip_brackets(match.group(2))
        name = sync.strip_brackets(match.group(3))
        add_chunk(
            "routine",
            (schema.lower(), name.lower()),
            object_slug(schema, name),
            to_create_or_alter(statement),
        )

    for statement in sync.extract_synonym_statements(content):
        match = sync.CREATE_PATTERN.search(statement)
        if not match:
            continue
        schema = sync.strip_brackets(match.group(2))
        name = sync.strip_brackets(match.group(3))
        add_chunk(
            "synonym",
            (schema.lower(), name.lower()),
            object_slug(schema, name),
            wrap_synonym(sync, statement),
        )

    for statement in sync.extract_role_statements(content):
        match = sync.CREATE_ROLE_PATTERN.search(statement)
        if not match:
            continue
        role = sync.strip_brackets(match.group(1))
        add_chunk(
            "role",
            (role.lower(),),
            f"security.{role}",
            wrap_role(sync, statement),
        )

    for statement in sync.extract_role_membership_statements(content):
        membership = sync.parse_role_membership(statement)
        if not membership:
            continue
        role, member = membership
        add_chunk(
            "role_membership",
            (role.lower(), member.lower()),
            f"security.{role}_add_{member}",
            sync.normalize_role_membership_sql(role, member),
        )

    return sorted(chunks, key=lambda chunk: (chunk.sort_key, chunk.name_slug))


def find_unparsed_batches(content: str, sync, chunks: list[MigrationChunk]) -> list[str]:
    covered = {sync.sql_dedupe_key(chunk.sql) for chunk in chunks}
    unparsed: list[str] = []

    batches = re.split(r"^\s*GO\s*$", content, flags=re.MULTILINE | re.IGNORECASE)
    for batch in batches:
        normalized = normalize_migration_sql(batch)
        if not normalized:
            continue
        if sync.sql_dedupe_key(normalized) in covered:
            continue

        known = (
            sync.extract_schema_statements(normalized)
            or sync.extract_type_statements(normalized)
            or sync.extract_sequence_statements(normalized)
            or sync.extract_assembly_statements(normalized)
            or sync.extract_create_table_statements(normalized)
            or sync.extract_alter_table_statements(normalized)
            or sync.extract_index_statements(normalized)
            or sync.extract_statistics_statements(normalized)
            or sync.extract_routine_statements(normalized)
            or sync.extract_synonym_statements(normalized)
            or sync.extract_role_statements(normalized)
            or sync.extract_role_membership_statements(normalized)
        )
        if known:
            continue

        unparsed.append(normalized)

    return unparsed


def write_migration_files(
    chunks: list[MigrationChunk],
    version_dir: Path,
    project_root: Path,
    dry_run: bool,
) -> list[Path]:
    written: list[Path] = []
    sequence = next_sequence_number(version_dir) if version_dir.exists() else 1

    for chunk in chunks:
        migration_id = generate_migration_id()
        filename = f"{sequence:02d}_{chunk.name_slug}.sql"
        output_path = version_dir / filename
        body = chunk.sql.strip()
        if not body.endswith("\n"):
            body += "\n"
        content = build_migration_header(migration_id) + body

        if dry_run:
            print(f"Would create: {output_path.relative_to(project_root)}")
            print(content)
            print()
        else:
            output_path.write_text(content, encoding="utf-8")
            print(f"Created: {output_path.relative_to(project_root)}")
            written.append(output_path)

        sequence += 1

    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap migration files from a baseline SQL script.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to baseline schema SQL (SSMS / SqlPackage / mssql-scripter export)",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Target semver migration folder (MAJOR.MINOR.PATCH), e.g. 1.0.0",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (defaults to the current directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing .sql files in the target version folder before writing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned migration files without writing them",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Run sync-schema-from-migrations.py after writing migration files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    input_path = args.input.resolve()

    if not SEMVER_FOLDER_PATTERN.fullmatch(args.version):
        print(
            "ERROR: --version must be MAJOR.MINOR.PATCH (example: 1.0.0)",
            file=sys.stderr,
        )
        return 1

    if not input_path.is_file():
        print(f"ERROR: Baseline file not found: {input_path}", file=sys.stderr)
        return 1

    sync = load_sync_module()
    content = input_path.read_text(encoding="utf-8")
    chunks = parse_baseline(content, sync)

    if not chunks:
        print(
            "ERROR: No supported schema objects found in baseline SQL.",
            file=sys.stderr,
        )
        return 1

    version_dir = project_root / "Deployments" / "Migrations" / args.version
    existing_files = sorted(version_dir.glob("*.sql")) if version_dir.exists() else []

    if existing_files and not args.force and not args.dry_run:
        print(
            f"ERROR: {version_dir.relative_to(project_root)} already contains "
            f"{len(existing_files)} migration file(s). Use --force to replace them "
            "or choose a new --version.",
            file=sys.stderr,
        )
        return 1

    if existing_files and args.force and not args.dry_run:
        for sql_file in existing_files:
            sql_file.unlink()
            print(f"Removed: {sql_file.relative_to(project_root)}")

    version_dir.mkdir(parents=True, exist_ok=True)

    unparsed = find_unparsed_batches(normalize_baseline_sql(content), sync, chunks)
    if unparsed:
        print(
            f"WARN: {len(unparsed)} batch(es) were not recognized and will be skipped.",
            file=sys.stderr,
        )
        for index, batch in enumerate(unparsed, start=1):
            preview = batch.splitlines()[0][:120]
            print(f"  skipped batch {index}: {preview}", file=sys.stderr)

    print(
        f"Parsed {len(chunks)} object(s) from {input_path.name} "
        f"-> Deployments/Migrations/{args.version}/"
    )
    write_migration_files(chunks, version_dir, project_root, args.dry_run)

    if args.sync:
        if args.dry_run:
            print("Skipping sync because --dry-run was specified.")
            return 0
        return sync.sync(project_root)

    if not args.dry_run:
        print(
            "Next: sql-mig sync "
            "or dotnet build Databasecode.sqlproj"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
