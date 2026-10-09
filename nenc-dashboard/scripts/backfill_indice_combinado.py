"""Grava o índice combinado de todos os áudios já importados.

A lista de Áudios lê `audios.indice_combinado` (e `indice_texto`,
`indice_voz`), que o dashboard grava a cada importação, reprocessamento ou
exclusão. O acervo anterior às colunas nasce com NULL. A régua da voz é o
projeto inteiro, então o cálculo é feito projeto a projeto, com o mesmo
`calcular_indices` que o app usa.

Dry-run por padrão. Dentro do container do dashboard:
    python scripts/backfill_indice_combinado.py --database /app/data/prosodia.db
    python scripts/backfill_indice_combinado.py --database /app/data/prosodia.db --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_interview_qr_codes import _backup  # noqa: E402
from utils.prosodia_indice import calcular_indices, faixa  # noqa: E402

BACKUP_LABEL = "indice_backfill"
_COLUNAS = ("indice_combinado", "indice_texto", "indice_voz")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="grava; sem isto e so simulacao")
    arguments = parser.parse_args()

    database_path = arguments.database.expanduser().resolve()
    if not database_path.is_file():
        print("Banco nao encontrado: {}".format(database_path), file=sys.stderr)
        return 1

    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    colunas = {row["name"] for row in conn.execute("PRAGMA table_info(audios)")}
    if not set(_COLUNAS) <= colunas:
        print("audios.indice_combinado nao existe: suba o dashboard novo antes (init_db cria).", file=sys.stderr)
        return 1

    projetos = [row["project_id"] for row in conn.execute("SELECT DISTINCT project_id FROM audios ORDER BY project_id")]
    print("Banco: {}".format(database_path))
    gravacoes: list[tuple] = []
    total_faixas: Counter = Counter()
    for project_id in projetos:
        linhas = conn.execute(
            "SELECT session_id, sincronizado_csv FROM audios WHERE project_id = ? AND sincronizado_csv IS NOT NULL",
            (project_id,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM audios WHERE project_id = ?", (project_id,)).fetchone()[0]
        indices = calcular_indices({str(row["session_id"]): row["sincronizado_csv"] for row in linhas})
        faixas = Counter(faixa(*valores) for valores in indices.values())
        total_faixas.update(faixas)
        print("  projeto {}: {} de {} audio(s) com indice {}".format(project_id, len(indices), total, dict(faixas)))
        gravacoes.append((project_id, indices))

    print("\nResumo: {} audio(s) com indice {}".format(sum(len(i) for _, i in gravacoes), dict(total_faixas)))
    if not arguments.apply:
        print("Dry-run. Repita com --apply para gravar.")
        return 0

    print("Backup: {}".format(_backup(database_path, BACKUP_LABEL)))
    with conn:
        for project_id, indices in gravacoes:
            conn.execute(
                "UPDATE audios SET indice_combinado = NULL, indice_texto = NULL, indice_voz = NULL WHERE project_id = ?",
                (project_id,),
            )
            conn.executemany(
                "UPDATE audios SET indice_combinado = ?, indice_texto = ?, indice_voz = ? "
                "WHERE project_id = ? AND session_id = ?",
                [(i, t, v, project_id, sid) for sid, (i, t, v) in indices.items()],
            )
    conn.close()
    print("Aplicado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
