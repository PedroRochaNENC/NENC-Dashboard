"""Move one Prosodia project, its data and its external resources to another organization.

Dry-run by default. Run from the application root:
    py scripts/move_project_organization.py --database path/to/prosodia.db \
        --api-project-id 5 --organization Smartfit --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

APP_ROOT = Path(__file__).resolve().parent.parent

# Tabelas ligadas ao projeto pelo proprio project_id.
PROJECT_OWNED_TABLES = ("project_analyses",)
# Tabelas ligadas ao projeto atraves dos audios dele.
AUDIO_OWNED_TABLES = ("analyses", "quality_checks", "high_activations")


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _existing_tables(database: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _resolve_organization(database: sqlite3.Connection, reference: str) -> sqlite3.Row:
    if reference.isdigit():
        row = database.execute(
            "SELECT id, name, is_active FROM organizations WHERE id = ?",
            (int(reference),),
        ).fetchone()
    else:
        rows = database.execute(
            "SELECT id, name, is_active FROM organizations WHERE name = ? COLLATE NOCASE",
            (reference,),
        ).fetchall()
        if len(rows) > 1:
            raise LookupError(
                "Mais de uma organizacao chamada {}. Use o id.".format(reference)
            )
        row = rows[0] if rows else None
    if row is None:
        raise LookupError("Organizacao nao encontrada: {}.".format(reference))
    return row


def _resolve_project(
    database: sqlite3.Connection, arguments: argparse.Namespace
) -> sqlite3.Row:
    if arguments.project_id is not None:
        clause, values = "p.id = ?", (arguments.project_id,)
        reference = "id {}".format(arguments.project_id)
    elif arguments.api_project_id is not None:
        clause, values = "p.api_project_id = ?", (arguments.api_project_id,)
        reference = "API #{}".format(arguments.api_project_id)
    else:
        clause, values = "p.name = ? COLLATE NOCASE", (arguments.project_name,)
        reference = "nome '{}'".format(arguments.project_name)

    rows = database.execute(
        """
        SELECT p.*, o.name AS organization_name
        FROM projects p
        LEFT JOIN organizations o ON o.id = p.organization_id
        WHERE {}
        ORDER BY p.id
        """.format(clause),
        values,
    ).fetchall()
    if not rows:
        raise LookupError("Nenhum projeto encontrado por {}.".format(reference))
    if len(rows) > 1:
        found = ", ".join("{} ({})".format(row["id"], row["name"]) for row in rows)
        raise LookupError(
            "Mais de um projeto encontrado por {}: {}. Use --project-id.".format(
                reference, found
            )
        )
    return rows[0]


def _decode_metadata(raw_metadata: Optional[str]) -> dict[str, Any]:
    if not raw_metadata:
        return {}
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _collect_external_resources(
    database: sqlite3.Connection, project: sqlite3.Row
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Return (resources to move, resources skipped because another organization owns them).

    Comeca pelos recursos que apontam para o projeto e desce pela cadeia de
    derivacao gravada em `parent_resource_type`/`parent_resource_id`.
    """

    if "organization_external_resources" not in _existing_tables(database):
        return [], []

    all_resources = database.execute(
        "SELECT * FROM organization_external_resources"
    ).fetchall()
    source_organization_id = int(project["organization_id"])

    seeds: set[tuple[str, str]] = set()
    if project["api_project_id"] is not None:
        seeds.add(("whatsapp_api_project", str(project["api_project_id"])))
    if project["whatsapp_campaign_id"] is not None:
        seeds.add(("whatsapp_campaign", str(project["whatsapp_campaign_id"])))
    for resource in all_resources:
        metadata = _decode_metadata(resource["metadata_json"])
        if "project_id" in metadata and str(metadata["project_id"]) == str(project["id"]):
            seeds.add((resource["resource_type"], resource["resource_id"]))

    by_key = {
        (resource["resource_type"], resource["resource_id"]): resource
        for resource in all_resources
    }
    children: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for resource in all_resources:
        metadata = _decode_metadata(resource["metadata_json"])
        parent_type = metadata.get("parent_resource_type")
        parent_id = metadata.get("parent_resource_id")
        if parent_type is None or parent_id is None:
            continue
        children.setdefault((str(parent_type), str(parent_id)), []).append(resource)

    selected: dict[tuple[str, str], sqlite3.Row] = {}
    skipped: dict[tuple[str, str], sqlite3.Row] = {}
    frontier = set(seeds)
    while frontier:
        next_frontier: set[tuple[str, str]] = set()
        for key in frontier:
            if key in selected or key in skipped:
                continue
            resource = by_key.get(key)
            if resource is None:
                continue
            if int(resource["organization_id"]) != source_organization_id:
                skipped[key] = resource
                continue
            selected[key] = resource
            for child in children.get(key, []):
                next_frontier.add((child["resource_type"], child["resource_id"]))
        frontier = next_frontier - set(selected) - set(skipped)

    ordered = sorted(
        selected.values(), key=lambda row: (row["resource_type"], row["resource_id"])
    )
    return ordered, list(skipped.values())


