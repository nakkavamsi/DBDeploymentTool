# $safeprojectname$

SQL Server database project with **migration-driven schema management**.

Shared tooling lives in the separate [`sql-migration-tools`](https://github.com/nakkavamsi/sql-migration-tools) package (`sql-mig` CLI).

## Quick start

```bash
# Install shared tooling
pip install -r requirements.txt

# Add a migration
sql-mig new --version 1.1.0 --name dbo.MyTable

# Bootstrap from an exported baseline script (brownfield adoption)
sql-mig bootstrap --input baseline.sql --version 1.0.0 --sync

# Build dacpac (sync runs automatically; this does not compile migration scripts)
dotnet build $safeprojectname$.sqlproj --configuration Release /p:NetCoreBuild=true

# Parse every migration, then compile the chain on a scratch database (requires sqlcmd)
sql-mig check -S localhost -U sa -C

# Apply pending migrations to a database (requires sqlcmd)
sql-mig run -S localhost -d MyDb -U sa -P '...' -C --status
```

## Script order and check

`sql-mig check` and `sql-mig run` use the same order: version folders from lowest `MAJOR.MINOR.PATCH` to highest, then filenames inside the folder (`01_`, `02_`, …). Put a parent table in an earlier file than the foreign key that references it. End `CREATE SCHEMA` with `GO` before objects in that schema.

`sql-mig check` keeps two passes on:

1. **Parse** — `SET PARSEONLY ON` for every file. Every syntax error is reported.
2. **Compile** — execute that order on a `sqlmig_check_*` database, then drop it. This pass catches a foreign key whose parent is still in a later file, and a procedure that names a missing column.

Stop one pass with `--skip-parse` or `--skip-compile`. `--noexec` keeps both and compiles each file with `SET NOEXEC ON` against `-d` instead of executing the chain. `dotnet build` compiles `SchemaModel/` only.

## Layout

- `Deployments/Migrations/` — source of truth (semver folders, filename order)
- `SchemaModel/` — auto-generated declarative model (do not edit)
- `requirements.txt` — installs `sql-migration-tools`
- `.github/workflows/deploy-migrations.yml` — parses and compiles migrations, then applies them

See the full documentation in the source template repository README.
