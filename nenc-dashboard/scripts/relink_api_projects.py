"""Religa projetos locais ao id correto do projeto na API de WhatsApp.

Os `projects.api_project_id` do banco local guardaram a numeracao de uma
instancia antiga da API (a que era exposta pelo tunel ngrok), que tem banco
proprio. Apontando o dashboard para producao, esses ids deixam de bater: o
"Projeto Smartfit" aponta para o projeto 6, que nem existe em producao.

O monitor so mostra audio de projeto da API pertencente a organizacao ativa,
entao o vinculo errado deixa a tela vazia. Alem do `api_project_id`, o claim em
`organization_external_resources` precisa acompanhar - sem ele
`_owned_api_project_ids` descarta o projeto silenciosamente.

Dry-run por padrao. Rode a partir da raiz da aplicacao:
    py scripts/relink_api_projects.py --database prosodia.db
    py scripts/relink_api_projects.py --database prosodia.db --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

# (projeto_local, api_esperado_hoje, api_correto, organizacao, motivo)
#
# Só entram os casos em que o nome bate ou o destino foi confirmado. Os demais
# projetos ficam de fora de proposito: ver AMBIGUOS, no fim do arquivo.
RELINKS = (
    (11, 6, 5, 2, "Projeto Smartfit -> API 5 'Smartfit - Teste' (16 audios); API 6 nao existe"),
    (6, None, 7, 1, "Demontracao ABIHPEC -> API 7 'Demontracao ABIHPEC' (nome identico)"),
)

# (projeto_local, api_atual, organizacao_dona_do_api, motivo)
#
# O projeto local perde o vinculo (nao tem contrapartida na API) e a posse do
# projeto da API passa para a organizacao certa. Sao dois efeitos distintos e por
# isso nao cabem em RELINKS.
REATRIBUIR = (
    (
        7,
        2,
        2,
        "Demontracao MC15 (Nenc) apontava para API 2 'SmartFit-Teste' (8 audios); "
        "o projeto da API e da Smartfit",
    ),
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _backup(database_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = database_path.parent / "backups" / "prosodia_before_api_relink_{}.db".format(stamp)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(database_path, destination)
    return destination


def _check_reatribuir(database: sqlite3.Connection) -> list[tuple]:
    """Confere as reatribuicoes de posse e devolve so o que ainda precisa mudar."""
    pendentes = []
    for local_id, api_id, organization_id, motivo in REATRIBUIR:
        row = database.execute(
            "SELECT id, name, api_project_id FROM projects WHERE id = ?", (local_id,)
        ).fetchone()
        if row is None:
            sys.exit("projeto local {} nao existe neste banco".format(local_id))

        dono = database.execute(
            """
            SELECT organization_id FROM organization_external_resources
            WHERE resource_type = 'whatsapp_api_project' AND resource_id = ?
            """,
            (str(api_id),),
        ).fetchone()
        dono_atual = dono["organization_id"] if dono else None

        if row["api_project_id"] is None and dono_atual == organization_id:
            print("  ja reatribuido: API {} -> org {}".format(api_id, organization_id))
            continue
        if row["api_project_id"] not in (None, api_id):
            sys.exit(
                "projeto local {} aponta para API {}, esperado {} - estado inesperado, "
                "revise antes de aplicar".format(local_id, row["api_project_id"], api_id)
            )
        print("  projeto {} ({}): API {} -> None".format(local_id, row["name"], row["api_project_id"]))
        print("    posse da API {}: org {} -> org {}".format(api_id, dono_atual, organization_id))
        print("    {}".format(motivo))
        pendentes.append((local_id, api_id, organization_id))
    return pendentes


def _check(database: sqlite3.Connection) -> list[tuple]:
    """Confere o estado atual e devolve so o que ainda precisa mudar."""
    pendentes = []
    for local_id, api_de, api_para, organization_id, motivo in RELINKS:
        row = database.execute(
            "SELECT id, name, organization_id, api_project_id FROM projects WHERE id = ?",
            (local_id,),
        ).fetchone()
        if row is None:
            sys.exit("projeto local {} nao existe neste banco".format(local_id))
        if row["organization_id"] != organization_id:
            sys.exit(
                "projeto local {} esta na organizacao {}, esperado {}".format(
                    local_id, row["organization_id"], organization_id
                )
            )
        if row["api_project_id"] == api_para:
            print("  ja religado: projeto {} -> API {}".format(local_id, api_para))
            continue
        if row["api_project_id"] != api_de:
            sys.exit(
                "projeto local {} aponta para API {}, esperado {} - estado inesperado, "
                "revise antes de aplicar".format(local_id, row["api_project_id"], api_de)
            )
        print("  projeto {} ({}): API {} -> {}".format(local_id, row["name"], api_de, api_para))
        print("    {}".format(motivo))
        pendentes.append((local_id, api_de, api_para, organization_id))
    return pendentes


def _apply(database: sqlite3.Connection, pendentes: list[tuple]) -> None:
    now = _timestamp()
    for local_id, api_de, api_para, organization_id in pendentes:
        database.execute(
            "UPDATE projects SET api_project_id = ? WHERE id = ?", (api_para, local_id)
        )
        if api_de is not None:
            database.execute(
                """
                DELETE FROM organization_external_resources
                WHERE resource_type = 'whatsapp_api_project' AND resource_id = ?
                """,
                (str(api_de),),
            )
        database.execute(
            """
            INSERT INTO organization_external_resources (
                resource_type, resource_id, organization_id, metadata_json, created_at, updated_at
            ) VALUES ('whatsapp_api_project', ?, ?, ?, ?, ?)
            ON CONFLICT(resource_type, resource_id) DO UPDATE SET
                organization_id = excluded.organization_id,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (str(api_para), organization_id, json.dumps({"project_id": local_id}), now, now),
        )