def _audio_ids(database: sqlite3.Connection, project_id: int) -> list[int]:
    return [
        row[0]
        for row in database.execute(
            "SELECT id FROM audios WHERE project_id = ?", (project_id,)
        )
    ]


def _count(database: sqlite3.Connection, statement: str, values: tuple) -> int:
    return database.execute(statement, values).fetchone()[0]


def _report_vector_stores(
    database: sqlite3.Connection,
    source_organization_id: int,
    target_organization_id: int,
) -> None:
    if "organization_vector_stores" not in _existing_tables(database):
        return
    stores = {
        int(row["organization_id"]): row["vector_store_id"]
        for row in database.execute(
            """
            SELECT organization_id, vector_store_id
            FROM organization_vector_stores
            WHERE module_key = 'prosodia' AND organization_id IN (?, ?)
            """,
            (source_organization_id, target_organization_id),
        )
    }
    source_store = stores.get(source_organization_id)
    target_store = stores.get(target_organization_id)
    if source_store and source_store != target_store:
        print(
            "  AVISO: a base de conhecimento das entrevistas ficou em {} (organizacao {}). "
            "A organizacao de destino usa {}, entao as analises com citacao exigem "
            "reenvio dos arquivos.".format(
                source_store, source_organization_id, target_store or "nenhuma base"
            )
        )


def _report_plan(
    database: sqlite3.Connection,
    project: sqlite3.Row,
    target_organization: sqlite3.Row,
    audio_ids: list[int],
    resources: list[sqlite3.Row],
    skipped_resources: list[sqlite3.Row],
    tables: set[str],
    new_name: Optional[str],
) -> None:
    print("Projeto: {} (id {})".format(project["name"], project["id"]))
    if new_name:
        print("  Novo nome: {}".format(new_name))
    print(
        "  Organizacao: {} (id {})  ->  {} (id {})".format(
            project["organization_name"],
            project["organization_id"],
            target_organization["name"],
            target_organization["id"],
        )
    )
    if project["api_project_id"] is not None:
        print("  Projeto na WhatsApp API: #{}".format(project["api_project_id"]))
    print("  audios: {}".format(len(audio_ids)))

    placeholders = ",".join("?" for _ in audio_ids)
    for table_name in AUDIO_OWNED_TABLES:
        if table_name not in tables:
            continue
        total = (
            _count(
                database,
                "SELECT COUNT(*) FROM {} WHERE audio_id IN ({})".format(
                    table_name, placeholders
                ),
                tuple(audio_ids),
            )
            if audio_ids
            else 0
        )
        print("  {}: {}".format(table_name, total))
    for table_name in PROJECT_OWNED_TABLES:
        if table_name not in tables:
            continue
        total = _count(
            database,
            "SELECT COUNT(*) FROM {} WHERE project_id = ?".format(table_name),
            (project["id"],),
        )
        print("  {}: {}".format(table_name, total))

    print("  recursos externos: {}".format(len(resources)))
    for resource in resources:
        print("    {} #{}".format(resource["resource_type"], resource["resource_id"]))
    for resource in skipped_resources:
        print(
            "  AVISO: {} #{} pertence a organizacao {} e nao sera movido.".format(
                resource["resource_type"],
                resource["resource_id"],
                resource["organization_id"],
            )
        )

    if project["api_project_id"] is not None:
        known = {
            (resource["resource_type"], resource["resource_id"])
            for resource in list(resources) + list(skipped_resources)
        }
        if ("whatsapp_api_project", str(project["api_project_id"])) not in known:
            print(
                "  AVISO: o projeto #{} da WhatsApp API nao esta registrado em "
                "organization_external_resources. Depois da troca, um administrador "
                "global precisa registra-lo para a organizacao de destino na tela "
                "Configuracao da WhatsApp API.".format(project["api_project_id"])
            )

    _report_vector_stores(
        database, int(project["organization_id"]), int(target_organization["id"])
    )


