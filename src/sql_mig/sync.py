#!/usr/bin/env python3
"""
Sync SchemaModel/ from Deployments/Migrations/.

Migrations are the source of truth. This script runs before dacpac build and
writes declarative object scripts into SchemaModel/{schema}/{objectType}/.

Migration folders must use semantic versioning: MAJOR.MINOR.PATCH
  Valid:   Deployments/Migrations/1.0.0/, Deployments/Migrations/1.2.3/
  Invalid: Deployments/Migrations/1.1/, Deployments/Migrations/v1.0.0/

Supported in each migration file:
  - CREATE TABLE (with optional IF OBJECT_ID wrapper)
  - ALTER TABLE ... ADD / DROP COLUMN / ALTER COLUMN (merged into CREATE TABLE)
  - ALTER TABLE ... ADD / DROP CONSTRAINT (merged into CREATE TABLE)
  - CREATE INDEX (merged into the parent table script)
  - DROP INDEX removes index from the parent table script
  - CREATE STATISTICS (merged into the parent table script)
  - DROP STATISTICS removes statistic from the parent table script
  - CREATE PROCEDURE / VIEW / FUNCTION / TRIGGER (CREATE OR ALTER supported)
  - CREATE SYNONYM (with optional IF OBJECT_ID wrapper)
  - CREATE TYPE ... FROM / AS TABLE -> Types/UserDefinedDataTypes or Types/UserDefinedTableTypes
  - CREATE SCHEMA -> Security/Schemas/{schema}.sql
  - CREATE ROLE -> Security/Roles/{role}.sql
  - CREATE SEQUENCE -> {schema}/Sequences/{sequence}.sql
  - CREATE ASSEMBLY -> Assemblies/{assembly}.sql
  - ALTER ROLE ... ADD MEMBER / sp_addrolemember -> merged into Security/Roles/{role}.sql
  - SCHEMA-OBJECT block with the full desired definition:

    -- SCHEMA-OBJECT-START
    CREATE TABLE [dbo].[Person] ( ... );
    -- SCHEMA-OBJECT-END

Later migration versions overwrite earlier definitions for the same object.
ALTER TABLE changes are applied incrementally to the accumulated table model.

Generated SchemaModel scripts are plain declarative CREATE statements for SSDT
dacpac build. Migrations may use CREATE OR ALTER and IF guards; those are
stripped or unwrapped during sync.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

OBJECT_TYPE_FOLDERS = {
    "TABLE": "Tables",
    "PROCEDURE": "StoredProcedures",
    "VIEW": "Views",
    "FUNCTION": "Functions",
    "TRIGGER": "Triggers",
    "SYNONYM": "Synonyms",
}

CREATE_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?(TABLE|PROCEDURE|VIEW|FUNCTION|TRIGGER|SYNONYM)\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

CREATE_SYNONYM_PATTERN = re.compile(
    r"\bCREATE\s+SYNONYM\s+",
    re.IGNORECASE,
)

CREATE_TYPE_HEADER_PATTERN = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?TYPE\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

IF_TYPE_ID_PATTERN = re.compile(
    r"IF\s+TYPE_ID\s*\([^)]+\)\s+IS\s+NULL\s+BEGIN\s*(.*?)\s*END\s*;?",
    re.IGNORECASE | re.DOTALL,
)

TYPE_ROOT_FOLDER = "Types"
TYPE_TABLE_FOLDER = "UserDefinedTableTypes"
TYPE_ALIAS_FOLDER = "UserDefinedDataTypes"

CREATE_ROUTINE_PATTERN = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?(PROCEDURE|FUNCTION|VIEW|TRIGGER)\s+",
    re.IGNORECASE,
)

BEGIN_END_TOKEN_PATTERN = re.compile(r"\b(BEGIN|END)\b", re.IGNORECASE)

CREATE_TABLE_HEADER_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))\s*\(",
    re.IGNORECASE,
)

ALTER_TABLE_PATTERN = re.compile(
    r"ALTER\s+TABLE\s+(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))\s+"
    r"(ADD|DROP|ALTER)\s+(.+?)\s*;",
    re.IGNORECASE | re.DOTALL,
)

CREATE_INDEX_HEADER_PATTERN = re.compile(
    r"\bCREATE\s+(?:(?:UNIQUE|CLUSTERED|NONCLUSTERED)\s+)*INDEX\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\s+ON\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))(?=\s*\()",
    re.IGNORECASE,
)

DROP_INDEX_PATTERN = re.compile(
    r"\bDROP\s+INDEX\s+(\[(?:[^\]]+)\]|(?:\w+))\s+ON\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))\s*;",
    re.IGNORECASE,
)

CREATE_STATISTICS_HEADER_PATTERN = re.compile(
    r"\bCREATE\s+STATISTICS\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\s+ON\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))(?=\s*\()",
    re.IGNORECASE,
)

DROP_STATISTICS_PATTERN = re.compile(
    r"\bDROP\s+STATISTICS\s+(\[(?:[^\]]+)\]|(?:\w+))\s+ON\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))\s*;",
    re.IGNORECASE,
)

SCHEMA_OBJECT_PATTERN = re.compile(
    r"--\s*SCHEMA-OBJECT-START\s*(.*?)\s*--\s*SCHEMA-OBJECT-END",
    re.IGNORECASE | re.DOTALL,
)

CREATE_SCHEMA_PATTERN = re.compile(
    r"CREATE\s+SCHEMA\s+(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

IF_SCHEMA_ID_PATTERN = re.compile(
    r"IF\s+SCHEMA_ID\s*\([^)]+\)\s+IS\s+NULL\s+BEGIN\s*(.*?)\s*END\s*;?",
    re.IGNORECASE | re.DOTALL,
)

CREATE_ROLE_PATTERN = re.compile(
    r"\bCREATE\s+ROLE\s+(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

IF_DATABASE_PRINCIPAL_ID_PATTERN = re.compile(
    r"IF\s+DATABASE_PRINCIPAL_ID\s*\([^)]+\)\s+IS\s+NULL\s+BEGIN\s*(.*?)\s*END\s*;?",
    re.IGNORECASE | re.DOTALL,
)

SECURITY_ROOT_FOLDER = "Security"
SECURITY_SCHEMAS_FOLDER = "Schemas"
SECURITY_ROLES_FOLDER = "Roles"
SEQUENCES_FOLDER = "Sequences"
ASSEMBLIES_FOLDER = "Assemblies"

CREATE_SEQUENCE_PATTERN = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?SEQUENCE\s+"
    r"(\[(?:[^\]]+)\]|(?:\w+))\.(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

CREATE_ASSEMBLY_PATTERN = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?ASSEMBLY\s+(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

ALTER_ROLE_ADD_MEMBER_PATTERN = re.compile(
    r"\bALTER\s+ROLE\s+(\[(?:[^\]]+)\]|(?:\w+))\s+ADD\s+MEMBER\s+(\[(?:[^\]]+)\]|(?:\w+))",
    re.IGNORECASE,
)

SP_ADDROLEMEMBER_PATTERN = re.compile(
    r"\b(?:EXEC(?:UTE)?(?:\s*\(\s*N')?\s*)?"
    r"sp_addrolemember\s+"
    r"(?:@rolename\s*=\s*)?N?'([^']+)'\s*,\s*(?:@membername\s*=\s*)?N?'([^']+)'",
    re.IGNORECASE,
)

EXEC_N_PATTERN = re.compile(
    r"EXEC\s*\(\s*N'(.*?)'\s*\)\s*;?",
    re.IGNORECASE | re.DOTALL,
)

IF_OBJECT_ID_PATTERN = re.compile(
    r"IF\s+OBJECT_ID\s*\([^)]+\)\s+IS\s+NULL\s+BEGIN\s*(.*?)\s*END\s*;?",
    re.IGNORECASE | re.DOTALL,
)

TABLE_CONSTRAINT_PREFIXES = (
    "CONSTRAINT",
    "PRIMARY KEY",
    "FOREIGN KEY",
    "UNIQUE",
    "CHECK",
)

DEPLOYMENTS_ROOT_FOLDER = "Deployments"
MIGRATIONS_ROOT_FOLDER = "Migrations"

GENERATED_HEADER = (
    "-- AUTO-GENERATED from Deployments/Migrations/. Do not edit directly.\n"
    "-- Add or change scripts under Deployments/Migrations/MAJOR.MINOR.PATCH/ and rebuild.\n\n"
)

SEMVER_FOLDER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass
class TableModel:
    schema: str
    name: str
    columns: dict[str, str] = field(default_factory=dict)
    constraints: dict[str, str] = field(default_factory=dict)
    constraint_order: list[str] = field(default_factory=list)
    indexes: dict[str, str] = field(default_factory=dict)
    index_order: list[str] = field(default_factory=list)
    statistics: dict[str, str] = field(default_factory=dict)
    statistics_order: list[str] = field(default_factory=list)

    def load_from_create(self, sql: str) -> None:
        parsed = parse_create_table(sql)
        if not parsed:
            raise ValueError("No CREATE TABLE statement found")

        self.schema, self.name, body = parsed
        self.columns.clear()
        self.constraints.clear()
        self.constraint_order.clear()
        self.indexes.clear()
        self.index_order.clear()
        self.statistics.clear()
        self.statistics_order.clear()

        for item in split_table_body(body):
            item_name, definition, is_constraint = parse_table_item(item)
            if is_constraint:
                self.constraints[item_name] = definition
                self.constraint_order.append(item_name)
            else:
                self.columns[item_name] = definition

    def add_table_constraint(self, name: str, definition: str) -> None:
        self.constraints[name] = definition
        if name not in self.constraint_order:
            self.constraint_order.append(name)

    def apply_add(self, rest: str) -> None:
        for clause in split_add_clauses(rest):
            self._apply_single_add(clause)

    def _apply_single_add(self, rest: str) -> None:
        rest = rest.strip()
        upper = rest.upper()

        if upper.startswith("CONSTRAINT"):
            for_column = re.search(
                r"\s+FOR\s+(\[(?:[^\]]+)\]|(?:\w+))\s*$",
                rest,
                re.IGNORECASE,
            )
            if for_column:
                column_name = strip_brackets(for_column.group(1))
                constraint_body = re.sub(
                    r"\s+FOR\s+(\[(?:[^\]]+)\]|(?:\w+))\s*$",
                    "",
                    rest,
                    flags=re.IGNORECASE,
                ).strip()
                constraint_name_match = re.match(
                    r"CONSTRAINT\s+(\[(?:[^\]]+)\]|(?:\w+))",
                    constraint_body,
                    re.IGNORECASE,
                )
                default_match = re.search(
                    r"(DEFAULT\s+.+)$",
                    constraint_body,
                    re.IGNORECASE,
                )
                if column_name not in self.columns:
                    raise ValueError(f"Column [{column_name}] does not exist for ADD CONSTRAINT")
                if constraint_name_match and default_match:
                    constraint_name = strip_brackets(constraint_name_match.group(1))
                    self.columns[column_name] = (
                        f"{self.columns[column_name]} "
                        f"CONSTRAINT [{constraint_name}] {default_match.group(1)}"
                    )
                return

            constraint_name_match = re.match(
                r"CONSTRAINT\s+(\[(?:[^\]]+)\]|(?:\w+))",
                rest,
                re.IGNORECASE,
            )
            if not constraint_name_match:
                raise ValueError(f"Could not parse table constraint: {rest}")
            constraint_name = strip_brackets(constraint_name_match.group(1))
            self.add_table_constraint(constraint_name, rest)
            return

        if upper.startswith("DEFAULT"):
            for_column = re.search(
                r"\s+FOR\s+(\[(?:[^\]]+)\]|(?:\w+))\s*$",
                rest,
                re.IGNORECASE,
            )
            if not for_column:
                raise ValueError(f"Could not parse column DEFAULT clause: {rest}")
            column_name = strip_brackets(for_column.group(1))
            if column_name not in self.columns:
                raise ValueError(f"Column [{column_name}] does not exist for ADD DEFAULT")
            default_clause = re.sub(
                r"\s+FOR\s+(\[(?:[^\]]+)\]|(?:\w+))\s*$",
                "",
                rest,
                flags=re.IGNORECASE,
            ).strip()
            if "CONSTRAINT" not in self.columns[column_name].upper():
                self.columns[column_name] = f"{self.columns[column_name]} {default_clause}"
            return

        for prefix in ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK"):
            if upper.startswith(prefix):
                synthetic_name = f"__{prefix.replace(' ', '_').lower()}_{len(self.constraint_order)}__"
                self.add_table_constraint(synthetic_name, rest)
                return

        column_match = re.match(
            r"(\[(?:[^\]]+)\]|(?:\w+))\s+(.+)",
            rest,
            re.IGNORECASE | re.DOTALL,
        )
        if not column_match:
            raise ValueError(f"Could not parse ADD clause: {rest}")

        column_name = strip_brackets(column_match.group(1))
        column_def = column_match.group(2).strip().rstrip(",")
        if not column_def.upper().startswith("["):
            column_def = f"[{column_name}] {column_def}"
        self.columns[column_name] = column_def

    def apply_drop(self, rest: str) -> None:
        rest = rest.strip()
        upper = rest.upper()

        if upper.startswith("COLUMN"):
            column_match = re.match(
                r"COLUMN\s+(\[(?:[^\]]+)\]|(?:\w+))",
                rest,
                re.IGNORECASE,
            )
            if not column_match:
                raise ValueError(f"Could not parse DROP COLUMN clause: {rest}")
            column_name = strip_brackets(column_match.group(1))
            if column_name not in self.columns:
                raise ValueError(f"Column [{column_name}] does not exist for DROP COLUMN")
            del self.columns[column_name]
            return

        if upper.startswith("CONSTRAINT"):
            constraint_match = re.match(
                r"CONSTRAINT\s+(\[(?:[^\]]+)\]|(?:\w+))",
                rest,
                re.IGNORECASE,
            )
            if not constraint_match:
                raise ValueError(f"Could not parse DROP CONSTRAINT clause: {rest}")
            constraint_name = strip_brackets(constraint_match.group(1))
            if constraint_name not in self.constraints:
                raise ValueError(
                    f"Constraint [{constraint_name}] does not exist for DROP CONSTRAINT"
                )
            del self.constraints[constraint_name]
            self.constraint_order = [
                name for name in self.constraint_order if name != constraint_name
            ]
            return

        raise ValueError(f"Could not parse DROP clause: {rest}")

    def apply_alter_column(self, rest: str) -> None:
        column_match = re.match(
            r"COLUMN\s+(\[(?:[^\]]+)\]|(?:\w+))\s+(.+)",
            rest.strip(),
            re.IGNORECASE | re.DOTALL,
        )
        if not column_match:
            raise ValueError(f"Could not parse ALTER COLUMN clause: {rest}")

        column_name = strip_brackets(column_match.group(1))
        if column_name not in self.columns:
            raise ValueError(f"Column [{column_name}] does not exist for ALTER COLUMN")

        new_definition = column_match.group(2).strip().rstrip(",")
        if not new_definition.upper().startswith("["):
            new_definition = f"[{column_name}] {new_definition}"
        self.columns[column_name] = new_definition

    def apply_alter(self, action: str, rest: str) -> None:
        action_upper = action.upper()
        if action_upper == "ADD":
            self.apply_add(rest)
        elif action_upper == "DROP":
            self.apply_drop(rest)
        elif action_upper == "ALTER":
            self.apply_alter_column(rest)
        else:
            raise ValueError(f"Unsupported ALTER TABLE action: {action}")

    def add_index(self, name: str, definition: str) -> None:
        self.indexes[name] = definition
        if name not in self.index_order:
            self.index_order.append(name)

    def drop_index(self, name: str) -> None:
        if name not in self.indexes:
            raise ValueError(f"Index [{name}] does not exist for DROP INDEX")
        del self.indexes[name]
        self.index_order = [index_name for index_name in self.index_order if index_name != name]

    def add_statistic(self, name: str, definition: str) -> None:
        self.statistics[name] = definition
        if name not in self.statistics_order:
            self.statistics_order.append(name)

    def drop_statistic(self, name: str) -> None:
        if name not in self.statistics:
            raise ValueError(f"Statistic [{name}] does not exist for DROP STATISTICS")
        del self.statistics[name]
        self.statistics_order = [
            statistic_name for statistic_name in self.statistics_order if statistic_name != name
        ]

    def render(self) -> str:
        if not self.columns and not self.constraints:
            raise ValueError(f"Table [{self.schema}].[{self.name}] has no columns")

        items = list(self.columns.values())
        items.extend(self.constraints[name] for name in self.constraint_order if name in self.constraints)
        body = ",\n    ".join(items)
        create_table = f"CREATE TABLE [{self.schema}].[{self.name}] (\n    {body}\n);"

        trailing: list[str] = []
        trailing.extend(
            self.indexes[name] for name in self.index_order if name in self.indexes
        )
        trailing.extend(
            self.statistics[name] for name in self.statistics_order if name in self.statistics
        )
        if not trailing:
            return create_table
        # SSDT requires GO between statements in a single table script file.
        return create_table + "\nGO\n\n" + "\nGO\n\n".join(trailing)


@dataclass
class RoleModel:
    name: str
    create_sql: str = ""
    member_order: list[str] = field(default_factory=list)
    members: dict[str, str] = field(default_factory=dict)

    def set_create(self, sql: str) -> None:
        self.create_sql = normalize_for_declarative_model(sql)

    def add_member(self, member_name: str, sql: str) -> None:
        normalized_member = strip_brackets(member_name)
        normalized_sql = sql.strip()
        if normalized_member not in self.members:
            self.member_order.append(normalized_member)
        self.members[normalized_member] = normalized_sql

    def render(self) -> str:
        batches: list[str] = []
        if self.create_sql:
            batches.append(self.create_sql.strip())
        elif self.members:
            batches.append(f"CREATE ROLE [{self.name}];")
        batches.extend(
            self.members[member_name]
            for member_name in self.member_order
            if member_name in self.members
        )
        if not batches:
            raise ValueError(f"Role [{self.name}] has no CREATE ROLE or members")
        if len(batches) == 1:
            return batches[0]
        return "\nGO\n\n".join(batches)


def parse_semver_folder(name: str) -> tuple[int, int, int] | None:
    match = SEMVER_FOLDER_PATTERN.fullmatch(name)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def validate_migration_folders(migrations_dir: Path) -> list[Path]:
    invalid: list[Path] = []
    valid: list[Path] = []

    for entry in migrations_dir.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue

        if parse_semver_folder(entry.name) is None:
            invalid.append(entry)
        else:
            valid.append(entry)

    if invalid:
        invalid_names = ", ".join(path.name for path in sorted(invalid, key=lambda p: p.name))
        print(
            "ERROR: Migration folders must use semantic versioning (MAJOR.MINOR.PATCH). "
            f"Invalid folder(s): {invalid_names}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    return sorted(valid, key=lambda path: parse_semver_folder(path.name) or (0, 0, 0))


def migration_sort_key(path: Path) -> tuple[tuple[int, int, int], str]:
    version = parse_semver_folder(path.parent.name)
    if version is None:
        version = (0, 0, 0)
    return version, path.name


def strip_brackets(identifier: str) -> str:
    return identifier.strip().strip("[]")


def parse_create_table(sql: str, start_index: int = 0) -> tuple[str, str, str] | None:
    match = CREATE_TABLE_HEADER_PATTERN.search(sql, start_index)
    if not match:
        return None

    open_paren = match.end() - 1
    depth = 0
    index = open_paren

    while index < len(sql):
        char = sql[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
        index += 1

    if depth != 0:
        return None

    body = sql[match.end() : index]
    return strip_brackets(match.group(1)), strip_brackets(match.group(2)), body


def extract_create_table_statement(sql: str, start_index: int = 0) -> str | None:
    match = CREATE_TABLE_HEADER_PATTERN.search(sql, start_index)
    if not match:
        return None

    open_paren = match.end() - 1
    depth = 0
    index = open_paren

    while index < len(sql):
        char = sql[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                index += 1
                break
        index += 1

    if depth != 0:
        return None

    while index < len(sql) and sql[index].isspace():
        index += 1
    if index < len(sql) and sql[index] == ";":
        index += 1

    return sql[match.start() : index].strip()


def contains_create_table(sql: str) -> bool:
    return parse_create_table(sql) is not None


def split_add_clauses(rest: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    index = 0

    while index < len(rest):
        char = rest[index]
        if char == "'":
            if not in_string:
                in_string = True
                current.append(char)
                index += 1
                continue
            if index + 1 < len(rest) and rest[index + 1] == "'":
                current.append("''")
                index += 2
                continue
            in_string = False
            current.append(char)
            index += 1
            continue
        if in_string:
            current.append(char)
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts if parts else [rest.strip()]


def split_table_body(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0

    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def parse_table_item(item: str) -> tuple[str, str, bool]:
    item = item.strip()
    upper = item.upper()

    if upper.startswith(TABLE_CONSTRAINT_PREFIXES):
        if upper.startswith("CONSTRAINT"):
            name_match = re.match(
                r"CONSTRAINT\s+(\[(?:[^\]]+)\]|(?:\w+))",
                item,
                re.IGNORECASE,
            )
            name = strip_brackets(name_match.group(1)) if name_match else f"__constraint_{abs(hash(item))}"
        else:
            name = f"__constraint_{abs(hash(item))}"
        return name, item, True

    column_match = re.match(r"(\[(?:[^\]]+)\]|(?:\w+))\s+", item)
    if not column_match:
        raise ValueError(f"Cannot parse table item: {item}")

    return strip_brackets(column_match.group(1)), item, False


def normalize_for_declarative_model(sql: str) -> str:
    sql = re.sub(r"CREATE\s+OR\s+ALTER\s+", "CREATE ", sql, count=1, flags=re.IGNORECASE)

    lines = sql.splitlines()
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return sql.strip()

    min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    normalized_lines = [
        line[min_indent:] if line.strip() else line
        for line in lines
    ]
    return "\n".join(normalized_lines).strip()


def prepare_schema_model_sql(sql: str) -> str:
    """Convert migration SQL into SSDT-compatible declarative CREATE scripts."""
    normalized = normalize_for_declarative_model(sql)
    if not normalized:
        return ""

    batches = re.split(r"^\s*GO\s*$", normalized, flags=re.MULTILINE | re.IGNORECASE)
    declarative_batches = [
        normalize_for_declarative_model(batch.strip())
        for batch in batches
        if batch.strip()
    ]
    if not declarative_batches:
        return ""
    return "\nGO\n\n".join(declarative_batches)


def unwrap_exec(content: str) -> str:
    exec_match = EXEC_N_PATTERN.search(content)
    if exec_match:
        return exec_match.group(1).strip()
    return content.strip()


def table_key(schema: str, name: str) -> tuple[str, str]:
    return strip_brackets(schema), strip_brackets(name)


def get_or_create_table(
    registry: dict[tuple[str, str], TableModel],
    schema: str,
    name: str,
) -> TableModel:
    key = table_key(schema, name)
    if key not in registry:
        registry[key] = TableModel(schema=key[0], name=key[1])
    return registry[key]


def get_or_create_role(registry: dict[str, RoleModel], name: str) -> RoleModel:
    role_name = strip_brackets(name)
    if role_name not in registry:
        registry[role_name] = RoleModel(name=role_name)
    return registry[role_name]


def is_table_type(sql: str) -> bool:
    return re.search(r"\bAS\s+TABLE\b", sql, re.IGNORECASE) is not None


def resolve_type_output_path(schemas_dir: Path, sql: str) -> Path | None:
    match = CREATE_TYPE_HEADER_PATTERN.search(sql)
    if not match:
        return None

    schema_name = strip_brackets(match.group(1))
    type_name = strip_brackets(match.group(2))
    subfolder = TYPE_TABLE_FOLDER if is_table_type(sql) else TYPE_ALIAS_FOLDER
    return schemas_dir / schema_name / TYPE_ROOT_FOLDER / subfolder / f"{type_name}.sql"


def resolve_schema_output_path(schemas_dir: Path, sql: str) -> Path | None:
    schema_match = CREATE_SCHEMA_PATTERN.search(sql)
    if not schema_match:
        return None
    schema_name = strip_brackets(schema_match.group(1))
    return (
        schemas_dir
        / SECURITY_ROOT_FOLDER
        / SECURITY_SCHEMAS_FOLDER
        / f"{schema_name}.sql"
    )


def resolve_role_output_path(schemas_dir: Path, sql: str) -> Path | None:
    role_match = CREATE_ROLE_PATTERN.search(sql)
    if not role_match:
        return None
    role_name = strip_brackets(role_match.group(1))
    return (
        schemas_dir
        / SECURITY_ROOT_FOLDER
        / SECURITY_ROLES_FOLDER
        / f"{role_name}.sql"
    )


def resolve_sequence_output_path(schemas_dir: Path, sql: str) -> Path | None:
    match = CREATE_SEQUENCE_PATTERN.search(sql)
    if not match:
        return None
    schema_name = strip_brackets(match.group(1))
    sequence_name = strip_brackets(match.group(2))
    return (
        schemas_dir
        / schema_name
        / SEQUENCES_FOLDER
        / f"{sequence_name}.sql"
    )


def resolve_assembly_output_path(schemas_dir: Path, sql: str) -> Path | None:
    match = CREATE_ASSEMBLY_PATTERN.search(sql)
    if not match:
        return None
    assembly_name = strip_brackets(match.group(1))
    return schemas_dir / ASSEMBLIES_FOLDER / f"{assembly_name}.sql"


def normalize_role_membership_sql(role_name: str, member_name: str) -> str:
    return f"ALTER ROLE [{role_name}] ADD MEMBER [{member_name}];"


def parse_role_membership(sql: str) -> tuple[str, str] | None:
    alter_match = ALTER_ROLE_ADD_MEMBER_PATTERN.search(sql)
    if alter_match:
        return (
            strip_brackets(alter_match.group(1)),
            strip_brackets(alter_match.group(2)),
        )

    sp_match = SP_ADDROLEMEMBER_PATTERN.search(sql)
    if sp_match:
        return sp_match.group(1), sp_match.group(2)

    exec_match = EXEC_N_PATTERN.search(sql)
    if exec_match:
        return parse_role_membership(exec_match.group(1))

    return None


def parse_create_index_target(sql: str) -> tuple[str, str, str] | None:
    match = CREATE_INDEX_HEADER_PATTERN.search(sql)
    if not match:
        return None
    return (
        strip_brackets(match.group(1)),
        strip_brackets(match.group(2)),
        strip_brackets(match.group(3)),
    )


def extract_index_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    for match in CREATE_INDEX_HEADER_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        statement = content[match.start():statement_end].strip()
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    return statements


def extract_drop_index_targets(content: str) -> list[tuple[str, str, str]]:
    targets: list[tuple[str, str, str]] = []
    for match in DROP_INDEX_PATTERN.finditer(content):
        index_name = strip_brackets(match.group(1))
        schema_name = strip_brackets(match.group(2))
        table_name = strip_brackets(match.group(3))
        targets.append((schema_name, table_name, index_name))
    return targets


def parse_create_statistics_target(sql: str) -> tuple[str, str, str] | None:
    match = CREATE_STATISTICS_HEADER_PATTERN.search(sql)
    if not match:
        return None
    return (
        strip_brackets(match.group(1)),
        strip_brackets(match.group(2)),
        strip_brackets(match.group(3)),
    )


def extract_statistics_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    for match in CREATE_STATISTICS_HEADER_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        statement = content[match.start():statement_end].strip()
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    return statements


def extract_drop_statistics_targets(content: str) -> list[tuple[str, str, str]]:
    targets: list[tuple[str, str, str]] = []
    for match in DROP_STATISTICS_PATTERN.finditer(content):
        statistic_name = strip_brackets(match.group(1))
        schema_name = strip_brackets(match.group(2))
        table_name = strip_brackets(match.group(3))
        targets.append((schema_name, table_name, statistic_name))
    return targets


def resolve_output_path(schemas_dir: Path, sql: str) -> Path | None:
    schema_path = resolve_schema_output_path(schemas_dir, sql)
    if schema_path:
        return schema_path

    role_path = resolve_role_output_path(schemas_dir, sql)
    if role_path:
        return role_path

    type_path = resolve_type_output_path(schemas_dir, sql)
    if type_path:
        return type_path

    sequence_path = resolve_sequence_output_path(schemas_dir, sql)
    if sequence_path:
        return sequence_path

    assembly_path = resolve_assembly_output_path(schemas_dir, sql)
    if assembly_path:
        return assembly_path

    match = CREATE_PATTERN.search(sql)
    if not match:
        return None

    object_type, schema, name = match.groups()
    folder = OBJECT_TYPE_FOLDERS.get(object_type.upper())
    if not folder:
        return None

    schema_name = strip_brackets(schema)
    object_name = strip_brackets(name)
    return schemas_dir / schema_name / folder / f"{object_name}.sql"


def sql_dedupe_key(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).upper()


def extract_create_table_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    def add_statement(sql: str) -> None:
        normalized = sql.strip()
        if not contains_create_table(normalized):
            return
        key = sql_dedupe_key(normalized)
        if key in seen:
            return
        seen.add(key)
        statements.append(normalized)

    for block in SCHEMA_OBJECT_PATTERN.finditer(content):
        add_statement(block.group(1).strip())

    content_without_blocks = SCHEMA_OBJECT_PATTERN.sub("", content)
    wrapped_spans: list[tuple[int, int]] = []

    for wrapper in IF_OBJECT_ID_PATTERN.finditer(content_without_blocks):
        inner = wrapper.group(1).strip()
        if contains_create_table(inner):
            add_statement(inner)
            wrapped_spans.append(wrapper.span())

    for match in CREATE_TABLE_HEADER_PATTERN.finditer(content_without_blocks):
        if any(start <= match.start() < end for start, end in wrapped_spans):
            continue
        statement = extract_create_table_statement(content_without_blocks, match.start())
        if statement:
            add_statement(statement)

    return statements


def find_statement_end_semicolon(sql: str, start: int) -> int:
    depth = 0
    in_string = False
    index = start

    while index < len(sql):
        char = sql[index]
        if char == "'" and not in_string:
            in_string = True
            index += 1
            continue
        if char == "'" and in_string:
            if index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            in_string = False
            index += 1
            continue
        if in_string:
            index += 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == ";" and depth == 0:
            return index + 1
        index += 1

    return len(sql)


def find_routine_end(sql: str, start: int) -> int:
    as_match = re.search(r"\bAS\b", sql[start:], re.IGNORECASE)
    if not as_match:
        return find_statement_end_semicolon(sql, start)

    search_from = start + as_match.start()
    begin_match = re.search(r"\bBEGIN\b", sql[search_from:], re.IGNORECASE)
    if not begin_match:
        return find_statement_end_semicolon(sql, start)

    depth = 0
    for token in BEGIN_END_TOKEN_PATTERN.finditer(sql, search_from + begin_match.start()):
        if token.group(1).upper() == "BEGIN":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                end = token.end()
                while end < len(sql) and sql[end].isspace():
                    end += 1
                if end < len(sql) and sql[end] == ";":
                    end += 1
                return end

    return -1


def extract_routine_statement(sql: str, start: int = 0) -> tuple[str, int] | None:
    match = CREATE_ROUTINE_PATTERN.search(sql, start)
    if not match:
        return None

    statement_start = match.start()
    statement_end = find_routine_end(sql, statement_start)
    if statement_end < 0:
        return None

    return sql[statement_start:statement_end].strip(), statement_end


def extract_routine_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()
    index = 0

    while index < len(content):
        extracted = extract_routine_statement(content, index)
        if not extracted:
            break
        statement, next_index = extracted
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)
        index = max(next_index, index + 1)

    return statements


def extract_type_statement(sql: str, start: int = 0) -> tuple[str, int] | None:
    match = CREATE_TYPE_HEADER_PATTERN.search(sql, start)
    if not match:
        return None

    statement_start = match.start()
    if is_table_type(sql[statement_start:]):
        as_table_match = re.search(r"\bAS\s+TABLE\b", sql[statement_start:], re.IGNORECASE)
        if not as_table_match:
            return None

        open_paren = sql.find("(", statement_start + as_table_match.end())
        if open_paren < 0:
            return None

        depth = 0
        index = open_paren
        while index < len(sql):
            char = sql[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    index += 1
                    break
            index += 1

        if depth != 0:
            return None

        while index < len(sql) and sql[index].isspace():
            index += 1
        if index < len(sql) and sql[index] == ";":
            index += 1
        return sql[statement_start:index].strip(), index

    statement_end = find_statement_end_semicolon(sql, statement_start)
    return sql[statement_start:statement_end].strip(), statement_end


def extract_type_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()
    index = 0

    while index < len(content):
        extracted = extract_type_statement(content, index)
        if not extracted:
            break
        statement, next_index = extracted
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)
        index = max(next_index, index + 1)

    type_id_match = IF_TYPE_ID_PATTERN.search(content)
    if type_id_match:
        statement = unwrap_exec(type_id_match.group(1))
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    return statements


def extract_synonym_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    for match in CREATE_SYNONYM_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        statement = content[match.start():statement_end].strip()
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    return statements


def extract_role_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    for match in CREATE_ROLE_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        statement = content[match.start():statement_end].strip()
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    principal_match = IF_DATABASE_PRINCIPAL_ID_PATTERN.search(content)
    if principal_match:
        statement = unwrap_exec(principal_match.group(1))
        if CREATE_ROLE_PATTERN.search(statement):
            key = sql_dedupe_key(statement)
            if key not in seen:
                seen.add(key)
                statements.append(statement)

    return statements


def extract_sequence_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    for match in CREATE_SEQUENCE_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        statement = content[match.start():statement_end].strip()
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    for wrapper in IF_OBJECT_ID_PATTERN.finditer(content):
        inner = wrapper.group(1).strip()
        if CREATE_SEQUENCE_PATTERN.search(inner):
            key = sql_dedupe_key(inner)
            if key not in seen:
                seen.add(key)
                statements.append(inner)

    return statements


def extract_assembly_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    for match in CREATE_ASSEMBLY_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        statement = content[match.start():statement_end].strip()
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    for wrapper in IF_OBJECT_ID_PATTERN.finditer(content):
        inner = wrapper.group(1).strip()
        if CREATE_ASSEMBLY_PATTERN.search(inner):
            key = sql_dedupe_key(inner)
            if key not in seen:
                seen.add(key)
                statements.append(inner)

    return statements


def extract_role_membership_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    def add_membership(sql: str) -> None:
        membership = parse_role_membership(sql)
        if not membership:
            return
        role_name, member_name = membership
        normalized = normalize_role_membership_sql(role_name, member_name)
        key = sql_dedupe_key(normalized)
        if key in seen:
            return
        seen.add(key)
        statements.append(normalized)

    for match in ALTER_ROLE_ADD_MEMBER_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        add_membership(content[match.start():statement_end])

    for match in SP_ADDROLEMEMBER_PATTERN.finditer(content):
        add_membership(match.group(0))

    for exec_match in EXEC_N_PATTERN.finditer(content):
        add_membership(exec_match.group(1))

    return statements


def extract_schema_statements(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    for match in CREATE_SCHEMA_PATTERN.finditer(content):
        statement_end = find_statement_end_semicolon(content, match.start())
        statement = content[match.start():statement_end].strip()
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    schema_id_match = IF_SCHEMA_ID_PATTERN.search(content)
    if schema_id_match:
        statement = unwrap_exec(schema_id_match.group(1))
        key = sql_dedupe_key(statement)
        if key not in seen:
            seen.add(key)
            statements.append(statement)

    return statements


def extract_other_declarative_sql(content: str) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()

    def add_statement(sql: str) -> None:
        normalized = sql.strip()
        if not normalized or contains_create_table(normalized):
            return
        if (
            parse_role_membership(normalized)
            or CREATE_ROLE_PATTERN.search(normalized)
        ):
            return
        key = sql_dedupe_key(normalized)
        if key in seen:
            return
        seen.add(key)
        statements.append(normalized)

    for block in SCHEMA_OBJECT_PATTERN.finditer(content):
        add_statement(block.group(1).strip())

    content_without_blocks = SCHEMA_OBJECT_PATTERN.sub("", content)
    wrapped_spans: list[tuple[int, int]] = []

    for wrapper in IF_OBJECT_ID_PATTERN.finditer(content_without_blocks):
        inner = wrapper.group(1).strip()
        if contains_create_table(inner):
            continue
        if (
            CREATE_ROUTINE_PATTERN.search(inner)
            or CREATE_SCHEMA_PATTERN.search(inner)
            or CREATE_SYNONYM_PATTERN.search(inner)
            or CREATE_TYPE_HEADER_PATTERN.search(inner)
            or CREATE_SEQUENCE_PATTERN.search(inner)
            or CREATE_ASSEMBLY_PATTERN.search(inner)
        ):
            add_statement(inner)
            wrapped_spans.append(wrapper.span())

    for statement in extract_routine_statements(content_without_blocks):
        routine_match = CREATE_ROUTINE_PATTERN.search(statement)
        if not routine_match:
            continue
        routine_start = content_without_blocks.find(statement)
        if routine_start >= 0 and any(
            start <= routine_start < end for start, end in wrapped_spans
        ):
            continue
        add_statement(statement)

    for statement in extract_schema_statements(content_without_blocks):
        add_statement(statement)

    for statement in extract_synonym_statements(content_without_blocks):
        synonym_start = content_without_blocks.find(statement)
        if synonym_start >= 0 and any(
            start <= synonym_start < end for start, end in wrapped_spans
        ):
            continue
        add_statement(statement)

    for statement in extract_type_statements(content_without_blocks):
        type_start = content_without_blocks.find(statement)
        if type_start >= 0 and any(
            start <= type_start < end for start, end in wrapped_spans
        ):
            continue
        add_statement(statement)

    for statement in extract_sequence_statements(content_without_blocks):
        sequence_start = content_without_blocks.find(statement)
        if sequence_start >= 0 and any(
            start <= sequence_start < end for start, end in wrapped_spans
        ):
            continue
        add_statement(statement)

    for statement in extract_assembly_statements(content_without_blocks):
        assembly_start = content_without_blocks.find(statement)
        if assembly_start >= 0 and any(
            start <= assembly_start < end for start, end in wrapped_spans
        ):
            continue
        add_statement(statement)

    return statements


def extract_alter_table_statements(content: str) -> list[tuple[str, str, str, str]]:
    results: list[tuple[str, str, str, str]] = []
    for match in ALTER_TABLE_PATTERN.finditer(content):
        schema, table, action, rest = match.groups()
        results.append((schema, table, action, rest.strip()))
    return results


def process_migration_file(
    migration_file: Path,
    table_registry: dict[tuple[str, str], TableModel],
    role_registry: dict[str, RoleModel],
    other_objects: dict[Path, str],
    schemas_dir: Path,
    project_root: Path,
) -> list[str]:
    messages: list[str] = []
    content = migration_file.read_text(encoding="utf-8")

    for create_sql in extract_create_table_statements(content):
        parsed = parse_create_table(create_sql)
        if not parsed:
            continue
        schema_name, table_name, _ = parsed
        model = get_or_create_table(table_registry, schema_name, table_name)
        model.load_from_create(create_sql)
        messages.append(
            f"Loaded CREATE TABLE from {migration_file.relative_to(project_root)} "
            f"-> [{model.schema}].[{model.name}]"
        )

    for schema, table, action, rest in extract_alter_table_statements(content):
        model = get_or_create_table(table_registry, schema, table)
        if not model.columns and action.upper() != "ADD":
            print(
                f"WARN: ALTER TABLE on [{strip_brackets(schema)}].[{strip_brackets(table)}] "
                f"in {migration_file} before CREATE TABLE was found",
                file=sys.stderr,
            )
        model.apply_alter(action, rest)
        messages.append(
            f"Applied ALTER TABLE {action.upper()} from {migration_file.relative_to(project_root)} "
            f"-> [{model.schema}].[{model.name}]"
        )

    for statement in extract_index_statements(content):
        index_target = parse_create_index_target(statement)
        if not index_target:
            continue
        index_name, schema_name, table_name = index_target
        model = get_or_create_table(table_registry, schema_name, table_name)
        model.add_index(index_name, normalize_for_declarative_model(statement))
        messages.append(
            f"Loaded CREATE INDEX from {migration_file.relative_to(project_root)} "
            f"-> [{model.schema}].[{model.name}].[{index_name}]"
        )

    for schema_name, table_name, index_name in extract_drop_index_targets(content):
        model = get_or_create_table(table_registry, schema_name, table_name)
        model.drop_index(index_name)
        messages.append(
            f"Applied DROP INDEX from {migration_file.relative_to(project_root)} "
            f"-> [{model.schema}].[{model.name}].[{index_name}]"
        )

    for statement in extract_statistics_statements(content):
        statistic_target = parse_create_statistics_target(statement)
        if not statistic_target:
            continue
        statistic_name, schema_name, table_name = statistic_target
        model = get_or_create_table(table_registry, schema_name, table_name)
        model.add_statistic(statistic_name, normalize_for_declarative_model(statement))
        messages.append(
            f"Loaded CREATE STATISTICS from {migration_file.relative_to(project_root)} "
            f"-> [{model.schema}].[{model.name}].[{statistic_name}]"
        )

    for schema_name, table_name, statistic_name in extract_drop_statistics_targets(content):
        model = get_or_create_table(table_registry, schema_name, table_name)
        model.drop_statistic(statistic_name)
        messages.append(
            f"Applied DROP STATISTICS from {migration_file.relative_to(project_root)} "
            f"-> [{model.schema}].[{model.name}].[{statistic_name}]"
        )

    for statement in extract_role_statements(content):
        role_match = CREATE_ROLE_PATTERN.search(statement)
        if not role_match:
            continue
        role_name = strip_brackets(role_match.group(1))
        model = get_or_create_role(role_registry, role_name)
        model.set_create(statement)
        messages.append(
            f"Loaded CREATE ROLE from {migration_file.relative_to(project_root)} "
            f"-> [{role_name}]"
        )

    for statement in extract_role_membership_statements(content):
        membership = parse_role_membership(statement)
        if not membership:
            continue
        role_name, member_name = membership
        model = get_or_create_role(role_registry, role_name)
        model.add_member(
            member_name,
            normalize_role_membership_sql(role_name, member_name),
        )
        messages.append(
            f"Loaded role membership from {migration_file.relative_to(project_root)} "
            f"-> [{role_name}]"
        )

    for sql in extract_other_declarative_sql(content):
        output_path = resolve_output_path(schemas_dir, sql)
        if not output_path:
            print(
                f"WARN: Could not resolve output path for object in {migration_file}",
                file=sys.stderr,
            )
            continue
        other_objects[output_path] = normalize_for_declarative_model(sql)
        messages.append(
            f"Loaded object from {migration_file.relative_to(project_root)} "
            f"-> {output_path.relative_to(project_root)}"
        )

    return messages


def ensure_migration_ids(migrations_dir: Path, project_root: Path) -> None:
    """Stamp any migration file missing a Migration-Id (e.g. created manually in the IDE)."""
    from sql_mig.migration_id import generate_migration_id, has_migration_id, prepend_migration_header

    for sql_file in sorted(migrations_dir.glob("*/*.sql")):
        content = sql_file.read_text(encoding="utf-8")
        if has_migration_id(content):
            continue
        migration_id = generate_migration_id()
        sql_file.write_text(
            prepend_migration_header(content, migration_id),
            encoding="utf-8",
        )
        print(
            f"Stamped Migration-Id on {sql_file.relative_to(project_root)} "
            f"-> {migration_id}"
        )


def sync(project_root: Path) -> int:
    migrations_dir = project_root / DEPLOYMENTS_ROOT_FOLDER / MIGRATIONS_ROOT_FOLDER
    schemas_dir = project_root / "SchemaModel"

    if not migrations_dir.exists():
        print(f"ERROR: Migrations folder not found at {migrations_dir}", file=sys.stderr)
        return 1

    ensure_migration_ids(migrations_dir, project_root)

    version_folders = validate_migration_folders(migrations_dir)
    migration_files: list[Path] = []

    for version_folder in version_folders:
        migration_files.extend(sorted(version_folder.glob("*.sql")))

    migration_files = sorted(migration_files, key=migration_sort_key)

    if not migration_files:
        print("No migration files found.")
        return 0

    table_registry: dict[tuple[str, str], TableModel] = {}
    role_registry: dict[str, RoleModel] = {}
    other_objects: dict[Path, str] = {}
    synced_paths: set[Path] = set()

    for migration_file in migration_files:
        try:
            messages = process_migration_file(
                migration_file,
                table_registry,
                role_registry,
                other_objects,
                schemas_dir,
                project_root,
            )
            for message in messages:
                print(message)
        except ValueError as error:
            print(f"ERROR: {migration_file}: {error}", file=sys.stderr)
            return 1

    for model in table_registry.values():
        output_path = schemas_dir / model.schema / "Tables" / f"{model.name}.sql"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_sql = prepare_schema_model_sql(model.render())
        output_path.write_text(GENERATED_HEADER + rendered_sql + "\n", encoding="utf-8")
        synced_paths.add(output_path)
        print(f"Synced table model -> {output_path.relative_to(project_root)}")

    for model in role_registry.values():
        output_path = (
            schemas_dir
            / SECURITY_ROOT_FOLDER
            / SECURITY_ROLES_FOLDER
            / f"{model.name}.sql"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_sql = prepare_schema_model_sql(model.render())
        output_path.write_text(GENERATED_HEADER + rendered_sql + "\n", encoding="utf-8")
        synced_paths.add(output_path)
        print(f"Synced role model -> {output_path.relative_to(project_root)}")

    for output_path, sql in other_objects.items():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(GENERATED_HEADER + prepare_schema_model_sql(sql) + "\n", encoding="utf-8")
        synced_paths.add(output_path)
        print(f"Synced object -> {output_path.relative_to(project_root)}")

    for indexes_dir in schemas_dir.glob("**/Tables/Indexes"):
        if not indexes_dir.is_dir():
            continue
        for index_file in indexes_dir.glob("*.sql"):
            index_file.unlink()
            print(f"Removed stale index file -> {index_file.relative_to(project_root)}")
        indexes_dir.rmdir()

    role_memberships_dir = schemas_dir / SECURITY_ROOT_FOLDER / "RoleMemberships"
    if role_memberships_dir.is_dir():
        for membership_file in role_memberships_dir.glob("*.sql"):
            membership_file.unlink()
            print(
                f"Removed stale role membership file -> "
                f"{membership_file.relative_to(project_root)}"
            )
        role_memberships_dir.rmdir()

    for maintenance_dir in schemas_dir.glob("**/Maintenance"):
        if not maintenance_dir.is_dir():
            continue
        for maintenance_file in maintenance_dir.glob("*.sql"):
            maintenance_file.unlink()
            print(
                f"Removed stale maintenance file -> "
                f"{maintenance_file.relative_to(project_root)}"
            )
        maintenance_dir.rmdir()

    print(f"Schema sync complete. {len(synced_paths)} object(s) updated.")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Sync SchemaModel from Deployments/Migrations.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Database project root (default: current directory)",
    )
    args = parser.parse_args(argv)
    return sync(args.project_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
