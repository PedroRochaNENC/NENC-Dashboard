"""Poda o historico de analises de IA, mantendo as N mais recentes de cada dono.

Toda geracao de analise faz um INSERT e nada nunca e removido: o historico de um
audio ou de um projeto cresce para sempre, e cada linha carrega o texto inteiro
da analise. Este script corta a cauda antiga sem tocar na mais recente.

Dry-run por padrao. A partir da raiz da aplicacao:
    py scripts/prune_analysis_history.py --database data/nenc-insights.db
    py scripts/prune_analysis_history.py --database data/nenc-insights.db --keep 10 --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

# Cada historico e por dono: analise de audio pertence ao audio, as demais ao projeto.
HISTORICOS = (
    ("analyses", "audio_id", "audio"),
    ("project_analyses", "project_id", "projeto (NencBoost)"),
    ("ts_analyses", "project_id", "projeto (Teste Sensorial)"),
    ("jc_analyses", "project_id", "projeto (Jornada de Compra)"),
)


def _connect(database_path: Path) -> sqlite3.Connection:
    database = sqlite3.connect(database_path)
    database.row_factory = sqlite3.Row
    return database


def _tabelas(database: sqlite3.Connection) -> set:
    return {
        row[0]
        for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def excedentes(
    database: sqlite3.Connection, tabela: str, coluna_dono: str, manter: int
) -> dict:
    """Ids que passam do limite, por dono, do mais antigo para o mais novo.

    A ordem e a mesma que as telas usam para listar o historico (created_at
    desc, id desc), entao o que fica aqui e exatamente o que elas mostram no
    topo — e nunca a analise mais recente de alguem.
    """

    if manter < 1:
        raise ValueError("E preciso manter ao menos uma analise por dono.")

    por_dono: dict = {}
    vistos: Counter = Counter()
    for row in database.execute(
        "SELECT id, {dono} AS dono FROM {tabela} "
        "ORDER BY dono, created_at DESC, id DESC".format(dono=coluna_dono, tabela=tabela)
    ):
        dono = row["dono"]
        vistos[dono] += 1
        if vistos[dono] > manter:
            por_dono.setdefault(dono, []).append(row["id"])
    return por_dono


def _processar(
    database: sqlite3.Connection, manter: int, aplicar: bool
) -> Counter:
    resumo: Counter = Counter()
    tabelas = _tabelas(database)

    for tabela, coluna_dono, rotulo in HISTORICOS:
        if tabela not in tabelas:
            continue
        try:
            alvo = excedentes(database, tabela, coluna_dono, manter)
        except sqlite3.Error as error:
            print("  {}: falha ao inspecionar ({})".format(tabela, error), file=sys.stderr)
            continue

        total = sum(len(ids) for ids in alvo.values())
        print(
            "\n=== {} · por {} · {} dono(s) acima do limite · {} linha(s) a remover".format(
                tabela, rotulo, len(alvo), total
            )
        )
        for dono, ids in sorted(alvo.items(), key=lambda item: -len(item[1]))[:10]:
            print("  {} {}: {} linha(s)".format(coluna_dono, dono, len(ids)))

        resumo[tabela] = total
        if aplicar and total:
            ids = [linha_id for lista in alvo.values() for linha_id in lista]
            database.executemany(
                "DELETE FROM {} WHERE id = ?".format(tabela),
                [(linha_id,) for linha_id in ids],
            )
            database.commit()
    return resumo


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mantem apenas as N analises mais recentes de cada audio ou projeto."
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="SQLite database with the analysis history.",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=20,
        help="Quantas analises manter por dono (padrao: 20).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apaga de verdade. Sem esta flag, apenas mostra o que sairia.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    database_path = arguments.database.expanduser().resolve()
    if not database_path.is_file():
        print("Database not found: {}".format(database_path), file=sys.stderr)
        return 1
    if arguments.keep < 1:
        print("--keep precisa ser ao menos 1.", file=sys.stderr)
        return 1

    database = _connect(database_path)
    try:
        resumo = _processar(database, arguments.keep, arguments.apply)
    finally:
        database.close()

    print("\nResumo: {}".format(dict(resumo)))
    if not arguments.apply:
        print(
            "\nDry-run concluido. Faca uma copia do banco e repita com --apply "
            "para remover. O texto das analises antigas nao tem outra copia."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
