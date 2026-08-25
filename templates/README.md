# Visual Studio / dotnet project templates

Reusable template for creating new **migration-driven SQL Server database** repositories.

## What you get

- `Databasecode.sqlproj` (Microsoft.Build.Sql 2.2.0)
- `Deployments/Migrations/` + `Deployments/Rollback/`
- `sql-mig sync` → `SchemaModel/` → `.dacpac`
- Migration-Id scaffolding (`sql-mig new`, auto-stamp on build)
- GitHub Actions CI

---

## Install the template

### Option A — From this repository (recommended)

```bash
cd /path/to/DBDeploymentTool
dotnet new install ./templates/SqlMigrationDatabase
```

### Option B — After cloning only the template folder

Copy `templates/SqlMigrationDatabase` to any location, then:

```bash
dotnet new install /path/to/SqlMigrationDatabase
```

### Uninstall / update

```bash
dotnet new uninstall Databasecode.SqlMigrationDatabase
dotnet new install ./templates/SqlMigrationDatabase   # reinstall after changes
```

---

## Create a new database project

### Command line

```bash
dotnet new sql-migration-db -n MyCustomerDb -o ../MyCustomerDb
cd ../MyCustomerDb
dotnet build MyCustomerDb.sqlproj --configuration Release /p:NetCoreBuild=true
```

This creates:

```
MyCustomerDb/
├── MyCustomerDb.sqlproj
├── Deployments/Migrations/1.0.0/01_dbo.Sample.sql
├── SchemaModel/          (populated on first build)
└── .github/workflows/build.yml
```

### Visual Studio 2022

1. Install the template (`dotnet new install ...` above)
2. **Restart Visual Studio** (required to refresh templates)
3. **Create a new project**
4. Search: **SQL Server Database (Migration-Driven)** or `sql-migration-db`
5. Set project name and location → Create
6. Build solution (requires **Python 3** and **.NET SDK 8.0.406** on PATH)

> Visual Studio discovers templates installed via `dotnet new install`. No separate VSIX is required for basic use.

---

## Visual Studio extension (VSIX) — optional

For team-wide distribution inside an organization, you can package the template as a **VSIX**:

1. Install [Visual Studio SDK](https://docs.microsoft.com/en-us/visualstudio/extensibility/)
2. Create a **VSIX Project** with a **Project Template** item
3. Point the template archive at `templates/SqlMigrationDatabase`
4. Distribute the `.vsix` file; developers run **Extensions → Install from file**

The `dotnet new install` approach is simpler and works in VS, VS Code, and CI without a VSIX.

---

## Visual Studio VSIX extension (this repo)

A full **VSIX** project is included under `extensions/SqlMigrationDatabaseVsix/`.

### Build on Windows

```powershell
python extensions\pack-vsix-template.py
msbuild extensions\SqlMigrationDatabaseVsix.sln /p:Configuration=Release
```

Install `extensions\SqlMigrationDatabaseVsix\bin\Release\SqlMigrationDatabaseVsix.vsix`, restart VS, then **Create a new project** → **SQL Server Database (Migration-Driven)**.

CI builds the VSIX on `windows-latest` — see `.github/workflows/build-vsix.yml`.

See [extensions/README.md](../extensions/README.md) for full details.

---

## Prerequisites for every new repo

| Tool | Version |
|------|---------|
| .NET SDK | 8.0.406 (`global.json`) |
| Python | 3.x |
| SQL workload | VS 2022: *Data storage and processing* (optional, for publish UI) |

---

## Customize the template

Edit files under `templates/SqlMigrationDatabase/`, then:

```bash
dotnet new uninstall Databasecode.SqlMigrationDatabase
dotnet new install ./templates/SqlMigrationDatabase
```

Key replacement token: `Databasecode` → replaced with your project name (`sourceName` in `template.json`).
