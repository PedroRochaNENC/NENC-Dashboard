"""
API de importação da Jornada de Compra (serviço `nenc-import-api`).

Recebe o que o script `scripts/jornada_enviar.py` manda da pasta do projeto e
guarda como importação pendente. Quem grava nos dados do projeto é a revisão
em Uploads, no app, com a conta de quem revisou: esta API nunca escreve em
tabela de análise.

Autenticação: `Authorization: Bearer <token>`, com um token por organização
no `.env` do servidor (`NENC_IMPORT_TOKEN_<id da organização>`, ao menos 32
caracteres). O token só enxerga os projetos da sua organização.

Rotas (todas sob `/api/jornada`):

- `GET  /health`
- `GET  /projects` — id e nome dos projetos da organização;
- `POST /imports` — abre um lote: `{"project_id", "source_label", "client"}`;
  devolve o id e os hashes que o projeto já tem, para o script não reenviar;
- `GET  /imports/{id}/files/status?path=...` — quanto de um arquivo já chegou;
- `PUT  /imports/{id}/files` — um bloco (corpo bruto, até 8 MB) com os
  cabeçalhos `X-File-Path` e `X-File-Meta` (UTF-8 percent-encoded),
  `X-File-Role`, `X-File-Sha256`, `X-File-Size`, `X-Chunk-Offset` e,
  opcional, `X-Source-Sha256` (hash do original de um vídeo compactado);
- `POST /imports/{id}/close` — fecha para revisão: `{"summary", "ignored"}`.
"""

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from utils import jornada_db, jornada_imports
from utils.jornada_imports import CHUNK_MAX_BYTES, ChunkOutOfOrder, ImportRefused

PREFIX = "/api/jornada"
MIN_TOKEN_LENGTH = 32
_TOKEN_KEY = re.compile(r"NENC_IMPORT_TOKEN_(\d+)")



@asynccontextmanager
async def _lifespan(_: FastAPI):
    jornada_db.init_db()
    yield


app = FastAPI(title="NENC Insights · importação da Jornada", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespan)


def _configured_tokens() -> Dict[str, int]:
    tokens = {}
    for key, value in os.environ.items():
        match = _TOKEN_KEY.fullmatch(key)
        if match and len(value.strip()) >= MIN_TOKEN_LENGTH:
            tokens[value.strip()] = int(match.group(1))
    return tokens


def organization(authorization: str = Header(default="")) -> int:
    """Organização do token. Compara com todos, em tempo constante, e não diz qual falhou."""
    scheme, _, token = authorization.partition(" ")
    token = token.strip().encode("utf-8")
    found: Optional[int] = None
    for candidate, organization_id in _configured_tokens().items():
        if hmac.compare_digest(candidate.encode("utf-8"), token):
            found = organization_id
    if scheme.lower() != "bearer":
        found = None
    if found is None:
        raise HTTPException(status_code=401, detail="Token ausente ou inválido.")
    return found


@app.exception_handler(ImportRefused)
def _refused(_: Request, error: ImportRefused) -> JSONResponse:
    if isinstance(error, ChunkOutOfOrder):
        return JSONResponse(status_code=409, content={"detail": str(error), "expected_offset": error.expected})
    status = 404 if "não encontrad" in str(error) else 400
    return JSONResponse(status_code=status, content={"detail": str(error)})


class NewImport(BaseModel):
    project_id: int
    source_label: str = ""
    client: Dict[str, Any] = Field(default_factory=dict)


class CloseImport(BaseModel):
    summary: Dict[str, Any] = Field(default_factory=dict)
    ignored: List[Dict[str, Any]] = Field(default_factory=list)


@app.get(PREFIX + "/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get(PREFIX + "/projects")
def projects(organization_id: int = Depends(organization)) -> List[Dict[str, Any]]:
    return jornada_imports.list_projects(organization_id)


@app.post(PREFIX + "/imports")
def new_import(body: NewImport, organization_id: int = Depends(organization)) -> Dict[str, Any]:
    batch_id = jornada_imports.create_batch(organization_id, body.project_id, body.source_label, body.client)
    return {"batch_id": batch_id, "known": jornada_imports.known_hashes(organization_id, body.project_id)}


@app.get(PREFIX + "/imports/{batch_id}/files/status")
def file_status(batch_id: int, path: str, organization_id: int = Depends(organization)) -> Dict[str, Any]:
    return jornada_imports.file_status(organization_id, batch_id, path)


def _header(request: Request, name: str, required: bool = True) -> str:
    value = request.headers.get(name, "")
    if required and not value:
        raise HTTPException(status_code=400, detail="Cabeçalho {} ausente.".format(name))
    return unquote(value)


@app.put(PREFIX + "/imports/{batch_id}/files")
async def receive(batch_id: int, request: Request, organization_id: int = Depends(organization)) -> Dict[str, Any]:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > CHUNK_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Bloco maior que {} MB.".format(CHUNK_MAX_BYTES // 1024 ** 2))
    data = await request.body()
    if len(data) > CHUNK_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Bloco maior que {} MB.".format(CHUNK_MAX_BYTES // 1024 ** 2))
    try:
        size = int(_header(request, "x-file-size"))
        offset = int(_header(request, "x-chunk-offset"))
        meta = json.loads(_header(request, "x-file-meta", required=False) or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Cabeçalhos de tamanho, offset ou meta inválidos.")
    if not isinstance(meta, dict):
        raise HTTPException(status_code=400, detail="X-File-Meta precisa ser um objeto JSON.")
    # Gravar em disco e conferir o sha256 bloqueia: fora do laço de eventos.
    return await run_in_threadpool(
        jornada_imports.receive_chunk,
        organization_id, batch_id,
        rel_path=_header(request, "x-file-path"),
        role=_header(request, "x-file-role"),
        sha256=_header(request, "x-file-sha256"),
        size=size,
        offset=offset,
        data=data,
        meta=meta,
        source_sha256=_header(request, "x-source-sha256", required=False) or None,
    )


@app.post(PREFIX + "/imports/{batch_id}/close")
def close(batch_id: int, body: CloseImport, organization_id: int = Depends(organization)) -> Dict[str, Any]:
    return jornada_imports.close_batch(organization_id, batch_id, body.summary, body.ignored)
