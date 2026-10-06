"""
Importações pendentes do Teste Sensorial: o que o script enviou e ainda não
foi revisado.

O lote, os blocos, a retomada, a expiração e o descarte são do motor comum
(`utils/import_inbox.py`), com as tabelas `sens_import_*` e o inbox em
`<NENC_IMPORT_INBOX>/teste_sensorial/<org>/<projeto>/<lote>/`. Aqui fica o
que é do módulo: os papéis e limites, e a gravação — cada arquivo é lido por
`utils/sensorial_ingest.py` (o nome do participante sai nessa hora) e entra
pelas funções guardadas de `sensorial_db`, com a conta de quem revisou.

Uma rodada nova do pipeline substitui a anterior: a tabela de um papel único
(indicadores, PSD, métricas...) desativa a que estava ativa. Imagens e
manifestos ficam todos, cada um com a rodada de onde veio.
"""

from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from utils import auth, briefing, import_inbox, sensorial_db, sensorial_ingest
from utils.import_inbox import OPEN_STATUSES

UPLOAD_ROLES = tuple(sensorial_ingest.ROLES)
CHUNK_MAX_BYTES = 8 * 1024 * 1024
_MB = 1024 * 1024
# O PSD por janela de um estudo passa de 150 MB em CSV (o script comprime).
FILE_LIMITS = {
    "eeg_psd": 1024 * _MB,
    "eeg_indicadores": 512 * _MB,
    "perifericos_metricas": 512 * _MB,
    "base_limpa_chaves": 256 * _MB,
    "documento": 100 * _MB,
    "literatura": 100 * _MB,
}
DEFAULT_FILE_LIMIT = 100 * _MB
IMAGE_FILE_LIMIT = 25 * _MB


def file_limit(role: str) -> int:
    """Maior arquivo que a importação aceita para o papel, em bytes."""
    if sensorial_ingest.ROLES.get(role, {}).get("kind") == "image":
        return IMAGE_FILE_LIMIT
    return FILE_LIMITS.get(role, DEFAULT_FILE_LIMIT)


def _known(conn, project_id: int) -> Dict[str, List[str]]:
    files = []
    for row in conn.execute("SELECT sha256, source_sha256 FROM sens_files WHERE project_id = ?", (project_id,)):
        files += [value for value in (row["sha256"], row["source_sha256"]) if value]
    # As mesmas chaves da Jornada, para o script ler as duas respostas igual.
    return {"files": files, "media": [], "documents": []}


_INBOX = import_inbox.Inbox(
    db=sensorial_db,
    audit_prefix="sensorial",
    projects_table="sens_projects",
    batches_table="sens_import_batches",
    files_table="sens_import_files",
    target_type="sens_import_batch",
    subdir="teste_sensorial",
    roles=UPLOAD_ROLES,
    chunk_max_bytes=CHUNK_MAX_BYTES,
    file_limit=file_limit,
    known=_known,
)


def inbox_root() -> Path:
    return _INBOX.root()


def file_path(batch: Dict, file_id: int) -> Path:
    return _INBOX.file_path(batch, file_id)


# ---------------------------------------------------------------------------
# API: organização explícita
# ---------------------------------------------------------------------------

def list_projects(organization_id: int) -> List[Dict]:
    return _INBOX.list_projects(organization_id)


def expire_stale(now: Optional[datetime] = None) -> int:
    return _INBOX.expire_stale(now)


def open_batch(organization_id: int, project_id: int, source_label: str = "",
               client_info: Optional[Dict] = None, resume: bool = True) -> Dict[str, object]:
    return _INBOX.open_batch(organization_id, project_id, source_label, client_info, resume)


def create_batch(organization_id: int, project_id: int, source_label: str = "",
                 client_info: Optional[Dict] = None) -> int:
    return _INBOX.create_batch(organization_id, project_id, source_label, client_info)


def known_hashes(organization_id: int, project_id: int) -> Dict[str, List[str]]:
    return _INBOX.known_hashes(organization_id, project_id)


def receive_chunk(organization_id: int, batch_id: int, **chunk) -> Dict[str, object]:
    return _INBOX.receive_chunk(organization_id, batch_id, **chunk)


def file_status(organization_id: int, batch_id: int, rel_path: str) -> Dict[str, object]:
    return _INBOX.file_status(organization_id, batch_id, rel_path)


def close_batch(organization_id: int, batch_id: int, summary: Optional[Dict] = None,
                ignored: Sequence[Dict] = ()) -> Dict[str, object]:
    return _INBOX.close_batch(organization_id, batch_id, summary, ignored)


# ---------------------------------------------------------------------------
# Tela: sessão, guardas de escrita
# ---------------------------------------------------------------------------

def list_batches(project_id: int, statuses: Iterable[str] = OPEN_STATUSES) -> List[Dict]:
    return _INBOX.list_batches(project_id, statuses)


def batch_files(project_id: int, batch_id: int) -> List[Dict]:
    return _INBOX.batch_files(project_id, batch_id)


def missing_files(project_id: int, batch_id: int) -> List[str]:
    return _INBOX.missing_files(project_id, batch_id)


def read_file(project_id: int, batch_id: int, file_id: int) -> bytes:
    return _INBOX.read_file(project_id, batch_id, file_id)


