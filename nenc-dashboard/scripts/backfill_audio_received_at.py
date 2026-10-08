"""Carimba a hora de chegada (`received_at`) nas entrevistas já importadas.

O Resumo do projeto conta entradas por hora. A hora certa é a de chegada da
mensagem na API de WhatsApp; `created_at` marca a importação no dashboard e
concentraria o acervo no horário de cada sync. A coluna `audios.received_at`
nasce vazia para tudo o que foi importado antes dela.

Mesma regra de `backfill_interview_qr_codes.py`: o id do audio na API sai do
fim do `session_id`, e a hora só é aceita quando o `whatsapp_message_id` que a
API devolve é igual ao guardado localmente.

Dry-run por padrão. Dentro do container do dashboard:
    python scripts/backfill_audio_received_at.py --database /app/data/prosodia.db
    python scripts/backfill_audio_received_at.py --database /app/data/prosodia.db --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_interview_qr_codes import (  # noqa: E402
    _backup,
    _buscar_na_api,
    api_audio_id,
)

CARIMBAR = "carimbar"
SEM_HORA_NA_API = "sem_hora_na_api"
MENSAGEM_DIVERGENTE = "mensagem_divergente"
SEM_MENSAGEM_LOCAL = "sem_mensagem_local"
SESSAO_SEM_ID = "sessao_sem_id"
NAO_ENCONTRADO = "nao_encontrado_na_api"


def classificar(local: dict, api: Optional[dict]) -> tuple[str, Optional[str]]:
    """Decide o que fazer com uma entrevista. Devolve (categoria, received_at)."""

    if api_audio_id(local.get("session_id")) is None:
        return SESSAO_SEM_ID, None
    mensagem_local = str(local.get("whatsapp_message_id") or "").strip()
    if not mensagem_local:
        return SEM_MENSAGEM_LOCAL, None
    if api is None:
        return NAO_ENCONTRADO, None
    if str(api.get("whatsapp_message_id") or "").strip() != mensagem_local:
        return MENSAGEM_DIVERGENTE, None
    received = str(api.get("received_at") or "").strip()
    if not received:
        return SEM_HORA_NA_API, None
    return CARIMBAR, received


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="grava; sem isto e so simulacao")
    arguments = parser.parse_args()

    database_path = arguments.database.expanduser().resolve()
    if not database_path.is_file():
        print("Banco nao encontrado: {}".format(database_path), file=sys.stderr)
        return 1
    base_url = os.getenv("WHATSAPP_API_URL", "").strip().strip("'\"")
    api_key = os.getenv("WHATSAPP_API_KEY", "").strip().strip("'\"")
    if not base_url or not api_key:
        print("WHATSAPP_API_URL e WHATSAPP_API_KEY sao obrigatorias.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    colunas = {row["name"] for row in conn.execute("PRAGMA table_info(audios)")}
    if "received_at" not in colunas:
        print("audios.received_at nao existe: suba o dashboard novo antes (init_db cria).", file=sys.stderr)
        return 1

    pendentes = [
        dict(row)
        for row in conn.execute(
            "SELECT id, project_id, session_id, whatsapp_message_id FROM audios "
            "WHERE COALESCE(received_at, '') = '' ORDER BY id"
        )
    ]
    print("Banco: {}".format(database_path))
    print("Entrevistas sem hora de chegada: {}".format(len(pendentes)))

    contagem: Counter = Counter()
    carimbos: list[tuple[str, int]] = []
    for local in pendentes:
        audio_id = api_audio_id(local["session_id"])
        api = None
        if audio_id is not None and str(local.get("whatsapp_message_id") or "").strip():
            try:
                api = _buscar_na_api(base_url, api_key, audio_id)
            except Exception as error:
                contagem["erro_de_api"] += 1
                print("  audio {} ({}): falha na API: {}".format(local["id"], local["session_id"], error), file=sys.stderr)
                continue
        categoria, received = classificar(local, api)
        contagem[categoria] += 1
        if categoria == CARIMBAR:
            carimbos.append((received, local["id"]))

    print("\nResumo: {}".format(dict(contagem)))
    if not arguments.apply:
        print("Dry-run. Repita com --apply para gravar {} carimbo(s).".format(len(carimbos)))
        return 0
    if not carimbos:
        print("Nada a gravar.")
        return 0

    print("Backup: {}".format(_backup(database_path)))
    with conn:
        conn.executemany(
            "UPDATE audios SET received_at = ? WHERE id = ? AND COALESCE(received_at, '') = ''",
            carimbos,
        )
    conn.close()
    print("Aplicado: {} entrevista(s) carimbada(s).".format(len(carimbos)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
