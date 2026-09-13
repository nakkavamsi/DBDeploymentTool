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

4. Check what would run, then apply:

```bash
sql-mig run -S localhost -d MyDb -U sa -C --status
sql-mig run -S localhost -d MyDb -U sa -C
```

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

`sync` and `run` process versions in semver order, then files in filename order. `sql-mig new` prefixes `01_`, `02_`, … so order stays stable.

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
| `Unknown command` | Use `sql-mig --help`. Commands are `sync`, `run`, `new`, `stamp`, `bootstrap`. |
| Missing `sqlcmd` | Install SQL Server command-line tools / mssql-tools and retry `run`. |
| Missing Migration-Id | `sql-mig stamp --all` or `sql-mig sync`. |
| Invalid folder name | Rename to `MAJOR.MINOR.PATCH`. |
| Duplicate Migration-Id | Two files share an ID; give one a new ID with `stamp` (do not `--refresh` — that keeps the ID). |
| SchemaModel looks wrong | Edit the migration, not SchemaModel, then `sql-mig sync`. |
| Script failed mid-run | That file was not recorded. Fix it and `sql-mig run` again. |

For parser limits, history table columns, and module map, see [DOCUMENTATION.md](DOCUMENTATION.md).
