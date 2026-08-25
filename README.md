# sql-migration-tools

Shared Python CLI (`sql-mig`) for **migration-driven SQL Server database projects**.

This repo is tooling only. Schema SQL lives in each database project (`Deployments/`, `SchemaModel/`, `*.sqlproj`).

- **How to use:** [docs/HOWTO.md](docs/HOWTO.md)
- **Use from other database repos:** [docs/USING-FROM-DATABASE-PROJECTS.md](docs/USING-FROM-DATABASE-PROJECTS.md)
- **Sample database project:** [nakkavamsi/Databasecode](https://github.com/nakkavamsi/Databasecode)
- **Architecture and command internals:** [docs/DOCUMENTATION.md](docs/DOCUMENTATION.md)

## Install

```bash
pip install -e /path/to/sql-migration-tools
```

From GitHub:

```bash
pip install "sql-migration-tools @ git+https://github.com/nakkavamsi/DBDeploymentTool.git"
```

Requires Python 3.10+. `sql-mig run` also needs `sqlcmd` on `PATH`.

## Commands

Run these from the **database project** directory (not this tools repo). `--project-root` defaults to the current working directory.

| Command | Purpose |
|---------|---------|
| `sql-mig new` | Scaffold a new migration with `Migration-Id` |
| `sql-mig stamp` | Stamp missing `Migration-Id` headers |
| `sql-mig sync` | Rebuild `SchemaModel/` from `Deployments/Migrations/` |
| `sql-mig run` | Apply pending migrations via sqlcmd + history tables |
| `sql-mig bootstrap` | Split a baseline SQL export into migration files |

```bash
cd MyDatabaseProject

sql-mig new --version 2.3.0 --name dbo.person.add_status
sql-mig sync
sql-mig run -S localhost -d MyDb -U sa -C --status
sql-mig run -S localhost -d MyDb -U sa -C
sql-mig stamp --all
sql-mig bootstrap --input baseline.sql --version 1.0.0 --sync
```

```bash
sql-mig --help
sql-mig run --help
```

## Typical loop

1. `sql-mig new --version X.Y.Z --name some.change`
2. Edit the generated SQL (keep the `-- Migration-Id` header).
3. `sql-mig sync` so `SchemaModel/` matches migrations.
4. `sql-mig run -S ... -d ...` to apply pending scripts.

Migrations are the source of truth. Do not edit `SchemaModel/` by hand.

## Optional wrappers and Cursor hook

`scripts/` contains thin Python wrappers (`new-migration.py`, `stamp-migration-id.py`, `sync-schema-from-migrations.py`, `run-migrations.py`, `bootstrap-from-baseline.py`). Prefer `sql-mig`. Copy `scripts/` into a database project only if you want the old `python3 scripts/…` commands.

`.cursor/` stamps `-- Migration-Id` after **Agent** edits under `Deployments/Migrations/`. Copy that folder into each database project workspace if you want the same behavior. Details: [docs/USING-FROM-DATABASE-PROJECTS.md](docs/USING-FROM-DATABASE-PROJECTS.md#optional-cursor-hook).

## Project templates (dotnet / Visual Studio)

This repo ships scaffolding for new database projects:

- `templates/SqlMigrationDatabase` — `dotnet new sql-migration-db`
- `extensions/` — Visual Studio VSIX packaging of that template

```bash
git clone https://github.com/nakkavamsi/DBDeploymentTool.git
cd DBDeploymentTool
dotnet new install ./templates/SqlMigrationDatabase
dotnet new sql-migration-db -n MyDb -o ../MyDb
```

Details: [templates/README.md](templates/README.md), [extensions/README.md](extensions/README.md).

## Using from another database project

This repo is the CLI. Each database is a **separate** repo that installs the package and keeps its own SQL.

The sample consumer is **[Databasecode](https://github.com/nakkavamsi/Databasecode)** (migrations, `.sqlproj` sync target, CI).

Full wiring (requirements.txt, MSBuild, CI, `dotnet new` template, from-scratch checklist): [docs/USING-FROM-DATABASE-PROJECTS.md](docs/USING-FROM-DATABASE-PROJECTS.md).

```text
# in the database repo's requirements.txt
sql-migration-tools @ git+https://github.com/nakkavamsi/DBDeploymentTool.git
```

```bash
cd MyDatabaseProject
pip install -r requirements.txt
sql-mig sync
sql-mig run -S localhost -d MyDb -U sa -C
```

## Development

```bash
cd sql-migration-tools
pip install -e .
sql-mig --help
```
