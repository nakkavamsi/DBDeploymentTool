# How to use sql-migration-tools

This package is the **CLI only**. It does not contain database schema. You install it once, then run `sql-mig` from each SQL Server database project.

- Wire this tool into **other database repos** (and the Databasecode sample): [USING-FROM-DATABASE-PROJECTS.md](USING-FROM-DATABASE-PROJECTS.md)
- Full internals (parsers, SchemaModel layout, history tables): [DOCUMENTATION.md](DOCUMENTATION.md)

---

## What it does

| You write | The CLI does |
|-----------|----------------|
| Incremental SQL under `Deployments/Migrations/` | Applies it to SQL Server (`run`) |
| Version folders like `2.3.0` | Rebuilds `SchemaModel/` for SSDT / dacpac (`sync`) |

**Migrations are the source of truth.** Do not edit `SchemaModel/` by hand.

---

## Prerequisites

- Python 3.10+
- [sqlcmd](https://learn.microsoft.com/sql/tools/sqlcmd/sqlcmd-utility) on `PATH` (only needed for `sql-mig run`)
- A database project with this layout (create the folders if they do not exist yet):

```
MyDatabaseProject/
├── Databasecode.sqlproj          # optional SSDT project
├── SchemaModel/                  # generated — do not edit
└── Deployments/
    ├── Migrations/               # source of truth
    │   └── 1.0.0/
    ├── pre-deployments/          # optional one-shot scripts
    └── Rollback/                 # unused by this CLI
```

---

## Install

From this repo (editable, while developing the tools):

```bash
pip install -e /path/to/sql-migration-tools
```

From GitHub (typical for a database project or CI):

```bash
pip install "sql-migration-tools @ git+https://github.com/nakkavamsi/DBDeploymentTool.git"
```

In a database project's `requirements.txt`:

```text
sql-migration-tools @ git+https://github.com/nakkavamsi/DBDeploymentTool.git
```

Confirm:

```bash
sql-mig --help
# same as:
python -m sql_mig --help
```

Every command defaults `--project-root` to the **current working directory**. Run them from the database project root, not from this tools repo.

```bash
cd /path/to/MyDatabaseProject
sql-mig sync
```

---

## Commands at a glance

| Command | When to use |
|---------|-------------|
| `sql-mig new` | Start a new change |
| `sql-mig stamp` | Add missing `-- Migration-Id` headers |
| `sql-mig sync` | Rebuild `SchemaModel/` after editing migrations |
| `sql-mig check` | Compile every migration script before you apply it |
| `sql-mig run` | Apply pending scripts to SQL Server |
| `sql-mig bootstrap` | Split a baseline schema export into migration files |

```bash
sql-mig <command> --help
```

---

## Day-to-day: add a schema change

1. Create a stamped file in a semver folder:

```bash
cd MyDatabaseProject
sql-mig new --version 2.3.0 --name dbo.person.add_status
```

That writes something like:

```
Deployments/Migrations/2.3.0/01_20260522143000_a3f9b2c1_dbo.person.add_status.sql
```

2. Replace the `-- TODO` body with real T-SQL. Keep the header:

```sql
-- Migration-Id: 20260522143000_a3f9b2c1

ALTER TABLE [dbo].[person] ADD [status] NVARCHAR(20) NULL;
```

3. Rebuild SchemaModel (and optionally the SSDT project):

```bash
sql-mig sync
dotnet build Databasecode.sqlproj
```

4. Compile the migration chain, then apply it:

```bash
sql-mig check -S localhost -U sa -C
sql-mig run -S localhost -d MyDb -U sa -C --status
sql-mig run -S localhost -d MyDb -U sa -C
```

`check` prints the script order, parses every file, then runs that chain on a scratch database. See [Script order](#script-order) and [Compile migrations](#compile-migrations-sql-mig-check).

Windows / integrated auth:

```bash
sql-mig run -S localhost -d MyDb -E -C
```

Password auth:

```bash
sql-mig run -S localhost -d MyDb -U sa -P 'secret' -C
```

`-C` trusts the server certificate (sqlcmd `-C`). Use it against local / self-signed instances.

---

## Adopt an existing database

Export **schema-only** SQL (SSMS Generate Scripts, SqlPackage, or mssql-scripter), then:

```bash
cd MyDatabaseProject
sql-mig bootstrap --input baseline.sql --version 1.0.0 --sync
sql-mig run --list-files
sql-mig run -S localhost -d MyDb -U sa -C --dry-run
```

`--sync` rebuilds `SchemaModel/` after splitting objects into files.

### Skip objects while bootstrapping

Use manual filters so CDC / system / unwanted objects never become migrations:

```bash
# Preset: skip cdc schema + common CDC name patterns (fn_cdc_*, sp_cdc_*, cdc_*)
sql-mig bootstrap --input baseline.sql --version 1.0.0 --skip-cdc --sync

# Or pick filters yourself (repeatable flags)
sql-mig bootstrap --input baseline.sql --version 1.0.0 \
  --exclude-schema cdc \
  --exclude-kind view \
  --exclude-kind procedure \
  --exclude-kind function \
  --exclude-kind role \
  --exclude-kind user \
  --exclude-object dbo.sysdiagrams \
  --exclude-name-pattern '^fn_cdc_' \
  --sync
```

Or put the same rules in a filters file:

```text
# bootstrap.filters
schema cdc
kind role
kind user
object dbo.sysdiagrams
pattern ^fn_cdc_
pattern ^sp_cdc_
```

```bash
sql-mig bootstrap --input baseline.sql --version 1.0.0 --filters-file bootstrap.filters --sync
```

| Filter | Meaning |
|--------|---------|
| `--skip-cdc` | Schema `cdc` + name patterns `^fn_cdc_`, `^sp_cdc_`, `^cdc_` |
| `--exclude-schema` | Skip that schema and objects in it |
| `--exclude-kind` | `table`, `view`, `procedure`, `function`, `trigger`, `role`, `user`, … |
| `--exclude-name` | Skip unqualified name (roles, assemblies, …) |
| `--exclude-object` | Skip `schema.name` |
| `--exclude-name-pattern` | Case-insensitive regex on name or `schema.name` |
| `--filters-file` | Same rules from a text file |

Skipped objects are listed on stderr. `CREATE USER` is not emitted by bootstrap today; `--exclude-kind user` / `role` covers the security principals that *are* generated (roles / memberships).

If the target database **already matches** the baseline, do not re-run `1.0.0` against it. Skip that folder:

```bash
sql-mig run -S localhost -d MyDb -U sa -C --exclude-version 1.0.0
```

There is no “mark as applied without running” command. Preview with `--force` only when you intend to replace files already in that version folder:

```bash
sql-mig bootstrap --input baseline.sql --version 1.0.0 --force --sync
```

---

## Apply migrations (`sql-mig run`)

Requires `sqlcmd`. History is stored on the server:

- `dbo.__MigrationHistory` — one row per `-- Migration-Id`
- `dbo.__PreDeploymentHistory` — one row per pre-deploy relative path

Pending = files on disk whose ID is not in history. Renaming a file does not re-apply it; changing the Migration-Id would.

Useful flags:

```bash
sql-mig run --list-files                          # order on disk, no DB
sql-mig run -S localhost -d MyDb -U sa -C --status
sql-mig run -S localhost -d MyDb -U sa -C --dry-run
sql-mig run -S localhost -d MyDb -U sa -C --up-to-version 2.3.0
sql-mig run -S localhost -d MyDb -U sa -C --exclude-version 1.0.0
sql-mig run -S localhost -d MyDb -U sa -C --pre-deployments
sql-mig run -S localhost -d MyDb -U sa -C --pre-deployments-only
```

Connection is required unless you only list files.

If script N fails, it is **not** recorded. Earlier scripts stay applied. Fix the SQL and re-run.

### Pre-deployments

Optional scripts under `Deployments/pre-deployments/` (permissions, linked servers, `master` setup). They run only with `--pre-deployments` or `--pre-deployments-only`.

To run a script against another database:

```sql
-- SqlCmd-Database: master

-- script body...
```

History for that script is recorded **in that database**, not necessarily the `-d` target.

---

## Script order

`sync`, `check`, and `run` walk migrations the same way:

1. Version folders from lowest `MAJOR.MINOR.PATCH` to highest (`1.0.0`, then `1.1.0`, then `2.0.0`).
2. Inside a folder, filenames from A to Z.

`sql-mig new` prefixes `01_`, `02_`, … so that filename order stays under your control. The `-- Migration-Id` header identifies the script in history. It does not decide when the script runs. `--exclude-version` and `--up-to-version` drop folders from this list before compile or apply.

A foreign key, a procedure, or a view can use an object only after the script that creates it has already run. Put the dependency in an earlier filename.

```text
Deployments/Migrations/1.0.0/
  01_security.sales.sql              CREATE SCHEMA [sales];
  02_dbo.Customer.sql                primary key
  03_sales.Order.sql                 table only
  04_sales.Order.add_customer.sql    ALTER TABLE ... ADD CONSTRAINT FK
  05_dbo.usp_GetOrders.sql           procedure that joins both tables
```

```sql
-- 01_security.sales.sql
-- Migration-Id: ...
CREATE SCHEMA [sales];
GO
```

`CREATE SCHEMA` is its own batch. End it with `GO` before any object in that schema. The next file starts a new batch, so the schema file can contain only that statement.

```sql
-- 04_sales.Order.add_customer.sql
-- Migration-Id: ...
ALTER TABLE [sales].[Order]
    ADD CONSTRAINT [FK_Order_Customer]
    FOREIGN KEY ([CustomerId]) REFERENCES [dbo].[Customer] ([CustomerId]);
```

Write the files this way:

| Shape | Where it goes |
|-------|----------------|
| Schema | Its own file, statement ended with `GO` |
| Parent table and its primary key | Before any child that references it |
| Child table | Create the table first. Add outbound foreign keys in a later file |
| Self foreign key (`Employee.ManagerId` → `Employee.EmployeeId`) | Inside that table's `CREATE TABLE` |
| Composite foreign key | After the composite primary key exists, in a later `ALTER TABLE` |
| Two tables that reference each other | Create both with primary keys, then one later file adds both foreign keys |
| Procedure, view, function | After every table and column it names |

`sql-mig bootstrap` already writes this order: schemas and tables first, foreign keys peeled into later `ALTER TABLE` files, then routines. Hand-written migrations follow the same filename rule. `check` and `run` do not reorder files to satisfy foreign keys.

## Compile migrations (`sql-mig check`)

`dotnet build` compiles `SchemaModel/` only. Files under `Deployments/Migrations/` are removed from the SQL project, so a successful dacpac build does not prove those scripts are valid.

`sql-mig check` prints the script order, then runs two passes on that list. Both stay on until you stop one.

1. **Parse.** `SET PARSEONLY ON` for every file, against `master` (or `-d` when you pass it for a parse-only or `--noexec` run). Every syntax error is reported.
2. **Compile.** The chain executes on a database named `sqlmig_check_<timestamp>_<hex>`, then that database is dropped. A later script sees objects from earlier scripts. The first error stops this pass. History is not written. The login needs permission to create and drop a database.

A syntax error in parse does not turn compile off. Compile still runs, and the command fails if either pass fails.

```bash
sql-mig check -S localhost -U sa -C
sql-mig check -S localhost -U sa -C --exclude-version 9.9.9
sql-mig check -S localhost -U sa -C --up-to-version 1.0.0
sql-mig check -S localhost -U sa -C --skip-parse
sql-mig check -S localhost -U sa -C --skip-compile
sql-mig check -S localhost -d MyDb -U sa -C --noexec
```

`--skip-parse` stops parse. `--skip-compile` (or `--parse-only`) stops compile. `--noexec` keeps both passes and changes compile to `SET NOEXEC ON` against `-d`, so nothing is executed and nothing is dropped.

Before either pass, the command prints the plan:

```text
Script order: version folders from lowest MAJOR.MINOR.PATCH to highest,
then filenames A to Z inside each folder. check and run use this order.
The Migration-Id does not change it.

Parse and compile both run. Stop one with --skip-parse or --skip-compile.

Parse: SET PARSEONLY ON for each of 5 script(s) against master.
Each file is parsed on its own. Every syntax error is reported.

Compile: execute 5 script(s) on scratch database sqlmig_check_20261127120000_ab12cd, then drop it.
Each script runs after the ones above it, so a foreign key or procedure
can use objects those earlier scripts created. Stops at the first error.
dbo.__MigrationHistory is not written.

Order:
  1.0.0
      1  Deployments/Migrations/1.0.0/01_security.sales.sql
      2  Deployments/Migrations/1.0.0/02_dbo.Customer.sql
      3  Deployments/Migrations/1.0.0/03_sales.Order.sql
      4  Deployments/Migrations/1.0.0/04_sales.Order.add_customer.sql
      5  Deployments/Migrations/1.0.0/05_dbo.usp_GetOrders.sql
```

Each pass then walks that list:

```text
Parse: SET PARSEONLY ON against master.

-> [1/5] Deployments/Migrations/1.0.0/01_security.sales.sql
   ok
Parse: 5 migration script(s), no issues.

Compile: execute on scratch database sqlmig_check_20261127120000_ab12cd.

-> [1/5] Deployments/Migrations/1.0.0/01_security.sales.sql
   ok
```

| Pass | What it proves |
|------|----------------|
| Parse | The text is legal T-SQL. A missing foreign-key parent still passes here. |
| Compile | The chain executes in the printed order. A foreign key whose parent is still in a later file fails here (`references invalid table`). A procedure that names a missing column fails here (`Invalid column name`). |
| `--noexec` compile | Each file is compiled alone with `SET NOEXEC ON`. Syntax errors are reported and the database is unchanged. Parse still runs. |

The execute compile refuses `-d`, so an existing database is never dropped. `--noexec` needs `-d`. `--up-to-version 1.0.0` limits both passes to folders up through that version.

---

## Stamp IDs on hand-written files

Every migration must start with:

```sql
-- Migration-Id: YYYYMMDDHHMMSS_<8-hex>
```

`sql-mig new` and `sql-mig sync` stamp missing IDs. For files you created by hand:

```bash
sql-mig stamp path/to/file.sql
sql-mig stamp --all
sql-mig stamp --all --refresh   # keep IDs, drop legacy header lines
```

Optional: copy this repo’s `.cursor/` folder into a database project so Cursor **Agent** writes are stamped automatically. See [USING-FROM-DATABASE-PROJECTS.md](USING-FROM-DATABASE-PROJECTS.md#optional-cursor-hook).

---

## Version folders

Folders under `Deployments/Migrations/` **must** be `MAJOR.MINOR.PATCH`:

- Valid: `1.0.0`, `2.3.0`, `10.0.1`
- Invalid: `1.1`, `v1.0.0`, `1.0.0-beta`

`sync`, `check`, and `run` process versions in semver order, then files in filename order. Details and the primary-key / foreign-key layout: [Script order](#script-order).

---

## CI / MSBuild

```yaml
- run: pip install "sql-migration-tools @ git+https://github.com/nakkavamsi/DBDeploymentTool.git"
- run: sql-mig sync
- run: sql-mig run -S ${{ env.SQL_HOST }} -d MyDb -U sa -P "$SQL_PASSWORD" -C
```

```xml
<Exec Command="sql-mig sync" WorkingDirectory="$(MSBuildProjectDirectory)" />
```

---

## What stays in the database project vs this repo

**Database project:** `Deployments/`, `SchemaModel/`, `*.sqlproj`.

**This repo:** the `sql-mig` package only.

**Sample consumer:** [nakkavamsi/Databasecode](https://github.com/nakkavamsi/Databasecode). How to attach this CLI to a second database repo: [USING-FROM-DATABASE-PROJECTS.md](USING-FROM-DATABASE-PROJECTS.md).

---

## Common problems

| Symptom | What to do |
|---------|------------|
| `Unknown command` | Use `sql-mig --help`. Commands are `sync`, `run`, `new`, `stamp`, `bootstrap`, `check`. |
| Missing `sqlcmd` | Install SQL Server command-line tools / mssql-tools and retry `run`. |
| Missing Migration-Id | `sql-mig stamp --all` or `sql-mig sync`. |
| Invalid folder name | Rename to `MAJOR.MINOR.PATCH`. |
| Duplicate Migration-Id | Two files share an ID; give one a new ID with `stamp` (do not `--refresh` — that keeps the ID). |
| SchemaModel looks wrong | Edit the migration, not SchemaModel, then `sql-mig sync`. |
| Script failed mid-run | That file was not recorded. Fix it and `sql-mig run` again. |
| `references invalid table` during `check` | The foreign key file sorts before the parent table. Move the parent to an earlier filename and add the key with a later `ALTER TABLE`. |
| `Invalid column name` during `check` | The procedure or view names a column the earlier scripts did not create. Fix the column or the script order, then run the default `sql-mig check` again. |

For parser limits, history table columns, and module map, see [DOCUMENTATION.md](DOCUMENTATION.md).
