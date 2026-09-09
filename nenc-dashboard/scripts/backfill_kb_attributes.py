"""Carimba os atributos de escopo nos documentos que ja estao na base de conhecimento.

Sem atributo, um documento nao casa com nenhum filtro e some da busca. Como a
analise passou a filtrar por projeto (utils/kb_attributes.py), este backfill
precisa rodar — e ser conferido — antes de o filtro chegar em producao.

Dry-run por padrao. A partir da raiz da aplicacao:
    py scripts/backfill_kb_attributes.py --database data/nenc-insights.db
    py scripts/backfill_kb_attributes.py --database data/nenc-insights.db --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

APP_ROOT = Path(__file__).resolve().parent.parent

# Nomes que a aplicacao gera ao mandar material de projeto para a base.
PREFIXOS_DE_SESSAO = (
    ("Prosodia-", "prosodia", "projeto"),
    ("Transcricao-", "transcricao", "projeto"),
    ("qualidade_entrevista_", "qualidade", "analise"),
    ("analise_ia_", "analise_ia", "analise"),
)
PREFIXOS_DE_PROJETO = (
    ("analise_geral_", "analise_geral", "analise"),
    ("briefing_", "briefing", "projeto"),
)


def _slug(text: object) -> str:
    """Mesma normalizacao que as telas aplicam ao montar o nome do arquivo."""

    return "".join(
        character if character.isalnum() or character in ("-", "_") else "_"
        for character in str(text or "")
    ).strip("_").lower()


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


def _vector_stores(database: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        database.execute(
            """
            SELECT organization_id, module_key, vector_store_id
            FROM organization_vector_stores
            ORDER BY organization_id, module_key
            """
        )
    )


def _project_index(database: sqlite3.Connection, organization_id: int) -> dict:
    """Mapas para reconhecer a quem pertence cada arquivo ja indexado."""

    por_file_id: dict[str, dict] = {}
    por_sessao: dict[str, int] = {}
    por_slug_de_projeto: dict[str, int] = {}

    for row in database.execute(
        """
        SELECT a.project_id, a.session_id,
               a.openai_file_id_prosodia, a.openai_file_id_transcricao
        FROM audios a
        JOIN projects p ON p.id = a.project_id
        WHERE p.organization_id = ?
        """,
        (organization_id,),
    ):
        session_id = row["session_id"]
        por_sessao[_slug(session_id)] = row["project_id"]
        for column, tipo in (
            ("openai_file_id_prosodia", "prosodia"),
            ("openai_file_id_transcricao", "transcricao"),
        ):
            file_id = row[column]
            if file_id:
                por_file_id[file_id] = {
                    "project_id": row["project_id"],
                    "session_id": session_id,
                    "tipo": tipo,
                }

    for row in database.execute(
        "SELECT id, name FROM projects WHERE organization_id = ?",
        (organization_id,),
    ):
        por_slug_de_projeto[_slug(row["name"])] = row["id"]

    return {
        "por_file_id": por_file_id,
        "por_sessao": por_sessao,
        "por_slug_de_projeto": por_slug_de_projeto,
    }


def _classificar(file_id: str, filename: str, indice: dict, module_key: str) -> dict:
    """Decide os atributos de um documento ja indexado.

    A ordem importa: o id guardado no banco e a evidencia mais forte; o nome do
    arquivo e o que sobra para o material que a aplicacao nunca registrou.
    """

    if module_key != "prosodia":
        # Fora da Prosodia so existe literatura: nenhuma tela manda dado de
        # projeto para a base da organizacao.
        return {"escopo": "referencia", "modulo": module_key}

    conhecido = indice["por_file_id"].get(file_id)
    if conhecido:
        return {
            "escopo": "projeto",
            "modulo": "prosodia",
            "project_id": conhecido["project_id"],
            "session_id": conhecido["session_id"],
            "tipo": conhecido["tipo"],
        }

    nome = filename or ""
    for prefixo, tipo, escopo in PREFIXOS_DE_SESSAO:
        if nome.startswith(prefixo):
            resto = _slug(nome[len(prefixo) :].rsplit(".", 1)[0])
            for sessao_slug, project_id in indice["por_sessao"].items():
                if resto.startswith(sessao_slug):
                    return {
                        "escopo": escopo,
                        "modulo": "prosodia",
                        "project_id": project_id,
                        "session_id": sessao_slug,
                        "tipo": tipo,
                    }
            # Parece material de projeto, mas a sessao nao existe mais.
            return {"escopo": escopo, "modulo": "prosodia", "tipo": tipo}

    for prefixo, tipo, escopo in PREFIXOS_DE_PROJETO:
        if nome.startswith(prefixo):
            resto = _slug(nome[len(prefixo) :])
            for projeto_slug, project_id in indice["por_slug_de_projeto"].items():
                if projeto_slug and resto.startswith(projeto_slug):
                    return {
                        "escopo": escopo,
                        "modulo": "prosodia",
                        "project_id": project_id,
                        "tipo": tipo,
                    }
            return {"escopo": escopo, "modulo": "prosodia", "tipo": tipo}

    return {"escopo": "referencia", "modulo": "prosodia"}


def _nome_do_arquivo(client: OpenAI, file_id: str, cache: dict) -> str:
    if file_id not in cache:
        try:
            cache[file_id] = client.files.retrieve(file_id).filename or ""
        except Exception:
            cache[file_id] = ""
    return cache[file_id]


def _processar_store(
    client: OpenAI,
    database: sqlite3.Connection,
    row: sqlite3.Row,
    aplicar: bool,
    refazer: bool,
) -> Counter:
    organization_id = row["organization_id"]
    module_key = row["module_key"]
    vector_store_id = row["vector_store_id"]

    print(
        "\n=== organizacao {} · modulo {} · {}".format(
            organization_id, module_key, vector_store_id
        )
    )

    indice = _project_index(database, organization_id)
    nomes: dict[str, str] = {}
    try:
        nomes = {
            arquivo.id: (arquivo.filename or "")
            for arquivo in client.files.list(purpose="assistants", limit=10000)
        }
    except Exception:
        pass

    contagem: Counter = Counter()
    try:
        arquivos = list(client.vector_stores.files.list(vector_store_id=vector_store_id))
    except Exception as error:
        print("  falha ao listar: {}".format(error), file=sys.stderr)
        return contagem

    for arquivo in arquivos:
        atributos_atuais = getattr(arquivo, "attributes", None) or {}
        if atributos_atuais.get("escopo") and not refazer:
            contagem["ja_carimbado"] += 1
            continue

        filename = nomes.get(arquivo.id) or _nome_do_arquivo(client, arquivo.id, nomes)
        atributos = _classificar(arquivo.id, filename, indice, module_key)
        indefinido = atributos["escopo"] != "referencia" and not atributos.get(
            "project_id"
        )
        contagem["indefinido" if indefinido else atributos["escopo"]] += 1

        marca = " (SEM PROJETO)" if indefinido else ""
        print(
            "  {:<48} -> {}{}".format(
                (filename or arquivo.id)[:48], atributos["escopo"], marca
            )
        )

        if aplicar:
            try:
                client.vector_stores.files.update(
                    arquivo.id,
                    vector_store_id=vector_store_id,
                    attributes=atributos,
                )
            except Exception as error:
                contagem["falhou"] += 1
                print("    falha: {}".format(error), file=sys.stderr)

    return contagem


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill dos atributos de escopo na base de conhecimento."
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database to read project and audio ownership from.",
    )
    parser.add_argument(
        "--refazer",
        action="store_true",
        help="Recarimba tambem os documentos que ja tem escopo.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Grava os atributos. Sem esta flag, apenas mostra a classificacao.",
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
    total: Counter = Counter()
    try:
        stores = _vector_stores(database)
        if not stores:
            print("Nenhum vector store registrado no banco.")
            return 0
        for row in stores:
            total.update(
                _processar_store(
                    client, database, row, arguments.apply, arguments.refazer
                )
            )
    finally:
        database.close()

    print("\nResumo: {}".format(dict(total)))
    if total.get("indefinido"):
        print(
            "Atencao: {} documento(s) parecem material de projeto mas nao tem "
            "projeto correspondente. Eles ficam fora de qualquer busca filtrada "
            "ate serem apagados por scripts/cleanup_orphan_kb_files.py.".format(
                total["indefinido"]
            )
        )
    if not arguments.apply:
        print("\nDry-run concluido. Repita com --apply para gravar os atributos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
