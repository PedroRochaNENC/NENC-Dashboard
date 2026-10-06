"""
Importações pendentes da Jornada de Compra: o que o script enviou e ainda não
foi revisado.

O lote, os blocos, a retomada, a expiração e o descarte são do motor comum
(`utils/import_inbox.py`), com as tabelas `jc_import_*` e o inbox na raiz de
`NENC_IMPORT_INBOX` (`<org>/<projeto>/<lote>/<arquivo>.bin`), como sempre
foram. Aqui fica o que é da Jornada: o que conta como já enviado (arquivos,
vídeos e documentos), a tarefa deduzida da pasta e a gravação, com as funções
guardadas de `jornada_db` — o autor de cada arquivo é quem revisou.
"""

from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from utils import auth, import_inbox, jornada_db, jornada_media
from utils.import_inbox import (  # noqa: F401 (nomes que a API e a tela usam daqui)
    BATCH_MAX_BYTES,
    OPEN_STATUSES,
    STATUSES,
    ChunkOutOfOrder,
    ImportRefused,
)
from utils.jornada_folder import CHUNK_BYTES, ROLES, file_limit, folder_task
from utils.jornada_ingest import normalize_participant, parse_upload, store_key

UPLOAD_ROLES = tuple(role for role in ROLES if role != "ignorado")
CHUNK_MAX_BYTES = CHUNK_BYTES  # limites por arquivo: jornada_folder.file_limit
_IMAGE_CATEGORY = {"gondola": "gôndola", "heatmap": "heatmap", "embalagem": "embalagem", "outra": "outra"}


def _known(conn, project_id: int) -> Dict[str, List[str]]:
    files = [r["sha256"] for r in conn.execute("SELECT sha256 FROM jc_files WHERE project_id = ?", (project_id,))]
    media = []
    for row in conn.execute("SELECT sha256, source_sha256 FROM jc_media WHERE project_id = ?", (project_id,)):
        media += [value for value in (row["sha256"], row["source_sha256"]) if value]
    # Documentos gravados não ficam em jc_files (viram Contexto e base de
    # conhecimento): sem isto, cada envio mandaria o relatório de novo para a base.
    # O que a revisão deixou de fora não conta: volta no próximo envio.
    documents = [r["sha256"] for r in conn.execute(
        "SELECT f.sha256 FROM jc_import_files f JOIN jc_import_batches b ON b.id = f.batch_id "
        "WHERE b.project_id = ? AND b.status = 'gravada' AND f.role = 'documento' AND f.status = 'gravado'",
        (project_id,))]
    return {"files": files, "media": media, "documents": documents}


def _decorate(item: Dict) -> None:
    # Gravação cujo nome não disse a tarefa (enviada por uma versão anterior do
    # script): a pasta do caminho resolve, como no leitor da pasta.
    if item["role"] in ("quadros", "video_cena", "video_heatmap") and not item["meta"].get("task"):
        task, folder = folder_task(PurePosixPath(item["rel_path"]).parts[:-1])
        if task:
            item["meta"].update(task=task, task_from_folder=folder)


_INBOX = import_inbox.Inbox(
    db=jornada_db,
    audit_prefix="jornada",
    projects_table="jc_projects",
    batches_table="jc_import_batches",
    files_table="jc_import_files",
    target_type="jc_import_batch",
    subdir="",
    roles=UPLOAD_ROLES,
    chunk_max_bytes=CHUNK_MAX_BYTES,
    file_limit=file_limit,
    known=_known,
    decorate=_decorate,
)


def inbox_root() -> Path:
    return _INBOX.root()


def _file_path(batch: Dict, file_id: int) -> Path:
    return _INBOX.file_path(batch, file_id)


# ---------------------------------------------------------------------------
# API: organização explícita
# ---------------------------------------------------------------------------

def list_projects(organization_id: int) -> List[Dict]:
    """Projetos da organização do token: só id e nome."""
    return _INBOX.list_projects(organization_id)


def expire_stale(now: Optional[datetime] = None) -> int:
    """Lotes abertos há mais que o prazo viram `expirada` e perdem os arquivos."""
    return _INBOX.expire_stale(now)


def open_batch(organization_id: int, project_id: int, source_label: str = "",
               client_info: Optional[Dict] = None, resume: bool = True) -> Dict[str, object]:
    """Lote para um envio: retoma o que ficou recebendo ou abre outro."""
    return _INBOX.open_batch(organization_id, project_id, source_label, client_info, resume)


