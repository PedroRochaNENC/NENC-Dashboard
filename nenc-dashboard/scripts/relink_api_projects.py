"""Acerta o vinculo e a posse dos projetos da API de WhatsApp no banco local.

O monitor so mostra audio de projeto da API pertencente a organizacao ativa, e
resolve isso por dois caminhos que precisam concordar: `projects.api_project_id`
diz para qual projeto da API o projeto local aponta, e
`organization_external_resources` diz de quem aquele projeto da API e. Quando os
dois discordam, ou quando o id aponta para projeto que nao existe, a tela fica
vazia sem explicar por que.

Cada ambiente tem seu proprio banco, com numeracao propria, entao o mapeamento
vive em PLANOS e o plano e escolhido na linha de comando. Nao ha plano padrao de
proposito: rodar o mapeamento do ambiente errado e justamente o erro que este
script existe para nao repetir.

Dry-run por padrao. Rode a partir da raiz da aplicacao:
    py scripts/relink_api_projects.py --plano local --database prosodia.db
    python3 scripts/relink_api_projects.py --plano producao --database data/prosodia.db --apply
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

# religar:     (projeto_local, org, api_de, api_para, motivo)
# desvincular: (projeto_local, org, api_atual, motivo)  -> api_project_id vira NULL
# posse:       (api_id, organizacao, motivo)            -> dono do projeto da API
#
# A organizacao vai junto de proposito, mesmo sendo redundante com o banco: e a
# checagem que denuncia plano rodado no ambiente errado antes de qualquer escrita.
#
# "desvincular" e "posse" andam juntos no caso do API 2, mas sao efeitos
# distintos: um projeto local pode perder o vinculo sem que a posse mude, e a
# posse pode mudar sem que nenhum projeto local seja tocado.
PLANOS = {
    "local": {
        "religar": (
            (11, 2, 6, 5, "Projeto Smartfit -> API 5 'Smartfit - Teste' (16 audios); API 6 nao existe"),
            (6, 1, None, 7, "Demontracao ABIHPEC -> API 7 'Demontracao ABIHPEC' (nome identico)"),
        ),
        "desvincular": (
            (7, 1, 2, "Demontracao MC15 (Nenc) apontava para API 2, que e da Smartfit"),
        ),
        "posse": (
            (2, 2, "API 2 'SmartFit-Teste' (8 audios) e da Smartfit, estava com a Nenc"),
        ),
    },
    # Producao ja tem ABIHPEC (local 6 -> API 7) e Smartfit (local 16 -> API 5)
    # corretos; so o API 2 esta com o dono errado, por tres projetos locais.
    "producao": {
        "religar": (),
        "desvincular": (
            (7, 1, 2, "Demontracao MC15 (Nenc) apontava para API 2, que e da Smartfit"),
            (9, 1, 2, "Teste (Nenc) apontava para API 2, que e da Smartfit"),
            (10, 1, 2, "Teste 2 (Nenc) apontava para API 2, que e da Smartfit"),
        ),
        "posse": (
            (2, 2, "API 2 'SmartFit-Teste' (8 audios) e da Smartfit, estava com a Nenc"),
        ),
    },
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _backup(database_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = database_path.parent / "backups" / "prosodia_before_api_relink_{}.db".format(stamp)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(database_path, destination)
    return destination


def _projeto(database: sqlite3.Connection, local_id: int, organization_id: int) -> sqlite3.Row:
    row = database.execute(
        "SELECT id, name, organization_id, api_project_id FROM projects WHERE id = ?",
        (local_id,),
    ).fetchone()
    if row is None:
        sys.exit("projeto local {} nao existe neste banco - plano errado para este ambiente?".format(local_id))
    if row["organization_id"] != organization_id:
        sys.exit(
            "projeto local {} esta na organizacao {}, esperado {} - plano errado para "
            "este ambiente?".format(local_id, row["organization_id"], organization_id)
        )
    return row


def _dono(database: sqlite3.Connection, api_id: int):
    row = database.execute(
        """
        SELECT organization_id FROM organization_external_resources
        WHERE resource_type = 'whatsapp_api_project' AND resource_id = ?
        """,
        (str(api_id),),
    ).fetchone()
    return row["organization_id"] if row else None


def _check_religar(database: sqlite3.Connection, plano: dict) -> list[tuple]:
    pendentes = []
    for local_id, organization_id, api_de, api_para, motivo in plano["religar"]:
        row = _projeto(database, local_id, organization_id)
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


def _check_desvincular(database: sqlite3.Connection, plano: dict) -> list[tuple]:
    pendentes = []
    for local_id, organization_id, api_atual, motivo in plano["desvincular"]:
        row = _projeto(database, local_id, organization_id)
        if row["api_project_id"] is None:
            print("  ja desvinculado: projeto {}".format(local_id))
            continue
        if row["api_project_id"] != api_atual:
            sys.exit(
                "projeto local {} aponta para API {}, esperado {} - estado inesperado, "
                "revise antes de aplicar".format(local_id, row["api_project_id"], api_atual)
            )
        print("  projeto {} ({}): API {} -> None".format(local_id, row["name"], api_atual))
        print("    {}".format(motivo))
        pendentes.append((local_id,))
    return pendentes


def _check_posse(database: sqlite3.Connection, plano: dict) -> list[tuple]:
    pendentes = []
    for api_id, organization_id, motivo in plano["posse"]:
        atual = _dono(database, api_id)
        if atual == organization_id:
            print("  posse ja correta: API {} -> org {}".format(api_id, organization_id))
            continue
        print("  posse da API {}: org {} -> org {}".format(api_id, atual, organization_id))
        print("    {}".format(motivo))
        pendentes.append((api_id, organization_id, atual))
    return pendentes


def _apply(database: sqlite3.Connection, religar, desvincular, posse) -> None:
    now = _timestamp()

    for local_id, api_de, api_para, organization_id in religar:
        database.execute("UPDATE projects SET api_project_id = ? WHERE id = ?", (api_para, local_id))
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

    for (local_id,) in desvincular:
        database.execute("UPDATE projects SET api_project_id = NULL WHERE id = ?", (local_id,))

    for api_id, organization_id, _atual in posse:
        # O metadata aponta para o projeto local que justificava a posse; depois
        # de desvincular ele mente, entao vai embora junto com o dono antigo.
        atualizadas = database.execute(
            """
            UPDATE organization_external_resources
            SET organization_id = ?, metadata_json = '{}', updated_at = ?
            WHERE resource_type = 'whatsapp_api_project' AND resource_id = ?
            """,
            (organization_id, now, str(api_id)),
        ).rowcount
        if not atualizadas:
            database.execute(
                """
                INSERT INTO organization_external_resources (
                    resource_type, resource_id, organization_id, metadata_json, created_at, updated_at
                ) VALUES ('whatsapp_api_project', ?, ?, '{}', ?, ?)
                """,
                (str(api_id), organization_id, now, now),
            )


def _report(database: sqlite3.Connection) -> None:
    print("\nProjetos locais:")
    for row in database.execute(
        "SELECT id, name, organization_id, api_project_id FROM projects ORDER BY id"
    ):
        print(
            "  local={} org={} api={} {}".format(
                row["id"], row["organization_id"], row["api_project_id"], ascii(row["name"])
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
    parser.add_argument("--plano", required=True, choices=sorted(PLANOS), help="ambiente a corrigir")
    parser.add_argument("--database", default=str(APP_ROOT / "prosodia.db"))
    parser.add_argument("--apply", action="store_true", help="grava; sem isto e so simulacao")
    args = parser.parse_args()

    database_path = Path(args.database).resolve()
    if not database_path.exists():
        sys.exit("banco nao encontrado: {}".format(database_path))

    plano = PLANOS[args.plano]
    database = sqlite3.connect(database_path)
    database.row_factory = sqlite3.Row

    print("Plano: {}".format(args.plano))
    print("Banco: {}".format(database_path))
    print("\nMudancas:")
    religar = _check_religar(database, plano)
    desvincular = _check_desvincular(database, plano)
    posse = _check_posse(database, plano)

    if not religar and not desvincular and not posse:
        print("  nada a fazer")
        _report(database)
        return

    if not args.apply:
        print("\nDry-run. Repita com --apply para gravar.")
        return

    print("\nBackup: {}".format(_backup(database_path)))
    with database:
        _apply(database, religar, desvincular, posse)
    print("Aplicado.")
    _report(database)


if __name__ == "__main__":
    main()


# Deixados de fora, por falta de destino defensavel na API:
#
#   local 1  'PROJETO 5o FORUM FUTURO DO AGRO'  sem nada parecido na API
#   local 4  'Teste Whatsapp'                   nenhum projeto da API corresponde
#   local 5  'Teste whatsapp 2'                 idem
#   local 8  'Projeto politica' -> API 3        nomes nao batem, mas API 3 esta
#                                               vazio, entao nao mostra nada errado
#
# Em producao varios projetos locais dividem o mesmo api_project_id (API 3 tem os
# locais 8, 11 e 14; API 4 tem 12 e 15). O monitor deduplica por id de audio, entao
# isso nao duplica nada na tela, mas e sinal de projeto criado em duplicidade.
#
# Depois que a posse do API 2 vai para a Smartfit, nenhum projeto local dela aponta
# para esse id - e `_owned_api_project_ids` percorre projetos locais. Os 8 audios so
# aparecem no monitor quando a Smartfit tiver um projeto ligado ao API 2, criado
# pela pagina de Projetos: inserir a linha na mao aqui pularia autoria, auditoria e
# os defaults do formulario.
