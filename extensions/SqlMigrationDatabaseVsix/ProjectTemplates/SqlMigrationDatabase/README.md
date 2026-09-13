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

# Build dacpac (sync runs automatically)
dotnet build $safeprojectname$.sqlproj --configuration Release /p:NetCoreBuild=true

# Apply pending migrations to a database (requires sqlcmd)
sql-mig run -S localhost -d MyDb -U sa -P '...' -C --status
```

## Layout

- `Deployments/Migrations/` — source of truth (semver folders)
- `SchemaModel/` — auto-generated declarative model (do not edit)
- `requirements.txt` — installs `sql-migration-tools`

See the full documentation in the source template repository README.