def _backup_database(database_path: Path, backup_directory: Path) -> Path:
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_directory / "{}_before_project_move_{}{}".format(
        database_path.stem, timestamp, database_path.suffix
    )
    shutil.copy2(database_path, backup_path)
    return backup_path


def _apply_move(
    database: sqlite3.Connection,
    project: sqlite3.Row,
    target_organization: sqlite3.Row,
    audio_ids: list[int],
    resources: list[sqlite3.Row],
    tables: set[str],
    new_name: Optional[str],
) -> None:
    project_id = project["id"]
    target_organization_id = target_organization["id"]
    now = _timestamp()

    if new_name:
        database.execute(
            "UPDATE projects SET organization_id = ?, name = ? WHERE id = ?",
            (target_organization_id, new_name, project_id),
        )
    else:
        database.execute(
            "UPDATE projects SET organization_id = ? WHERE id = ?",
            (target_organization_id, project_id),
        )
    database.execute(
        "UPDATE audios SET organization_id = ? WHERE project_id = ?",
        (target_organization_id, project_id),
    )

    if audio_ids:
        placeholders = ",".join("?" for _ in audio_ids)
        for table_name in AUDIO_OWNED_TABLES:
            if table_name not in tables:
                continue
            database.execute(
                "UPDATE {} SET organization_id = ? WHERE audio_id IN ({})".format(
                    table_name, placeholders
                ),
                (target_organization_id, *audio_ids),
            )
    for table_name in PROJECT_OWNED_TABLES:
        if table_name not in tables:
            continue
        database.execute(
            "UPDATE {} SET organization_id = ? WHERE project_id = ?".format(table_name),
            (target_organization_id, project_id),
        )

    for resource in resources:
        database.execute(
            """
            UPDATE organization_external_resources
            SET organization_id = ?, updated_at = ?
            WHERE resource_type = ? AND resource_id = ?
            """,
            (
                target_organization_id,
                now,
                resource["resource_type"],
                resource["resource_id"],
            ),
        )

    if "audit_log" in tables:
        database.execute(
            """
            INSERT INTO audit_log (
                organization_id, actor_user_id, action, target_type, target_id,
                metadata_json, created_at
            ) VALUES (?, NULL, ?, 'project', ?, ?, ?)
            """,
            (
                target_organization_id,
                "prosodia.project.move_organization",
                str(project_id),
                json.dumps(
                    {
                        "from_organization_id": project["organization_id"],
                        "to_organization_id": target_organization_id,
                        "previous_name": project["name"],
                        "name": new_name or project["name"],
                        "audios": len(audio_ids),
                        "external_resources": [
                            "{}:{}".format(
                                resource["resource_type"], resource["resource_id"]
                            )
                            for resource in resources
                        ],
                        "operator": "scripts/move_project_organization.py",
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                now,
            ),
        )


def _verify(
    database: sqlite3.Connection,
    project_id: int,
    target_organization_id: int,
    tables: set[str],
) -> list[str]:
    problems: list[str] = []
    row = database.execute(
        "SELECT organization_id FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None or int(row[0]) != int(target_organization_id):
        problems.append("O projeto nao ficou com a organizacao de destino.")

    stranded = _count(
        database,
        "SELECT COUNT(*) FROM audios WHERE project_id = ? AND organization_id <> ?",
        (project_id, target_organization_id),
    )
    if stranded:
        problems.append("{} audio(s) continuam na organizacao antiga.".format(stranded))

    audio_ids = _audio_ids(database, project_id)
    if audio_ids:
        placeholders = ",".join("?" for _ in audio_ids)
        for table_name in AUDIO_OWNED_TABLES:
            if table_name not in tables:
                continue
            stranded = _count(
                database,
                "SELECT COUNT(*) FROM {} WHERE audio_id IN ({}) AND organization_id <> ?".format(
                    table_name, placeholders
                ),
                (*audio_ids, target_organization_id),
            )
            if stranded:
                problems.append(
                    "{} registro(s) de {} continuam na organizacao antiga.".format(
                        stranded, table_name
                    )
                )
    for table_name in PROJECT_OWNED_TABLES:
        if table_name not in tables:
            continue
        stranded = _count(
            database,
            "SELECT COUNT(*) FROM {} WHERE project_id = ? AND organization_id <> ?".format(
                table_name
            ),
            (project_id, target_organization_id),
        )
        if stranded:
            problems.append(
                "{} registro(s) de {} continuam na organizacao antiga.".format(
                    stranded, table_name
                )
            )
    return problems


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move a Prosodia project and its data to another organization."
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database to inspect or change.",
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--project-id", type=int, help="Project id in the projects table.")
    selector.add_argument(
        "--api-project-id",
        type=int,
        help="WhatsApp API project id shown as 'API #N' in the project list.",
    )
    selector.add_argument("--project-name", help="Exact project name.")
    parser.add_argument(
        "--organization",
        required=True,
        help="Target organization: numeric id or exact name.",
    )
    parser.add_argument(
        "--rename", help="Optional new project name applied in the same operation."
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=APP_ROOT / "backups",
        help="Directory for the pre-move SQLite backup.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the move. Without this flag, only a dry-run is performed.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    database_path = arguments.database.expanduser().resolve()
    if not database_path.is_file():
        print("Database not found: {}".format(database_path), file=sys.stderr)
        return 2

    database = sqlite3.connect(str(database_path))
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    try:
        tables = _existing_tables(database)
        for required in ("organizations", "projects", "audios"):
            if required not in tables:
                print("A tabela {} nao existe neste banco.".format(required), file=sys.stderr)
                return 2

        try:
            target_organization = _resolve_organization(database, arguments.organization)
            project = _resolve_project(database, arguments)
        except LookupError as error:
            print(str(error), file=sys.stderr)
            return 2

        if not target_organization["is_active"]:
            print("A organizacao de destino esta inativa.", file=sys.stderr)
            return 2
        if int(project["organization_id"]) == int(target_organization["id"]):
            print(
                "O projeto {} (id {}) ja pertence a organizacao {}. Nada a fazer.".format(
                    project["name"], project["id"], target_organization["name"]
                )
            )
            return 0

        audio_ids = _audio_ids(database, project["id"])
        resources, skipped_resources = _collect_external_resources(database, project)
        _report_plan(
            database,
            project,
            target_organization,
            audio_ids,
            resources,
            skipped_resources,
            tables,
            arguments.rename,
        )

        if not arguments.apply:
            print(
                "\nDry-run concluido. Repita com --apply para criar o backup e mover o projeto."
            )
            return 0

        backup_path = _backup_database(database_path, arguments.backup_dir.expanduser())
        print("\nBackup criado: {}".format(backup_path))

        with database:
            _apply_move(
                database,
                project,
                target_organization,
                audio_ids,
                resources,
                tables,
                arguments.rename,
            )

        problems = _verify(database, project["id"], target_organization["id"], tables)
    finally:
        database.close()

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "Verificacao falhou. Restaure {} antes de tentar de novo.".format(backup_path),
            file=sys.stderr,
        )
        return 1

    print(
        "Projeto movido e verificado. Reinicie as sessoes abertas para que a troca "
        "apareca na interface."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
