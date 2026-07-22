"""Backup, validate, and apply the explicit legacy organization migration.

Run from the application root:
    py scripts/migrate_legacy_data.py --database path/to/nenc-insights.db \
        --organization-id 123 --apply
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from utils import prosodia_db

OWNED_TABLES = (
    "projects",
    "audios",
    "analyses",
    "quality_checks",
    "project_analyses",
    "high_activations",
)


def _existing_tables(database: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _table_columns(database: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in database.execute("PRAGMA table_info({})".format(table_name))
    }


def _counts_by_organization(
    database: sqlite3.Connection, tables: Iterable[str]
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for table_name in tables:
        columns = _table_columns(database, table_name)
        if "organization_id" not in columns:
            counts[table_name] = {"legacy_without_column": 1}
            continue
        missing = database.execute(
            "SELECT COUNT(*) FROM {} WHERE organization_id IS NULL".format(table_name)
        ).fetchone()[0]
        invalid = database.execute(
            """
            SELECT COUNT(*)
            FROM {table_name}
            WHERE organization_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM organizations
                  WHERE organizations.id = {table_name}.organization_id
              )
            """.format(table_name=table_name)
        ).fetchone()[0]
        counts[table_name] = {"without_organization": missing, "invalid": invalid}
    return counts


def _print_counts(label: str, counts: dict[str, dict[str, int]]) -> None:
    print(label)
    for table_name, table_counts in counts.items():
        details = ", ".join(
            "{}={}".format(key, value) for key, value in table_counts.items()
        )
        print("  {}: {}".format(table_name, details))


def _backup_database(database_path: Path, backup_directory: Path) -> Path:
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_directory / "{}_before_tenant_migration_{}{}".format(
        database_path.stem, timestamp, database_path.suffix
    )
    shutil.copy2(database_path, backup_path)
    return backup_path


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and explicitly migrate legacy Prosodia data to one organization."
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database to validate or migrate.",
    )
    parser.add_argument(
        "--organization-id",
        type=int,
        required=True,
        help="Existing organization that owns every legacy record.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=APP_ROOT / "backups",
        help="Directory for the pre-migration SQLite backup.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration. Without this flag, only a dry-run is performed.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    database_path = arguments.database.expanduser().resolve()
    if not database_path.is_file():
        print("Database not found: {}".format(database_path), file=sys.stderr)
        return 2

    with sqlite3.connect(database_path) as database:
        tables = _existing_tables(database)
        if "organizations" not in tables:
            print(
                "The organizations table is missing. Run the application bootstrap first.",
                file=sys.stderr,
            )
            return 2
        organization = database.execute(
            "SELECT id, name, is_active FROM organizations WHERE id = ?",
            (arguments.organization_id,),
        ).fetchone()
        if organization is None:
            print(
                "Organization {} does not exist in this database.".format(
                    arguments.organization_id
                ),
                file=sys.stderr,
            )
            return 2
        if not organization[2]:
            print("The target organization is inactive.", file=sys.stderr)
            return 2
        owned_tables = tuple(table for table in OWNED_TABLES if table in tables)
        _print_counts("Pre-migration ownership check:", _counts_by_organization(database, owned_tables))

    if not arguments.apply:
        print("Dry-run complete. Re-run with --apply to create a backup and migrate.")
        return 0

    backup_path = _backup_database(database_path, arguments.backup_dir.expanduser())
    print("Backup created: {}".format(backup_path))

    previous_database_path = os.environ.get("NENC_DB_PATH")
    previous_legacy_organization_id = os.environ.get("NENC_LEGACY_ORGANIZATION_ID")
    os.environ["NENC_DB_PATH"] = str(database_path)
    os.environ["NENC_LEGACY_ORGANIZATION_ID"] = str(arguments.organization_id)
    try:
        prosodia_db.init_db()
    finally:
        if previous_database_path is None:
            os.environ.pop("NENC_DB_PATH", None)
        else:
            os.environ["NENC_DB_PATH"] = previous_database_path
        if previous_legacy_organization_id is None:
            os.environ.pop("NENC_LEGACY_ORGANIZATION_ID", None)
        else:
            os.environ["NENC_LEGACY_ORGANIZATION_ID"] = previous_legacy_organization_id

    with sqlite3.connect(database_path) as database:
        counts = _counts_by_organization(database, OWNED_TABLES)
    _print_counts("Post-migration ownership check:", counts)
    remaining_issues = any(
        count
        for table_counts in counts.values()
        for key, count in table_counts.items()
        if key in {"without_organization", "invalid", "legacy_without_column"}
    )
    if remaining_issues:
        print("Migration verification failed. Restore the backup before retrying.", file=sys.stderr)
        return 1

    print("Migration completed and verified. Remove NENC_LEGACY_ORGANIZATION_ID from the server environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
