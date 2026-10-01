"""
Importações pendentes da Jornada de Compra: o que o script enviou e ainda não
foi revisado.

Duas metades, com guardas diferentes:

- **API** (`api/main.py`, token da organização, sem conta logada): cria o lote,
  recebe os arquivos em blocos e fecha. Toda função recebe a organização de
  forma explícita e confere que o projeto é dela. Nada aqui grava em tabela de
  análise: os arquivos ficam no inbox, fora do banco.
- **Tela** (Uploads, sessão com escrita): lista, grava ou descarta. Gravar usa
  as funções guardadas de `jornada_db`, então o autor de cada arquivo é quem
  revisou — o token não diz quem enviou.

O inbox fica em `NENC_IMPORT_INBOX` ou, sem a variável, em `jornada_inbox/`
ao lado do banco: `<org>/<projeto>/<lote>/<arquivo>.bin`. Lote parado vence em
`NENC_IMPORT_TTL_DAYS` dias (30 por padrão) e seus arquivos são apagados.
"""

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from utils import auth, jornada_db, jornada_media
from utils.jornada_folder import CHUNK_BYTES, ROLES, file_limit
from utils.jornada_ingest import normalize_participant, parse_upload, store_key

STATUSES = ("recebendo", "pronta", "gravando", "gravada", "descartada", "expirada")
OPEN_STATUSES = ("recebendo", "pronta")
UPLOAD_ROLES = tuple(role for role in ROLES if role != "ignorado")
CHUNK_MAX_BYTES = CHUNK_BYTES  # limites por arquivo: jornada_folder.file_limit
BATCH_MAX_BYTES = 10 * 1024 * 1024 * 1024
_IMAGE_CATEGORY = {"gondola": "gôndola", "heatmap": "heatmap", "embalagem": "embalagem", "outra": "outra"}


class ImportRefused(ValueError):
    """Recusa com mensagem para quem envia (vira 4xx na API)."""


class ChunkOutOfOrder(ImportRefused):
    """Bloco que não começa onde o arquivo parou; `expected` diz o offset certo."""

    def __init__(self, expected: int):
        super().__init__("Bloco fora de ordem: esperado o offset {}.".format(expected))
        self.expected = int(expected)


def inbox_root() -> Path:
    configured = os.environ.get("NENC_IMPORT_INBOX", "").strip()
    if configured:
        return Path(configured).expanduser()
    return jornada_db._database_path().parent / "jornada_inbox"


def _ttl_days() -> int:
    try:
        return max(1, int(os.environ.get("NENC_IMPORT_TTL_DAYS", "30")))
    except ValueError:
        return 30


def _batch_dir(organization_id: int, project_id: int, batch_id: int) -> Path:
    return inbox_root() / str(int(organization_id)) / str(int(project_id)) / str(int(batch_id))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _project_of_org(conn, organization_id: int, project_id: int) -> Dict:
    row = conn.execute(
        "SELECT id, name FROM jc_projects WHERE id = ? AND organization_id = ?",
        (int(project_id), int(organization_id)),
    ).fetchone()
    if row is None:
        raise ImportRefused("Projeto não encontrado nesta organização.")
    return dict(row)


def _batch_of_org(conn, organization_id: int, batch_id: int) -> Dict:
    row = conn.execute(
        "SELECT * FROM jc_import_batches WHERE id = ? AND organization_id = ?",
        (int(batch_id), int(organization_id)),
    ).fetchone()
    if row is None:
        raise ImportRefused("Importação não encontrada nesta organização.")
    return dict(row)


def _remove_dir(path: Path) -> None:
    root = inbox_root().resolve()
    try:
        target = path.resolve()
    except OSError:
        return
    if target == root or root not in target.parents:
        return
    shutil.rmtree(target, ignore_errors=True)


# ---------------------------------------------------------------------------
# API: organização explícita
# ---------------------------------------------------------------------------