def _apply_reatribuir(database: sqlite3.Connection, pendentes: list[tuple]) -> None:
    now = _timestamp()
    for local_id, api_id, organization_id in pendentes:
        database.execute(
            "UPDATE projects SET api_project_id = NULL WHERE id = ?", (local_id,)
        )
        # A posse muda de dono; apagar e recriar perderia o created_at.
        database.execute(
            """
            UPDATE organization_external_resources
            SET organization_id = ?, metadata_json = ?, updated_at = ?
            WHERE resource_type = 'whatsapp_api_project' AND resource_id = ?
            """,
            (organization_id, json.dumps({}), now, str(api_id)),
        )
        if not database.execute(
            """
            SELECT 1 FROM organization_external_resources
            WHERE resource_type = 'whatsapp_api_project' AND resource_id = ?
            """,
            (str(api_id),),
        ).fetchone():
            database.execute(
                """
                INSERT INTO organization_external_resources (
                    resource_type, resource_id, organization_id, metadata_json, created_at, updated_at
                ) VALUES ('whatsapp_api_project', ?, ?, ?, ?, ?)
                """,
                (str(api_id), organization_id, json.dumps({}), now, now),
            )


def _report(database: sqlite3.Connection) -> None:
    print("\nProjetos locais:")
    for row in database.execute(
        "SELECT id, name, organization_id, api_project_id FROM projects ORDER BY id"
    ):
        print(
            "  local={} org={} api={} {}".format(
                row["id"], row["organization_id"], row["api_project_id"], row["name"]
            )
        )
    print("\nPosse de projeto da API:")
    for row in database.execute(
        """
        SELECT resource_id, organization_id, metadata_json
        FROM organization_external_resources
        WHERE resource_type = 'whatsapp_api_project'
        ORDER BY CAST(resource_id AS INTEGER)
        """
    ):
        print(
            "  api_project={} org={} {}".format(
                row["resource_id"], row["organization_id"], row["metadata_json"]
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(APP_ROOT / "prosodia.db"))
    parser.add_argument("--apply", action="store_true", help="grava; sem isto e so simulacao")
    args = parser.parse_args()

    database_path = Path(args.database).resolve()
    if not database_path.exists():
        sys.exit("banco nao encontrado: {}".format(database_path))

    database = sqlite3.connect(database_path)
    database.row_factory = sqlite3.Row

    print("Banco: {}".format(database_path))
    print("\nMudancas:")
    pendentes = _check(database)
    pendentes_reatribuir = _check_reatribuir(database)
    if not pendentes and not pendentes_reatribuir:
        print("  nada a fazer")
        _report(database)
        return

    if not args.apply:
        print("\nDry-run. Repita com --apply para gravar.")
        return

    print("\nBackup: {}".format(_backup(database_path)))
    with database:
        _apply(database, pendentes)
        _apply_reatribuir(database, pendentes_reatribuir)
    print("Aplicado.")
    _report(database)


if __name__ == "__main__":
    main()


# AMBIGUOS - deixados de fora por falta de destino defensavel na API:
#
#   local 1  'PROJETO 5o FORUM FUTURO DO AGRO'  sem nada parecido na API
#   local 4  'Teste Whatsapp'                   API 3 'teste 2' ja e do local 8
#   local 5  'Teste whatsapp 2'                 idem
#   local 8  'Projeto politica' -> API 3        nomes nao batem, mas API 3 esta
#                                               vazio, entao nao mostra nada errado
#
# Depois de REATRIBUIR, o API 2 pertence a Smartfit mas nenhum projeto local dela
# aponta para ele - `_owned_api_project_ids` percorre projetos locais, entao os 8
# audios ficam sem aparecer no monitor ate a Smartfit ter um projeto ligado ao
# API 2. Isso se faz pela pagina de Projetos, nao aqui: criar linha de projeto na
# mao pularia autoria, auditoria e os defaults do formulario.