def _extension(name: str) -> str:
    return "".join(PurePosixPath(name).suffixes)[-12:]


def file_item(entry: Dict, path: Path, parsed: sensorial_ingest.ParsedFile) -> Dict:
    """Item para `sensorial_db.add_files` a partir de um arquivo do lote já lido."""
    name = PurePosixPath(entry["rel_path"]).name
    single = bool(sensorial_ingest.ROLES[parsed.role]["single"])
    item = {
        "filename": name,
        "rel_path": entry["rel_path"],
        "role": parsed.role,
        "run_id": parsed.meta.get("run_id"),
        "meta": parsed.meta,
        "sha256": entry["sha256"],
        "source_sha256": entry.get("source_sha256"),
        "path": path,
        "extension": _extension(name),
        "supersede": single,
    }
    if parsed.table is not None:
        item["table"] = parsed.table
        item["table_version"] = sensorial_ingest.PARSER_VERSION
    return item


def _profile_rows(table) -> List[Dict]:
    attributes = [column for column in table.columns if column != "participant_code"]
    rows = []
    for record in table.to_dict("records"):
        profile = {column: record[column] for column in attributes if record.get(column)}
        rows.append({"code": record["participant_code"], "profile": profile})
    return rows


def apply_batch(
    project_id: int,
    batch_id: int,
    fixes: Optional[Dict[int, Dict]] = None,
    *,
    selected: Optional[Iterable[int]] = None,
    send_document: Optional[Callable[[str, bytes, Dict], None]] = None,
) -> Dict[str, object]:
    """Grava o lote como o revisor.

    `fixes` pode trocar o papel de um arquivo (`{"role": ...}`) ou marcar um
    documento como briefing (`{"doc_type": "briefing"}`); `selected` restringe
    aos arquivos marcados. `send_document(filename, content, meta)` manda um
    documento para a base de conhecimento.
    """
    fixes = fixes or {}
    return _INBOX.apply(project_id, batch_id,
                        lambda _actor, batch: _apply(project_id, batch, fixes, selected, send_document))


def _apply(project_id: int, batch: Dict, fixes: Dict[int, Dict], selected, send_document):
    files = [f for f in batch_files(project_id, batch["id"]) if f["status"] == "completo"]
    if selected is not None:
        keep = {int(file_id) for file_id in selected}
        files = [f for f in files if f["id"] in keep]
    # Rodadas em ordem: numa mesma importação, a mais nova de um papel fica ativa.
    files.sort(key=lambda f: (sensorial_ingest.run_id_from_path(f["rel_path"]) or "", f["rel_path"]))
    project = sensorial_db.get_project(project_id) or {}
    briefing_set = bool(project.get("briefing_text"))
    skipped: List[str] = []
    warnings: List[str] = []
    recorded: List[int] = []
    report = {"files": 0, "duplicates": 0, "participants": 0, "documents": 0, "skipped": skipped,
              "warnings": warnings}
    for entry in files:
        fix = fixes.get(entry["id"], {})
        name = PurePosixPath(entry["rel_path"]).name
        path = file_path(batch, entry["id"])
        role = fix.get("role") or entry["role"]
        # Uma tabela por vez: o PSD de um estudo ocupa dezenas de MB em memória.
        parsed = sensorial_ingest.parse_file(name, path, role=role, rel_path=entry["rel_path"])
        if not parsed.ok:
            reasons = "; ".join(issue["message"] for issue in parsed.issues if issue["level"] == "error")
            skipped.append("{}: {}".format(name, reasons))
            continue
        result = sensorial_db.add_files(project_id, [file_item(entry, path, parsed)])
        report["files"] += len(result["added"])
        report["duplicates"] += len(result["duplicates"])
        recorded.append(entry["id"])
        if role == "perfil" and parsed.table is not None:
            report["participants"] += sensorial_db.upsert_participants(
                project_id, _profile_rows(parsed.table), only_missing=True, source="planilha")
        if role in ("documento", "literatura"):
            content = path.read_bytes()
            doc_type = fix.get("doc_type") or entry["meta"].get("doc_type")
            if role == "documento" and doc_type == "briefing" and not briefing_set:
                text, error = briefing.extract_briefing_text(name, content)
                if text:
                    try:
                        sensorial_db.update_project(project_id, briefing_text=briefing.cap_text(text),
                                                    briefing_filename=name)
                        briefing_set = True
                    except auth.AuthorizationError:
                        warnings.append("{}: o Contexto só é editado por quem criou o projeto.".format(name))
                elif error:
                    warnings.append("{}: {}".format(name, error))
            if send_document is not None and result["added"]:
                try:
                    send_document(name, content, {"role": role, "doc_type": doc_type})
                    report["documents"] += 1
                except Exception as error:  # a base fora do ar não impede o resto
                    warnings.append("{}: não foi para a base ({}).".format(name, error))
    return report, recorded


def discard_batch(project_id: int, batch_id: int) -> None:
    _INBOX.discard_batch(project_id, batch_id)


def remove_project_inbox(organization_id: int, project_id: int) -> None:
    """Chamado ao excluir o projeto: o que estava pendente sai do disco."""
    _INBOX.remove_project_inbox(organization_id, project_id)
