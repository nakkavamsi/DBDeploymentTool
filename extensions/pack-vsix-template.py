#!/usr/bin/env python3
"""
Pack templates/SqlMigrationDatabase for Visual Studio VSIX project templates.

Replaces Databasecode -> $safeprojectname$ and project GUID -> $guid1$
for Visual Studio template parameter substitution.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SOURCE_NAME = "Databasecode"
PROJECT_GUID = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
SKIP_NAMES = {".template.config", "__pycache__"}


def should_skip(path: Path) -> bool:
    return any(part in SKIP_NAMES for part in path.parts)


def transform_content(text: str) -> str:
    text = text.replace(SOURCE_NAME, "$safeprojectname$")
    text = text.replace(PROJECT_GUID, "$guid1$")
    return text


def transform_name(name: str) -> str:
    return name.replace(SOURCE_NAME, "$safeprojectname$")


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    for item in sorted(src.rglob("*")):
        if should_skip(item):
            continue
        rel = item.relative_to(src)
        if any(part in SKIP_NAMES for part in rel.parts):
            continue

        target_name = transform_name(item.name)
        target = dst / rel.parent / target_name

        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix.lower() in {".sql", ".sqlproj", ".json", ".yml", ".md", ".sh", ".py", ".gitignore", ".gitkeep"}:
            target.write_text(transform_content(item.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(item, target)


def write_vstemplate(target_dir: Path) -> None:
    vstemplate = """<?xml version="1.0" encoding="utf-8"?>
<VSTemplate Version="3.0.0" Type="Project"
  xmlns="http://schemas.microsoft.com/developer/vstemplate/2005">
  <TemplateData>
    <Name>SQL Server Database (Migration-Driven)</Name>
    <Description>SQL Server dacpac project with semver migrations, SchemaModel sync, baseline bootstrap, Migration-Id tooling, and CI.</Description>
    <ProjectType>Database</ProjectType>
    <ProjectSubType>SQL</ProjectSubType>
    <SortOrder>1000</SortOrder>
    <CreateNewFolder>true</CreateNewFolder>
    <DefaultName>Database</DefaultName>
    <ProvideDefaultName>true</ProvideDefaultName>
    <LocationField>Enabled</LocationField>
    <EnableLocationBrowseButton>true</EnableLocationBrowseButton>
    <LanguageTag>SQL</LanguageTag>
    <PlatformTag>Windows</PlatformTag>
  </TemplateData>
  <TemplateContent>
    <Project TargetFileName="$safeprojectname$.sqlproj"
             File="$safeprojectname$.sqlproj"
             ReplaceParameters="true">
      <Folder Name="Deployments" TargetFolderName="Deployments">
        <Folder Name="Migrations" TargetFolderName="Migrations">
          <Folder Name="1.0.0" TargetFolderName="1.0.0">
            <ProjectItem ReplaceParameters="true" TargetFileName="01_dbo.Sample.sql">Deployments\\Migrations\\1.0.0\\01_dbo.Sample.sql</ProjectItem>
          </Folder>
        </Folder>
        <Folder Name="Rollback" TargetFolderName="Rollback">
          <Folder Name="FULL" TargetFolderName="FULL" />
          <Folder Name="SOFT" TargetFolderName="SOFT" />
        </Folder>
        <Folder Name="pre-deployments" TargetFolderName="pre-deployments">
          <ProjectItem ReplaceParameters="false">Deployments\\pre-deployments\\readme.txt</ProjectItem>
        </Folder>
      </Folder>
      <Folder Name="SchemaModel" TargetFolderName="SchemaModel">
        <ProjectItem ReplaceParameters="false">SchemaModel\\.gitkeep</ProjectItem>
      </Folder>
      <Folder Name=".github" TargetFolderName=".github">
        <Folder Name="workflows" TargetFolderName="workflows">
          <ProjectItem ReplaceParameters="true">.github\\workflows\\build.yml</ProjectItem>
        </Folder>
      </Folder>
      <ProjectItem ReplaceParameters="true">global.json</ProjectItem>
      <ProjectItem ReplaceParameters="true">README.md</ProjectItem>
      <ProjectItem ReplaceParameters="false">.gitignore</ProjectItem>
    </Project>
  </TemplateContent>
</VSTemplate>
"""
    (target_dir / "SqlMigrationDatabase.vstemplate").write_text(vstemplate, encoding="utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "templates" / "SqlMigrationDatabase"
    dst = repo_root / "extensions" / "SqlMigrationDatabaseVsix" / "ProjectTemplates" / "SqlMigrationDatabase"

    if not src.exists():
        print(f"ERROR: Template source not found: {src}", file=sys.stderr)
        return 1

    copy_tree(src, dst)
    write_vstemplate(dst)
    print(f"Packed VSIX template -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
