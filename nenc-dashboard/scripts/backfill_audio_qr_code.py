"""Preenche o código do QR nos áudios importados antes da coluna existir.

O áudio herda a unidade do QR code por onde entrou, e o vínculo é pelo código
do QR (`audios.qr_code_code`), que a tela de QR codes não deixa editar. Os
áudios importados até aqui só guardaram o nome (`audios.qr_code_name`), ou o
código no lugar do nome quando a API não mandava nome.

Para cada projeto ligado à API, o script lê a lista de QR codes do projeto e
procura o QR de cada áudio: primeiro pelo código, depois pelo nome, sem
diferenciar caixa nem espaços. Nome que casa com mais de um QR fica vazio e
vai para o relatório, como o nome que não casa com nenhum (QR renomeado ou
apagado depois da importação). Nesses casos a unidade se corrige na página do
áudio.

Dry-run por padrão. Dentro do container do dashboard, que já tem a URL e a
chave da API e o caminho do banco:
    python scripts/backfill_audio_qr_code.py --database /app/data/prosodia.db
    python scripts/backfill_audio_qr_code.py --database /app/data/prosodia.db --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

PREENCHER = "preencher"
AMBIGUO = "nome_em_mais_de_um_qr"
NAO_ENCONTRADO = "qr_nao_encontrado"


def _chave(texto) -> str:
    return " ".join(str(texto or "").split()).casefold()


def codigo_do_qr(qr_code_name: str, qrs: list[dict]) -> tuple[str, Optional[str]]:
    """Decide o código do QR de um áudio. Devolve (categoria, código).

    Função pura: é a regra que evita ligar o áudio ao QR errado. O código vem
    antes do nome porque a importação gravava o código no campo do nome
    quando a API não mandava nome, e dois QRs podem ter o mesmo nome, mas não
    o mesmo código.
    """
    procurado = _chave(qr_code_name)
    por_codigo = [qr for qr in qrs if _chave(qr.get("code")) == procurado]
    if len(por_codigo) == 1:
        return PREENCHER, str(por_codigo[0]["code"]).strip()
    por_nome = [qr for qr in qrs if _chave(qr.get("name")) == procurado and str(qr.get("code") or "").strip()]
    if len(por_nome) == 1:
        return PREENCHER, str(por_nome[0]["code"]).strip()
    if len(por_nome) > 1:
        return AMBIGUO, None
    return NAO_ENCONTRADO, None


def _qrs_na_api(base_url: str, api_key: str, api_project_id: int) -> list[dict]:
    request = urllib.request.Request(
        "{}/projects/{}/qr-codes".format(base_url.rstrip("/"), api_project_id),
        headers={"X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise


def _backup(database_path: Path, label: str = "qr_code_backfill") -> Path:
    """Copia o banco para backups/; `label` diz no nome qual script gravou."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = database_path.parent / "backups" / "prosodia_before_{}_{}.db".format(label, stamp)
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

    # A conexão fecha logo depois da leitura: as chamadas à API e o backup
    # acontecem sem o arquivo aberto, e a gravação abre outra.
    with closing(sqlite3.connect(database_path)) as conn:
        conn.row_factory = sqlite3.Row
        colunas = {row["name"] for row in conn.execute("PRAGMA table_info(audios)")}
        if "qr_code_code" not in colunas:
            print("audios.qr_code_code nao existe: suba o dashboard novo antes (init_db cria).", file=sys.stderr)
            return 1
        pendentes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT a.id, a.project_id, a.qr_code_name, p.api_project_id
                FROM audios a
                JOIN projects p ON p.id = a.project_id
                WHERE COALESCE(a.qr_code_name, '') <> '' AND COALESCE(a.qr_code_code, '') = ''
                ORDER BY a.project_id, a.id
                """
            )
        ]
    print("Banco: {}".format(database_path))
    print("Audios com QR e sem codigo: {}".format(len(pendentes)))

    contagem: Counter = Counter()
    preenchimentos: list[tuple[str, int]] = []
    qrs_por_projeto: dict[int, Optional[list[dict]]] = {}
    for audio in pendentes:
        api_project_id = audio["api_project_id"]
        if not api_project_id:
            contagem["projeto_sem_api"] += 1
            continue
        if api_project_id not in qrs_por_projeto:
            try:
                qrs_por_projeto[api_project_id] = _qrs_na_api(base_url, api_key, api_project_id)
            except Exception as error:
                qrs_por_projeto[api_project_id] = None
                print("  projeto da API {}: falha ao ler os QR codes: {}".format(api_project_id, error), file=sys.stderr)
        qrs = qrs_por_projeto[api_project_id]
        if qrs is None:
            contagem["erro_de_api"] += 1
            continue
        categoria, codigo = codigo_do_qr(audio["qr_code_name"], qrs)
        contagem[categoria] += 1
        if categoria == PREENCHER:
            preenchimentos.append((codigo, audio["id"]))
        else:
            print("  audio {:>4}  projeto {:>3}  QR {!r}: {}".format(
                audio["id"], audio["project_id"], audio["qr_code_name"], categoria))

    print("\nResumo: {}".format(dict(contagem)))
    if not arguments.apply:
        print("Dry-run. Repita com --apply para gravar {} codigo(s).".format(len(preenchimentos)))
        return 0
    if not preenchimentos:
        print("Nada a gravar.")
        return 0

    print("Backup: {}".format(_backup(database_path)))
    with closing(sqlite3.connect(database_path)) as conn, conn:
        conn.executemany(
            "UPDATE audios SET qr_code_code = ? WHERE id = ? AND COALESCE(qr_code_code, '') = ''",
            preenchimentos,
        )
    print("Aplicado: {} audio(s) com o codigo do QR.".format(len(preenchimentos)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
