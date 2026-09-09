"""Encontra e remove o que sobrou na base de conhecimento sem dono no banco.

Ate a Fase 2 nada era apagado da OpenAI quando um projeto ou audio saia do
banco: transcricao de entrevista — dado pessoal — continuava indexada e
citavel. Este script recolhe esse passivo, e serve de rede para o que a
limpeza em cascata nao conseguir apagar na hora (OpenAI fora do ar, por exemplo).

Dry-run por padrao. A partir da raiz da aplicacao:
    py scripts/cleanup_orphan_kb_files.py --database data/nenc-insights.db
    py scripts/cleanup_orphan_kb_files.py --database data/nenc-insights.db --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

APP_ROOT = Path(__file__).resolve().parent.parent


def _connect(database_path: Path) -> sqlite3.Connection:
    database = sqlite3.connect(database_path)
    database.row_factory = sqlite3.Row
    return database


def _openai_client() -> OpenAI:
    for candidate in (APP_ROOT / ".env", APP_ROOT.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            break
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key == "sk-proj-YOUR_KEY_HERE":
        raise SystemExit("OPENAI_API_KEY nao configurada: nada a fazer.")
    return OpenAI(api_key=api_key)


def _tabelas(database: sqlite3.Connection) -> set:
    return {
        row[0]
        for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _projetos_vivos(database: sqlite3.Connection, organization_id: int) -> set:
    return {
        row["id"]
        for row in database.execute(
            "SELECT id FROM projects WHERE organization_id = ?", (organization_id,)
        )
    }


def _sessoes_vivas(database: sqlite3.Connection, organization_id: int) -> set:
    return {
        (row["project_id"], str(row["session_id"]))
        for row in database.execute(
            """
            SELECT a.project_id, a.session_id
            FROM audios a
            JOIN projects p ON p.id = a.project_id
            WHERE p.organization_id = ?
            """,
            (organization_id,),
        )
    }


def _orfaos_do_store(
    client: OpenAI,
    vector_store_id: str,
    projetos: set,
    sessoes: set,
) -> list:
    """Documentos de projeto cujo projeto ou sessao nao existe mais.

    Literatura nunca entra nesta lista: ela nao pertence a projeto nenhum, e
    apagar por falta de dono tiraria referencia de quem nao pediu.
    """

    orfaos = []
    for arquivo in client.vector_stores.files.list(vector_store_id=vector_store_id):
        atributos = getattr(arquivo, "attributes", None) or {}
        escopo = atributos.get("escopo")
        if not escopo:
            orfaos.append((arquivo.id, "sem atributo (rode o backfill primeiro)", False))
            continue
        if escopo == "referencia":
            continue

        project_id = atributos.get("project_id")
        if not project_id:
            orfaos.append((arquivo.id, "material de projeto sem project_id", True))
            continue
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            orfaos.append((arquivo.id, "project_id invalido", True))
            continue

        if project_id not in projetos:
            orfaos.append((arquivo.id, "projeto {} nao existe mais".format(project_id), True))
            continue

        session_id = atributos.get("session_id")
        if session_id and (project_id, str(session_id)) not in sessoes:
            orfaos.append(
                (arquivo.id, "sessao {} nao existe mais".format(session_id), True)
            )
    return orfaos


def _stores_sem_dono(client: OpenAI, conhecidos: set) -> list:
    """Vector stores na conta que o banco nao referencia mais.

    O Teste Sensorial cria um store por projeto; ate a Fase 2, apagar o projeto
    deixava o store na conta, pago e invisivel.
    """

    sobrando = []
    try:
        for store in client.vector_stores.list(limit=100):
            if store.id not in conhecidos:
                sobrando.append((store.id, getattr(store, "name", "") or ""))
    except Exception as error:
        print("  falha ao listar vector stores: {}".format(error), file=sys.stderr)
    return sobrando


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove documentos da base de conhecimento sem dono no banco."
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database that owns projects, audios and vector store ids.",
    )
    parser.add_argument(
        "--apagar-stores",
        action="store_true",
        help="Junto com --apply, apaga tambem os vector stores sem dono.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apaga de verdade. Sem esta flag, apenas lista o que seria apagado.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    database_path = arguments.database.expanduser().resolve()
    if not database_path.is_file():
        print("Database not found: {}".format(database_path), file=sys.stderr)
        return 1

    client = _openai_client()
    database = _connect(database_path)
    total_arquivos = 0
    apagados = 0
    try:
        tabelas = _tabelas(database)
        conhecidos = set()
        stores = list(
            database.execute(
                """
                SELECT organization_id, module_key, vector_store_id
                FROM organization_vector_stores
                ORDER BY organization_id, module_key
                """
            )
        )
        conhecidos.update(row["vector_store_id"] for row in stores)
        if "ts_projects" in tabelas:
            conhecidos.update(
                row["vector_store_id"]
                for row in database.execute(
                    "SELECT vector_store_id FROM ts_projects WHERE vector_store_id IS NOT NULL"
                )
            )

        for row in stores:
            organization_id = row["organization_id"]
            vector_store_id = row["vector_store_id"]
            print(
                "\n=== organizacao {} · modulo {} · {}".format(
                    organization_id, row["module_key"], vector_store_id
                )
            )
            projetos = _projetos_vivos(database, organization_id)
            sessoes = _sessoes_vivas(database, organization_id)
            try:
                orfaos = _orfaos_do_store(client, vector_store_id, projetos, sessoes)
            except Exception as error:
                print("  falha ao listar: {}".format(error), file=sys.stderr)
                continue

            if not orfaos:
                print("  nada a remover.")
            for file_id, motivo, removivel in orfaos:
                total_arquivos += 1
                print(
                    "  {} · {}{}".format(
                        file_id, motivo, "" if removivel else " · deixado como esta"
                    )
                )
                if arguments.apply and removivel:
                    try:
                        client.vector_stores.files.delete(
                            vector_store_id=vector_store_id, file_id=file_id
                        )
                    except Exception:
                        pass
                    try:
                        client.files.delete(file_id)
                        apagados += 1
                    except Exception as error:
                        print("    falha: {}".format(error), file=sys.stderr)

        print("\n=== vector stores sem dono no banco")
        sobrando = _stores_sem_dono(client, conhecidos)
        if not sobrando:
            print("  nenhum.")
        for store_id, nome in sobrando:
            print("  {} · {}".format(store_id, nome))
            if arguments.apply and arguments.apagar_stores:
                try:
                    client.vector_stores.delete(store_id)
                    print("    apagado.")
                except Exception as error:
                    print("    falha: {}".format(error), file=sys.stderr)
    finally:
        database.close()

    print(
        "\nResumo: {} arquivo(s) listado(s), {} apagado(s).".format(
            total_arquivos, apagados
        )
    )
    if not arguments.apply:
        print("Dry-run concluido. Repita com --apply para apagar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
