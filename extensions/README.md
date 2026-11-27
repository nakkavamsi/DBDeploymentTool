# SQL Server Migration Database — Visual Studio Extension (VSIX)

Packages the **SQL Server Database (Migration-Driven)** project template for **Visual Studio 2022** and **Visual Studio 2026**.

One VSIX installs on both. `source.extension.vsixmanifest` uses installation target `Version="[17.0,)"` (Community, Professional, and Enterprise; amd64 and arm64). Visual Studio 2022 matches the 17.x range. Visual Studio 2026 reads only that lower bound, so the same package loads there too. The SQL Server Data Tools prerequisite stays `Microsoft.VisualStudio.Component.SQL.SSDT` at `[17.0,)`, which is the component id in both products.

## Prerequisites (Windows)

- Visual Studio 2022 (17.x) or Visual Studio 2026 (18.x), amd64 or arm64
- Workload: **Data storage and processing** → SQL Server Data Tools
- Python 3.x (for pre-build template pack)
- .NET Framework 4.7.2 targeting pack (installed with VS)

The VSIX is still built with `Microsoft.VSSDK.BuildTools` 17.x. That SDK packages a Visual Studio 2022-compatible VSIX; the open install range is what makes it valid on Visual Studio 2026. No Visual Studio 2026-only APIs are referenced.

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
5. `sql-mig check -S ... -U ... -C` parses `Deployments/Migrations` and then compiles that order. Both passes run unless `--skip-parse` or `--skip-compile` stops one. `dotnet build` does not compile those scripts.

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
