# sql-migration-tools

Shared Python tooling for **migration-driven SQL Server database projects** (Databasecode-style).

Install once, use from every database repo:

```bash
pip install -e /path/to/sql-migration-tools
# or after publishing to GitHub:
pip install "sql-migration-tools @ git+https://github.com/<org>/sql-migration-tools.git"
```

## Commands

| Command | Purpose |
|---------|---------|
| `sql-mig sync` | Sync `SchemaModel/` from `Deployments/Migrations/` |
| `sql-mig run` | Apply pending migrations via sqlcmd + history tables |
| `sql-mig new` | Scaffold a new migration with `Migration-Id` |
| `sql-mig stamp` | Stamp missing `Migration-Id` headers |
| `sql-mig bootstrap` | Split a baseline SQL export into migration files |

All commands default `--project-root` to the **current working directory** (your database project).

## Examples

```bash
cd MyDatabaseProject

sql-mig sync
sql-mig new --version 2.3.0 --name dbo.person.add_status
sql-mig run -S localhost -d MyDb -U sa -C --status
sql-mig stamp --all
sql-mig bootstrap --input baseline.sql --version 1.0.0 --sync
```

## Using from a database project

1. Add a dependency (local path while developing):

```text
# requirements.txt
sql-migration-tools @ file:///Users/you/sql-migration-tools
```

2. Or install from Git once the tools repo is published.

3. Call from MSBuild / CI:

```xml
<Exec Command="sql-mig sync" WorkingDirectory="$(MSBuildProjectDirectory)" />
```

```yaml
- run: pip install "sql-migration-tools @ git+https://github.com/<org>/sql-migration-tools.git"
- run: sql-mig sync
- run: sql-mig run -S localhost -d MyDb -U sa -C
```

## What stays in each database project

- `Deployments/Migrations/`
- `Deployments/Rollback/`
- `Deployments/pre-deployments/`
- `SchemaModel/`
- `*.sqlproj`

This package does **not** contain schema SQL — only the tooling.

## Development

```bash
cd sql-migration-tools
pip install -e .
sql-mig --help
```
