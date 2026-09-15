"""Carimba o QR Code nas entrevistas importadas antes de a coluna voltar.

`audios.qr_code_name` foi removido num refactor e restaurado depois. Nesse
intervalo nenhuma importacao gravou o QR, entao a coluna "QR Code" da tabela de
Entrevistas mostra "Sem QR" para o acervo inteiro.

O QR mora na API de WhatsApp. O id do audio na API esta no fim do `session_id`
(`wa_<telefone>_<id>`, `wa_upload_<id>`), mas o id sozinho nao basta: parte do
acervo foi importada de uma instancia antiga da API, com numeracao propria, e o
mesmo id em producao pode ser outro audio. Por isso o QR so e aceito quando o
`whatsapp_message_id` que a API devolve e igual ao guardado localmente. Sem
mensagem para comparar, a entrevista fica como esta.

Dry-run por padrao. Dentro do container do dashboard, que ja tem a URL e a
chave da API e o caminho do banco:
    python scripts/backfill_interview_qr_codes.py --database /app/data/prosodia.db
    python scripts/backfill_interview_qr_codes.py --database /app/data/prosodia.db --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

_API_ID_NO_FIM = re.compile(r"_(\d+)$")

CARIMBAR = "carimbar"
SEM_QR_NA_API = "sem_qr_na_api"
MENSAGEM_DIVERGENTE = "mensagem_divergente"
SEM_MENSAGEM_LOCAL = "sem_mensagem_local"
SESSAO_SEM_ID = "sessao_sem_id"
NAO_ENCONTRADO = "nao_encontrado_na_api"


def api_audio_id(session_id: str) -> Optional[int]:
    """Id do audio na API a partir do session_id que a importacao monta."""

    if not str(session_id or "").startswith("wa_"):
        return None
    match = _API_ID_NO_FIM.search(str(session_id))
    return int(match.group(1)) if match else None


def classificar(local: dict, api: Optional[dict]) -> tuple[str, Optional[str]]:
    """Decide o que fazer com uma entrevista. Devolve (categoria, qr_code_name).

    Funcao pura: e a regra que evita carimbar o QR de outro audio.
    """

    if api_audio_id(local.get("session_id")) is None:
        return SESSAO_SEM_ID, None
    mensagem_local = str(local.get("whatsapp_message_id") or "").strip()
    if not mensagem_local:
        return SEM_MENSAGEM_LOCAL, None
    if api is None:
        return NAO_ENCONTRADO, None
    if str(api.get("whatsapp_message_id") or "").strip() != mensagem_local:
        return MENSAGEM_DIVERGENTE, None
    qr = api.get("qr_code_name") or api.get("qr_code_code")
    if not qr:
        return SEM_QR_NA_API, None
    return CARIMBAR, str(qr)


def _buscar_na_api(base_url: str, api_key: str, audio_id: int) -> Optional[dict]:
    request = urllib.request.Request(
        "{}/audios/{}".format(base_url.rstrip("/"), audio_id),
        headers={"X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def _backup(database_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = database_path.parent / "backups" / "prosodia_before_qr_backfill_{}.db".format(stamp)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(database_path, destination)
    return destination


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
    if "qr_code_name" not in colunas:
        print("audios.qr_code_name nao existe: suba o dashboard novo antes (init_db cria).", file=sys.stderr)
        return 1

    pendentes = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, project_id, session_id, whatsapp_message_id
            FROM audios
            WHERE COALESCE(qr_code_name, '') = ''
            ORDER BY id
            """
        )
    ]
    print("Banco: {}".format(database_path))
    print("Entrevistas sem QR: {}".format(len(pendentes)))

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
        categoria, qr = classificar(local, api)
        contagem[categoria] += 1
        if categoria == CARIMBAR:
            carimbos.append((qr, local["id"]))
            print("  audio {:>4}  projeto {:>3}  {:<36} -> {}".format(
                local["id"], local["project_id"], local["session_id"][:36], qr))
        elif categoria == MENSAGEM_DIVERGENTE:
            print("  audio {:>4}  projeto {:>3}  {:<36} -> mensagem diferente na API, ignorado".format(
                local["id"], local["project_id"], local["session_id"][:36]))

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
            "UPDATE audios SET qr_code_name = ? WHERE id = ? AND COALESCE(qr_code_name, '') = ''",
            carimbos,
        )
    conn.close()
    print("Aplicado: {} entrevista(s) carimbada(s).".format(len(carimbos)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