def create_batch(organization_id: int, project_id: int, source_label: str = "",
                 client_info: Optional[Dict] = None) -> int:
    """Sempre um lote novo (a API usa `open_batch`, que retoma)."""
    return _INBOX.create_batch(organization_id, project_id, source_label, client_info)


def known_hashes(organization_id: int, project_id: int) -> Dict[str, List[str]]:
    """O que o projeto já tem (ou tem pendente): o script não reenvia."""
    return _INBOX.known_hashes(organization_id, project_id)


def receive_chunk(organization_id: int, batch_id: int, **chunk) -> Dict[str, object]:
    """Recebe um bloco de um arquivo do lote (ver `import_inbox.Inbox.receive_chunk`)."""
    return _INBOX.receive_chunk(organization_id, batch_id, **chunk)


def file_status(organization_id: int, batch_id: int, rel_path: str) -> Dict[str, object]:
    """Quanto de um arquivo já chegou: o script retoma a partir daí."""
    return _INBOX.file_status(organization_id, batch_id, rel_path)


def close_batch(organization_id: int, batch_id: int, summary: Optional[Dict] = None,
                ignored: Sequence[Dict] = ()) -> Dict[str, object]:
    """Fecha o lote para revisão. Arquivo incompleto impede o fechamento."""
    return _INBOX.close_batch(organization_id, batch_id, summary, ignored)


# ---------------------------------------------------------------------------
# Tela: sessão, guardas de escrita
# ---------------------------------------------------------------------------

def list_batches(project_id: int, statuses: Iterable[str] = OPEN_STATUSES) -> List[Dict]:
    """Lotes do projeto (da organização ativa), com o tamanho e a contagem por papel."""
    return _INBOX.list_batches(project_id, statuses)


def batch_files(project_id: int, batch_id: int) -> List[Dict]:
    return _INBOX.batch_files(project_id, batch_id)


def missing_files(project_id: int, batch_id: int) -> List[str]:
    """Arquivos completos do lote que não estão no inbox (ex.: banco restaurado sem a pasta)."""
    return _INBOX.missing_files(project_id, batch_id)


def read_file(project_id: int, batch_id: int, file_id: int) -> bytes:
    """Conteúdo de um arquivo pendente (dados, imagens, documentos) para a prévia."""
    return _INBOX.read_file(project_id, batch_id, file_id)


def file_item(name: str, content: bytes, parsed, fix: Optional[Dict] = None,
              meta_hint: Optional[Dict] = None) -> Optional[Dict]:
    """Item para `jornada_db.add_files`, no mesmo formato que a tela de Uploads grava.

    `fix` é o que a revisão completou (tarefa, loja, grupo, participante);
    `meta_hint` é o que o leitor da pasta deduziu (loja e marca de fotos).
    Devolve None quando falta um campo obrigatório.
    """
    fix = dict(fix or {})
    hint = dict(meta_hint or {})
    needs = parsed.meta.get("needs") or []
    if any(not str(fix.get(field) or "").strip() for field in needs if field != "store"):
        return None
    overrides = {"kind": parsed.kind}
    if fix.get("task"):
        overrides["task"] = fix["task"]
    if parsed.kind != "image" and "store" in needs:
        overrides["store"] = store_key(fix.get("store", ""))
        overrides["store_label"] = str(fix.get("store", "")).strip()
    if fix.get("group"):
        overrides["group"] = str(fix["group"]).strip().upper()
    if fix.get("participant"):
        overrides["participant"] = normalize_participant(fix["participant"])
    detected = {key: parsed.meta.get(key) for key in (
        "participant", "task", "store", "store_label", "group", "n_rows", "participants", "tasks", "stores")
        if parsed.meta.get(key) not in (None, "", [], {})}
    meta = {"overrides": overrides, "detected": detected}
    if parsed.kind == "gaze_frames":
        meta["frames"] = parsed.meta.get("frames")
    if parsed.kind == "image":
        category = fix.get("category") or _IMAGE_CATEGORY.get(str(hint.get("category") or ""), "")
        meta.update(
            caption=name.rsplit(".", 1)[0],
            category=category,
            store=store_key(fix.get("store") or hint.get("store") or ""),
            brand=str(fix.get("brand") or hint.get("brand") or "").strip(),
            view=str(hint.get("view") or ""),
            edited=bool(hint.get("edited")),
        )
    return {"filename": name, "kind": parsed.kind, "content": content, "meta": meta}