def list_projects(organization_id: int) -> List[Dict]:
    """Projetos da organização do token: só id e nome."""
    jornada_db.init_db()
    with jornada_db._connect() as conn:
        rows = conn.execute(
            "SELECT id, name FROM jc_projects WHERE organization_id = ? ORDER BY name",
            (int(organization_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def expire_stale(now: Optional[datetime] = None) -> int:
    """Lotes abertos há mais que o prazo viram `expirada` e perdem os arquivos."""
    limit = ((now or datetime.now()) - timedelta(days=_ttl_days())).strftime("%Y-%m-%d %H:%M:%S")
    with jornada_db._connect() as conn:
        rows = conn.execute(
            "SELECT id, organization_id, project_id FROM jc_import_batches "
            "WHERE status IN ('recebendo', 'pronta') AND COALESCE(updated_at, created_at) < ?",
            (limit,),
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE jc_import_batches SET status = 'expirada', updated_at = ? WHERE id = ?",
                         (_now(), row["id"]))
    for row in rows:
        _remove_dir(_batch_dir(row["organization_id"], row["project_id"], row["id"]))
    return len(rows)


def open_batch(organization_id: int, project_id: int, source_label: str = "",
               client_info: Optional[Dict] = None, resume: bool = True) -> Dict[str, object]:
    """Lote para um envio: retoma o que ficou recebendo ou abre outro.

    Um envio interrompido deixa o lote em `recebendo`. Retomá-lo evita que os
    arquivos que já chegaram fiquem presos num lote que nunca fecha: o script
    pula o que já está lá e o protocolo de blocos continua o arquivo do meio.
    """
    jornada_db.init_db()
    expire_stale()
    label = str(source_label or "")[:300]
    client = json.dumps(client_info or {}, ensure_ascii=False)[:2000]
    with jornada_db._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _project_of_org(conn, organization_id, project_id)
        row = conn.execute(
            "SELECT id FROM jc_import_batches WHERE organization_id = ? AND project_id = ? "
            "AND status = 'recebendo' ORDER BY id DESC LIMIT 1",
            (int(organization_id), int(project_id)),
        ).fetchone() if resume else None
        if row is not None:
            batch_id = int(row["id"])
            conn.execute("UPDATE jc_import_batches SET source_label = ?, client_json = ?, updated_at = ? WHERE id = ?",
                         (label, client, _now(), batch_id))
        else:
            cursor = conn.execute(
                "INSERT INTO jc_import_batches (organization_id, project_id, status, source_label, client_json, "
                "created_at, updated_at) VALUES (?, ?, 'recebendo', ?, ?, ?, ?)",
                (int(organization_id), int(project_id), label, client, _now(), _now()),
            )
            batch_id = int(cursor.lastrowid)
    action = "jornada.import.resumed" if row is not None else "jornada.import.received"
    auth.audit_system_event(organization_id, action, "jc_import_batch", batch_id,
                            {"origem": "api", "project_id": int(project_id)})
    return {"batch_id": batch_id, "resumed": row is not None}


def create_batch(organization_id: int, project_id: int, source_label: str = "",
                 client_info: Optional[Dict] = None) -> int:
    """Sempre um lote novo (a API usa `open_batch`, que retoma)."""
    return int(open_batch(organization_id, project_id, source_label, client_info, resume=False)["batch_id"])


def known_hashes(organization_id: int, project_id: int) -> Dict[str, List[str]]:
    """O que o projeto já tem (ou tem pendente): o script não reenvia."""
    with jornada_db._connect() as conn:
        _project_of_org(conn, organization_id, project_id)
        files = [r["sha256"] for r in conn.execute("SELECT sha256 FROM jc_files WHERE project_id = ?",
                                                   (int(project_id),))]
        media = []
        for row in conn.execute("SELECT sha256, source_sha256 FROM jc_media WHERE project_id = ?",
                                (int(project_id),)):
            media += [value for value in (row["sha256"], row["source_sha256"]) if value]
        pending = []
        for row in conn.execute(
            "SELECT f.sha256, f.source_sha256 FROM jc_import_files f JOIN jc_import_batches b ON b.id = f.batch_id "
            "WHERE b.project_id = ? AND b.status IN ('recebendo', 'pronta') AND f.status = 'completo'",
            (int(project_id),),
        ):
            pending += [value for value in (row["sha256"], row["source_sha256"]) if value]
        # Documentos gravados não ficam em jc_files (viram Contexto e base de
        # conhecimento): sem isto, cada envio mandaria o relatório de novo para a base.
        documents = [r["sha256"] for r in conn.execute(
            "SELECT f.sha256 FROM jc_import_files f JOIN jc_import_batches b ON b.id = f.batch_id "
            "WHERE b.project_id = ? AND b.status = 'gravada' AND f.role = 'documento'", (int(project_id),))]
    return {"files": sorted(set(files)), "media": sorted(set(media)), "pending": sorted(set(pending)),
            "documents": sorted(set(documents))}


def _clean_rel_path(rel_path: str) -> str:
    """Caminho relativo só como metadado: sem raiz, sem `..`, com barras normais."""
    parts = [part for part in PurePosixPath(str(rel_path).replace("\\", "/")).parts
             if part not in ("", ".", "..", "/")]
    if not parts:
        raise ImportRefused("Caminho do arquivo vazio.")
    return "/".join(parts)[:500]


def receive_chunk(
    organization_id: int,
    batch_id: int,
    *,
    rel_path: str,
    role: str,
    sha256: str,
    size: int,
    offset: int,
    data: bytes,
    meta: Optional[Dict] = None,
    source_sha256: Optional[str] = None,
) -> Dict[str, object]:
    """Recebe um bloco de um arquivo do lote, na ordem (o `offset` diz onde ele entra).

    Bloco repetido (o cliente reenviou depois de uma falha de rede) é aceito sem
    gravar de novo; bloco fora de ordem é recusado com o offset esperado. No
    último bloco o sha256 do arquivo inteiro é conferido.
    """
    if role not in UPLOAD_ROLES:
        raise ImportRefused("Papel de arquivo desconhecido: {}.".format(role))
    sha256 = str(sha256 or "").lower()
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ImportRefused("sha256 inválido.")
    size, offset = int(size), int(offset)
    limit = file_limit(role)
    if size <= 0 or size > limit:
        raise ImportRefused("Tamanho fora do limite para {} ({} MB).".format(role, limit // (1024 * 1024)))
    if len(data) > CHUNK_MAX_BYTES:
        raise ImportRefused("Bloco maior que {} MB.".format(CHUNK_MAX_BYTES // (1024 * 1024)))
    rel_path = _clean_rel_path(rel_path)
    with jornada_db._connect() as conn:
        # Um bloco por vez no lote: ler o quanto chegou e anexar no disco precisa ser atômico.
        conn.execute("BEGIN IMMEDIATE")
        batch = _batch_of_org(conn, organization_id, batch_id)
        if batch["status"] != "recebendo":
            raise ImportRefused("A importação não está mais recebendo arquivos ({}).".format(batch["status"]))
        folder = _batch_dir(batch["organization_id"], batch["project_id"], batch_id)
        row = conn.execute("SELECT * FROM jc_import_files WHERE batch_id = ? AND rel_path = ?",
                           (int(batch_id), rel_path)).fetchone()
        if row is not None and (row["sha256"] != sha256 or int(row["size_bytes"]) != size):
            if offset != 0:
                raise ImportRefused("O arquivo mudou no meio do envio; recomece este arquivo do início.")
            # Mudou entre um envio e outro (lote retomado): recomeça do zero.
            conn.execute("DELETE FROM jc_import_files WHERE id = ?", (row["id"],))
            conn.commit()
            for suffix in (".part", ".bin"):
                (folder / "{}{}".format(row["id"], suffix)).unlink(missing_ok=True)
            conn.execute("BEGIN IMMEDIATE")
            row = None
        if row is None:
            if offset != 0:
                raise ImportRefused("O primeiro bloco precisa começar no offset 0.")
            total = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM jc_import_files WHERE batch_id = ?",
                                 (int(batch_id),)).fetchone()[0]
            if total + size > BATCH_MAX_BYTES:
                raise ImportRefused("A importação passaria de {} GB.".format(BATCH_MAX_BYTES // 1024 ** 3))
            cursor = conn.execute(
                "INSERT INTO jc_import_files (batch_id, rel_path, role, meta_json, sha256, source_sha256, size_bytes, "
                "received_bytes, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'recebendo', ?)",
                (int(batch_id), rel_path, role, json.dumps(meta or {}, ensure_ascii=False), sha256,
                 (source_sha256 or None), size, _now()),
            )
            row = conn.execute("SELECT * FROM jc_import_files WHERE id = ?", (cursor.lastrowid,)).fetchone()
        row = dict(row)
        if row["status"] == "completo":
            return {"file_id": row["id"], "received": int(row["size_bytes"]), "complete": True}
        received = int(row["received_bytes"])
        if offset + len(data) <= received:
            return {"file_id": row["id"], "received": received, "complete": False}
        if offset != received:
            raise ChunkOutOfOrder(received)
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / "{}.part".format(row["id"])
        with part.open("ab") as handle:
            handle.write(data)
        received += len(data)
        if received > size:
            part.unlink(missing_ok=True)
            conn.execute("UPDATE jc_import_files SET status = 'invalido', received_bytes = 0 WHERE id = ?",
                         (row["id"],))
            conn.commit()  # a recusa desfaria a marca de inválido
            raise ImportRefused("Recebido mais do que o tamanho declarado.")
        status = "recebendo"
        if received == size:
            digest = hashlib.sha256()
            with part.open("rb") as handle:
                for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != sha256:
                part.unlink(missing_ok=True)
                conn.execute("UPDATE jc_import_files SET status = 'invalido', received_bytes = 0 WHERE id = ?",
                             (row["id"],))
                conn.commit()
                raise ImportRefused("O sha256 do arquivo recebido não confere; envie de novo.")
            os.replace(part, folder / "{}.bin".format(row["id"]))
            status = "completo"
        conn.execute("UPDATE jc_import_files SET received_bytes = ?, status = ? WHERE id = ?",
                     (received, status, row["id"]))
        conn.execute("UPDATE jc_import_batches SET updated_at = ? WHERE id = ?", (_now(), int(batch_id)))
    return {"file_id": row["id"], "received": received, "complete": status == "completo"}


def file_status(organization_id: int, batch_id: int, rel_path: str) -> Dict[str, object]:
    """Quanto de um arquivo já chegou: o script retoma a partir daí."""
    with jornada_db._connect() as conn:
        _batch_of_org(conn, organization_id, batch_id)
        row = conn.execute("SELECT id, received_bytes, status FROM jc_import_files WHERE batch_id = ? AND rel_path = ?",
                           (int(batch_id), _clean_rel_path(rel_path))).fetchone()
    if row is None:
        return {"received": 0, "complete": False}
    return {"file_id": row["id"], "received": int(row["received_bytes"]), "complete": row["status"] == "completo"}


def close_batch(organization_id: int, batch_id: int, summary: Optional[Dict] = None,
                ignored: Sequence[Dict] = ()) -> Dict[str, object]:
    """Fecha o lote para revisão. Arquivo incompleto impede o fechamento."""
    with jornada_db._connect() as conn:
        batch = _batch_of_org(conn, organization_id, batch_id)
        if batch["status"] != "recebendo":
            raise ImportRefused("A importação já foi fechada ({}).".format(batch["status"]))
        incomplete = [r["rel_path"] for r in conn.execute(
            "SELECT rel_path FROM jc_import_files WHERE batch_id = ? AND status != 'completo'", (int(batch_id),))]
        if incomplete:
            raise ImportRefused("Arquivos incompletos: {}.".format(", ".join(incomplete[:5])))
        count = conn.execute("SELECT COUNT(*) FROM jc_import_files WHERE batch_id = ?", (int(batch_id),)).fetchone()[0]
        # Sem arquivo novo não há o que revisar: o lote não vira pendência vazia.
        status = "pronta" if count else "descartada"
        conn.execute(
            "UPDATE jc_import_batches SET status = ?, summary_json = ?, ignored_json = ?, closed_at = ?, "
            "updated_at = ? WHERE id = ?",
            (status, json.dumps(summary or {}, ensure_ascii=False)[:20000],
             json.dumps(list(ignored)[:2000], ensure_ascii=False), _now(), _now(), int(batch_id)),
        )
    if not count:
        _remove_dir(_batch_dir(batch["organization_id"], batch["project_id"], batch_id))
    auth.audit_system_event(organization_id, "jornada.import.closed", "jc_import_batch", batch_id,
                            {"origem": "api", "arquivos": int(count)})
    return {"batch_id": int(batch_id), "files": int(count), "status": status}


# ---------------------------------------------------------------------------
# Tela: sessão, guardas de escrita
# ---------------------------------------------------------------------------

def _decode(row) -> Dict:
    item = dict(row)
    for key, default in (("summary_json", {}), ("ignored_json", []), ("client_json", {})):
        try:
            item[key.replace("_json", "")] = json.loads(item.pop(key) or "null") or default
        except ValueError:
            item[key.replace("_json", "")] = default
    return item


def list_batches(project_id: int, statuses: Iterable[str] = OPEN_STATUSES) -> List[Dict]:
    """Lotes do projeto (da organização ativa), com o tamanho e a contagem por papel."""
    expire_stale()
    statuses = tuple(statuses)
    with jornada_db._connect() as conn:
        jornada_db._project_org(conn, project_id, jornada_db._active_organization_id())
        rows = conn.execute(
            "SELECT * FROM jc_import_batches WHERE project_id = ? AND status IN ({}) ORDER BY id DESC".format(
                ",".join("?" * len(statuses))),
            [int(project_id)] + list(statuses),
        ).fetchall()
        batches = [_decode(row) for row in rows]
        for batch in batches:
            counts = conn.execute(
                "SELECT role, COUNT(*) AS files, SUM(size_bytes) AS bytes FROM jc_import_files "
                "WHERE batch_id = ? GROUP BY role", (batch["id"],)).fetchall()
            batch["roles"] = {row["role"]: {"files": row["files"], "bytes": row["bytes"]} for row in counts}
    return batches


def batch_files(project_id: int, batch_id: int) -> List[Dict]:
    with jornada_db._connect() as conn:
        jornada_db._project_org(conn, project_id, jornada_db._active_organization_id())
        rows = conn.execute(
            "SELECT f.* FROM jc_import_files f JOIN jc_import_batches b ON b.id = f.batch_id "
            "WHERE f.batch_id = ? AND b.project_id = ? ORDER BY f.role, f.rel_path",
            (int(batch_id), int(project_id)),
        ).fetchall()
    files = []
    for row in rows:
        item = dict(row)
        item["meta"] = json.loads(item.pop("meta_json") or "{}")
        files.append(item)
    return files


def _file_path(batch: Dict, file_id: int) -> Path:
    return _batch_dir(batch["organization_id"], batch["project_id"], batch["id"]) / "{}.bin".format(int(file_id))


def _open_batch(conn, project_id: int, batch_id: int) -> Dict:
    jornada_db._project_org(conn, project_id, jornada_db._active_organization_id())
    row = conn.execute("SELECT * FROM jc_import_batches WHERE id = ? AND project_id = ?",
                       (int(batch_id), int(project_id))).fetchone()
    if row is None:
        raise ValueError("Importação não encontrada neste projeto.")
    return dict(row)


def read_file(project_id: int, batch_id: int, file_id: int) -> bytes:
    """Conteúdo de um arquivo pendente (dados, imagens, documentos) para a prévia."""
    with jornada_db._connect() as conn:
        batch = _open_batch(conn, project_id, batch_id)
    return _file_path(batch, file_id).read_bytes()


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
    actor = jornada_db._require_write()
    fixes = fixes or {}
    with jornada_db._connect() as conn:
        batch = _open_batch(conn, project_id, batch_id)
        # Reserva o lote: dois revisores clicando juntos não gravam duas vezes.
        claimed = conn.execute(
            "UPDATE jc_import_batches SET status = 'gravando', updated_at = ? WHERE id = ? AND status = 'pronta'",
            (_now(), int(batch_id)),
        ).rowcount
    if not claimed:
        raise ValueError("Só uma importação pronta pode ser gravada ({}).".format(batch["status"]))
    try:
        return _apply(actor, project_id, batch, fixes, selected, send_document)
    except BaseException:
        # Gravar de novo é seguro (arquivos e vídeos não duplicam): o lote volta a ficar pronto.
        with jornada_db._connect() as conn:
            conn.execute("UPDATE jc_import_batches SET status = 'pronta', updated_at = ? WHERE id = ? "
                         "AND status = 'gravando'", (_now(), int(batch_id)))
        raise


def _apply(actor, project_id: int, batch: Dict, fixes: Dict[int, Dict], selected, send_document) -> Dict:
    batch_id = batch["id"]
    project = jornada_db.get_project(project_id)
    files = [f for f in batch_files(project_id, batch_id) if f["status"] == "completo"]
    if selected is not None:
        keep = {int(file_id) for file_id in selected}
        files = [f for f in files if f["id"] in keep]

    items, seed, skipped, warnings = [], [], [], []
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
            continue
        if entry["role"] == "documento":
            text = path.read_text(encoding="utf-8", errors="replace")
            original = meta.get("original_name") or name
            if meta.get("doc_type") == "briefing" and not briefing_set:
                try:
                    jornada_db.update_project(project_id, briefing_text=text, briefing_filename=original)
                    briefing_set = True
                except auth.AuthorizationError:
                    warnings.append("{}: o Contexto só é editado por quem criou o projeto.".format(original))
            if send_document is not None:
                try:
                    send_document(original, text, meta)
                except Exception as error:  # a base fora do ar não impede o resto
                    warnings.append("{}: não foi para a base ({}).".format(original, error))
            report["documents"] += 1
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
        if parsed.kind == "bs_enriched_xlsx":
            seed.extend(parsed.meta.get("participant_info") or [])
    if items:
        result = jornada_db.add_files(project_id, items)
        report["files"] += len(result["added"])
        report["duplicates"] += len(result["duplicates"])
    if seed:
        jornada_db.upsert_participants(project_id, seed, only_missing=True, source="importacao")
    with jornada_db._connect() as conn:
        conn.execute(
            "UPDATE jc_import_batches SET status = 'gravada', decided_at = ?, decided_by_user_id = ?, "
            "updated_at = ? WHERE id = ?",
            (_now(), getattr(actor, "id", None) if isinstance(getattr(actor, "id", None), int) else None,
             _now(), int(batch_id)),
        )
    jornada_db._audit("jornada.import.apply", "jc_import_batch", int(batch_id), batch["organization_id"],
                      write=True)
    _remove_dir(_batch_dir(batch["organization_id"], project_id, batch_id))
    return report


def discard_batch(project_id: int, batch_id: int) -> None:
    actor = jornada_db._require_write()
    with jornada_db._connect() as conn:
        batch = _open_batch(conn, project_id, batch_id)
        if batch["status"] not in OPEN_STATUSES:
            raise ValueError("Esta importação já foi decidida ({}).".format(batch["status"]))
        conn.execute(
            "UPDATE jc_import_batches SET status = 'descartada', decided_at = ?, decided_by_user_id = ?, "
            "updated_at = ? WHERE id = ?",
            (_now(), getattr(actor, "id", None) if isinstance(getattr(actor, "id", None), int) else None,
             _now(), int(batch_id)),
        )
    jornada_db._audit("jornada.import.discard", "jc_import_batch", int(batch_id), batch["organization_id"],
                      write=True)
    _remove_dir(_batch_dir(batch["organization_id"], project_id, batch_id))


def remove_project_inbox(organization_id: int, project_id: int) -> None:
    """Chamado ao excluir o projeto: o que estava pendente sai do disco."""
    _remove_dir(inbox_root() / str(int(organization_id)) / str(int(project_id)))
