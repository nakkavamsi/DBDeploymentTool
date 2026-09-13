# SQL Server Migration Database — Visual Studio Extension (VSIX)

Packages the **SQL Server Database (Migration-Driven)** project template for Visual Studio 2022.

## Prerequisites (Windows)

- Visual Studio 2022 (17.x)
- Workload: **Data storage and processing** → SQL Server Data Tools
- Python 3.x (for pre-build template pack)
- .NET Framework 4.7.2 targeting pack (installed with VS)

## Build the VSIX locally

```powershell
# From repository root
python extensions\pack-vsix-template.py

cd extensions
msbuild SqlMigrationDatabaseVsix.sln /p:Configuration=Release
```

Output:

```
extensions/SqlMigrationDatabaseVsix/bin/Release/SqlMigrationDatabaseVsix.vsix
```

## Install the VSIX

1. Double-click `SqlMigrationDatabaseVsix.vsix`, or
2. Visual Studio → **Extensions** → **Manage Extensions** → **...** → **Install from file**

Restart Visual Studio.

## Create a project from the template

1. **File → New → Project**
2. Search: **SQL Server Database (Migration-Driven)**
3. Name the project → Create
4. Build (requires Python on PATH for schema sync)

## Update the template

After changing `templates/SqlMigrationDatabase/`:

```powershell
python extensions\pack-vsix-template.py
msbuild extensions\SqlMigrationDatabaseVsix.sln /p:Configuration=Release
```

Bump `Version` in `source.extension.vsixmanifest` before redistributing.

## CI

GitHub Actions workflow `.github/workflows/build-vsix.yml` builds the VSIX on `windows-latest` when `templates/` or `extensions/` change.

## Also available: dotnet template

```bash
dotnet new install ./templates/SqlMigrationDatabase
dotnet new sql-migration-db -n MyDb -o ../MyDb
```

Works in VS Code, Cursor, and CLI without the VSIX.