def apply_batch(
    project_id: int,
    batch_id: int,
    fixes: Optional[Dict[int, Dict]] = None,
    *,
    selected: Optional[Iterable[int]] = None,
    send_document: Optional[Callable[[str, str, Dict], None]] = None,
) -> Dict[str, object]:
    """Grava o lote como o revisor: arquivos, vídeos, documentos e escolhas.

    `fixes` completa o que a prévia pediu, por id de arquivo; `selected`
    restringe aos arquivos marcados (os outros são descartados com o lote).
    `send_document(filename, text, meta)` manda um documento para a base de
    conhecimento; sem ele, os documentos só alimentam o Contexto do projeto.
    """
    fixes = fixes or {}
    return _INBOX.apply(project_id, batch_id,
                        lambda _actor, batch: _apply(project_id, batch, fixes, selected, send_document))


def _apply(project_id: int, batch: Dict, fixes: Dict[int, Dict], selected, send_document):
    """Grava os arquivos do lote; devolve o relatório e os ids que entraram no projeto."""
    batch_id = batch["id"]
    project = jornada_db.get_project(project_id)
    files = [f for f in batch_files(project_id, batch_id) if f["status"] == "completo"]
    if selected is not None:
        keep = {int(file_id) for file_id in selected}
        files = [f for f in files if f["id"] in keep]

    items, item_ids, seed, skipped, warnings = [], [], [], [], []
    recorded: List[int] = []  # arquivos que entraram no projeto; os outros voltam num próximo envio
    report = {"files": 0, "duplicates": 0, "videos": 0, "documents": 0, "skipped": skipped, "warnings": warnings}
    briefing_set = bool((project or {}).get("briefing_text"))
    for entry in files:
        path = _file_path(batch, entry["id"])
        name = PurePosixPath(entry["rel_path"]).name
        meta = entry["meta"]
        if entry["role"] in ("video_cena", "video_heatmap"):
            participant = normalize_participant(fixes.get(entry["id"], {}).get("participant") or meta.get("participant"))
            task = fixes.get(entry["id"], {}).get("task") or meta.get("task")
            store = store_key(fixes.get(entry["id"], {}).get("store") or meta.get("store") or "")
            if not participant or not task:
                skipped.append(name)
                continue
            stored = jornada_media.adopt_video(batch["organization_id"], project_id, name, path)
            _, created = jornada_db.add_media(
                project_id, participant_code=participant, task=task, store=store, filename=name,
                kind="heatmap" if entry["role"] == "video_heatmap" else "cena",
                source_sha256=entry.get("source_sha256"), **stored,
            )
            report["videos" if created else "duplicates"] += 1
            recorded.append(entry["id"])
            continue
        if entry["role"] == "documento":
            text = path.read_text(encoding="utf-8", errors="replace")
            original = meta.get("original_name") or name
            reached = False
            if meta.get("doc_type") == "briefing" and not briefing_set:
                try:
                    jornada_db.update_project(project_id, briefing_text=text, briefing_filename=original)
                    briefing_set = reached = True
                except auth.AuthorizationError:
                    warnings.append("{}: o Contexto só é editado por quem criou o projeto.".format(original))
            if send_document is not None:
                try:
                    send_document(original, text, meta)
                    reached = True
                except Exception as error:  # a base fora do ar não impede o resto
                    warnings.append("{}: não foi para a base ({}).".format(original, error))
            if reached:
                report["documents"] += 1
                recorded.append(entry["id"])
            else:
                skipped.append(original)
            continue
        content = path.read_bytes()
        parsed = parse_upload(name, content, {"kind": "image"} if entry["role"] == "imagem" else None)
        if not parsed.ok:
            skipped.append(name)
            continue
        item = file_item(name, content, parsed, fixes.get(entry["id"]), meta)
        if item is None:
            skipped.append(name)
            continue
        items.append(item)
        item_ids.append(entry["id"])
        if parsed.kind == "bs_enriched_xlsx":
            seed.extend(parsed.meta.get("participant_info") or [])
    if items:
        result = jornada_db.add_files(project_id, items)
        report["files"] += len(result["added"])
        report["duplicates"] += len(result["duplicates"])
        recorded.extend(item_ids)  # repetidos também já estão no projeto
    if seed:
        jornada_db.upsert_participants(project_id, seed, only_missing=True, source="importacao")
    return report, recorded


def discard_batch(project_id: int, batch_id: int) -> None:
    _INBOX.discard_batch(project_id, batch_id)


def remove_project_inbox(organization_id: int, project_id: int) -> None:
    """Chamado ao excluir o projeto: o que estava pendente sai do disco."""
    _INBOX.remove_project_inbox(organization_id, project_id)
