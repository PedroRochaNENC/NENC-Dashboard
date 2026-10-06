"""
Sensorial DB — camada SQLite dos projetos do Teste Sensorial.

Segue as regras do `jornada_db`: toda escrita passa pela guarda do módulo, o
projeto tem autor, e as linhas filhas carregam a organização DO PROJETO (em
"Todas as organizações" a da sessão é 0 e violaria a chave estrangeira).

O banco guarda o que o usuário enviou e o que ele decidiu: o índice dos
arquivos (os dados em si ficam no disco, `utils/sensorial_store.py`),
participantes com perfil, decisões sobre sessões e análises de IA. Tudo o que
se calcula (índices, limpeza, comparações) sai do modelo e das métricas, e
`data_version` diz quando esse cálculo envelheceu.

As tabelas têm o prefixo `sens_`: as `ts_*` de uma versão antiga do módulo
podem existir no banco e ficam intocadas.
"""

import hashlib
import json
import os
import sqlite3
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import pandas as pd

from utils import auth, sensorial_store

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "prosodia.db"

MODULE_KEY = "teste_sensorial"

PROJECT_FIELDS = (
    "name",
    "categoria",
    "objetivo",
    "historico",
    "questions",
    "briefing_filename",
    "briefing_text",
    "settings_json",
    "quality_thresholds",
)
# Campos que mudam o resultado da análise: alterar qualquer um envelhece as
# análises de IA e o cache do modelo.
_DATA_FIELDS = {"settings_json", "quality_thresholds"}

SESSION_STATUSES = ("auto", "incluida", "excluida")
SESSION_LAYERS = ("todas", "eeg", "fc", "gsr", "associacao")

# Teto do que fica dentro do banco (imagens, texto de documentos). Tabelas do
# pipeline vão para o disco e não têm este teto.
MAX_BLOB_BYTES = 25 * 1024 * 1024

_CHILD_TABLES = (
    "sens_files",
    "sens_participants",
    "sens_sessions",
    "sens_analyses",
)


# ---------------------------------------------------------------------------
# Conexão e guardas
# ---------------------------------------------------------------------------

def _database_path() -> Path:
    return Path(os.environ.get("NENC_DB_PATH", str(_DEFAULT_DB_PATH))).expanduser()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    database_path = _database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(database_path), timeout=auth.SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _active_organization_id() -> int:
    """Organização validada pelo servidor; 0 é "Todas as organizações"."""
    return auth.active_organization_id()


def _audit(action: str, target_type: str, target_id: Optional[int], organization_id: int,
           write: bool = False) -> None:
    auth.audit_business_access(action, target_type, target_id, organization_id, write=write)


def _require_write():
    """Autoridade do servidor para toda função que altera estado; devolve a conta autorizada."""
    return auth.assert_module_write(MODULE_KEY)


def _actor_user_id(actor) -> Optional[int]:
    user_id = getattr(actor, "id", None)
    return int(user_id) if isinstance(user_id, int) else None


def _assert_project_authorship(project_id: int, actor, action: str) -> None:
    """Um administrador de organização não altera projeto de outro (mesma política da Jornada)."""
    if getattr(actor, "is_platform_admin", False) is True:
        return
    with _connect() as conn:
        row = conn.execute(
            "SELECT created_by_user_id, organization_id FROM sens_projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            return
        creator_id = row["created_by_user_id"]
        organization_id = row["organization_id"]
        if creator_id is None or creator_id == _actor_user_id(actor):
            return
        creator = conn.execute("SELECT is_platform_admin FROM users WHERE id = ?", (creator_id,)).fetchone()
        if creator is None or bool(creator["is_platform_admin"]):
            return
    reason = "Este projeto foi criado por outro administrador da organizacao."
    auth.audit_authorization_denied(actor if isinstance(actor, auth.User) else None, action, "sens_project",
                                    project_id, organization_id, reason)
    raise auth.AuthorizationError(reason)


def user_can_modify_project(project: Dict, user) -> bool:
    """Predicado da interface, com a mesma regra de `_assert_project_authorship`."""
    if not auth.can_write(user):
        return False
    if user.is_platform_admin:
        return True
    if project.get("created_by_user_id") == user.id:
        return True
    return bool(project.get("created_by_is_unrestricted"))


def _project_org(conn: sqlite3.Connection, project_id: int, organization_id: int) -> int:
    """Organização dona do projeto, exigindo que ele seja visível na organização ativa."""
    if organization_id:
        row = conn.execute("SELECT organization_id FROM sens_projects WHERE id = ? AND organization_id = ?",
                           (project_id, organization_id)).fetchone()
    else:
        row = conn.execute("SELECT organization_id FROM sens_projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise ValueError("Projeto nao encontrado para a organizacao ativa.")
    return int(row["organization_id"])


def _bump_version(conn: sqlite3.Connection, project_id: int) -> None:
    """Marca que os dados da análise mudaram, na mesma transação da escrita."""
    conn.execute(
        "UPDATE sens_projects SET data_version = data_version + 1, "
        "updated_at = datetime('now','localtime') WHERE id = ?",
        (project_id,),
    )


def _json_or(value: Optional[str], default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Cria as tabelas. Idempotente; as páginas chamam uma vez por execução."""
    auth.initialize_auth_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sens_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                categoria TEXT,
                objetivo TEXT,
                historico TEXT,
                questions TEXT,
                briefing_filename TEXT,
                briefing_text TEXT,
                settings_json TEXT,
                quality_thresholds TEXT,
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                data_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

            -- Índice dos arquivos: tabelas do pipeline ficam no disco
            -- (storage = 'disk'); imagens e texto de documentos, no blob.
            CREATE TABLE IF NOT EXISTS sens_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES sens_projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                rel_path TEXT,
                role TEXT NOT NULL,
                run_id TEXT,
                sha256 TEXT NOT NULL,
                source_sha256 TEXT,
                size_bytes INTEGER NOT NULL,
                storage TEXT NOT NULL DEFAULT 'blob' CHECK (storage IN ('blob','disk')),
                content BLOB,
                orig_path TEXT,
                table_version INTEGER,
                meta_json TEXT NOT NULL DEFAULT '{}',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, sha256)
            );

            -- Só o código do participante; o nome nunca é guardado aqui.
            CREATE TABLE IF NOT EXISTS sens_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES sens_projects(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                profile_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, code)
            );

            -- Decisões do usuário sobre sessões (por camada); o resto é automático.
            CREATE TABLE IF NOT EXISTS sens_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES sens_projects(id) ON DELETE CASCADE,
                sessao_id TEXT NOT NULL,
                layer TEXT NOT NULL DEFAULT 'todas'
                    CHECK (layer IN ('todas','eeg','fc','gsr','associacao')),
                status TEXT NOT NULL DEFAULT 'auto' CHECK (status IN ('auto','incluida','excluida')),
                reason TEXT,
                participant_code_override TEXT,
                condition_override TEXT,
                updated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, sessao_id, layer)
            );

            CREATE TABLE IF NOT EXISTS sens_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES sens_projects(id) ON DELETE CASCADE,
                model TEXT,
                mode TEXT,
                analysis_text TEXT,
                citations TEXT,
                search_json TEXT,
                filters_json TEXT,
                data_version INTEGER,
                kb_file_id TEXT,
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            -- Importações enviadas pelo script (utils/sensorial_imports.py): os
            -- arquivos esperam revisão no inbox, fora do banco.
            CREATE TABLE IF NOT EXISTS sens_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES sens_projects(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'recebendo',
                source_label TEXT,
                client_json TEXT,
                summary_json TEXT,
                ignored_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                closed_at TEXT,
                decided_at TEXT,
                decided_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS sens_import_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES sens_import_batches(id) ON DELETE CASCADE,
                rel_path TEXT NOT NULL,
                role TEXT NOT NULL,
                meta_json TEXT NOT NULL DEFAULT '{}',
                sha256 TEXT NOT NULL,
                source_sha256 TEXT,
                size_bytes INTEGER NOT NULL,
                received_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'recebendo',
                created_at TEXT,
                UNIQUE (batch_id, rel_path)
            );

            CREATE INDEX IF NOT EXISTS idx_sens_projects_org ON sens_projects(organization_id);
            CREATE INDEX IF NOT EXISTS idx_sens_files_active ON sens_files(project_id, is_active, role);
            CREATE INDEX IF NOT EXISTS idx_sens_import_batches_project ON sens_import_batches(project_id, status);
            """
        )
        for table_name in _CHILD_TABLES:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_{t}_project ON {t}(project_id)".format(t=table_name))


# ---------------------------------------------------------------------------
# Projetos
# ---------------------------------------------------------------------------

_PROJECT_SELECT = """
    SELECT p.*, o.name AS organization_name,
           CASE
               WHEN p.created_by_user_id IS NULL THEN 1
               WHEN creator.id IS NULL THEN 1
               ELSE creator.is_platform_admin
           END AS created_by_is_unrestricted,
           (SELECT COUNT(*) FROM sens_participants x WHERE x.project_id = p.id) AS n_participants,
           (SELECT COUNT(*) FROM sens_files x WHERE x.project_id = p.id AND x.is_active = 1) AS n_files,
           (SELECT COUNT(*) FROM sens_analyses x WHERE x.project_id = p.id) AS n_analyses
    FROM sens_projects p
    LEFT JOIN organizations o ON o.id = p.organization_id
    LEFT JOIN users creator ON creator.id = p.created_by_user_id
"""


def _check_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    unknown = set(fields) - set(PROJECT_FIELDS)
    if unknown:
        raise ValueError("Campos de projeto desconhecidos: {}".format(", ".join(sorted(unknown))))
    clean: Dict[str, Any] = {}
    for key, value in fields.items():
        if key == "settings_json" and isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        if key == "quality_thresholds" and isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if isinstance(value, str) and key not in ("settings_json", "quality_thresholds"):
            value = value.strip()
        clean[key] = value
    if "name" in clean and not clean["name"]:
        raise ValueError("O nome do projeto e obrigatorio.")
    return clean


def create_project(name: str, **fields) -> int:
    """Cria um projeto na organização ativa (em "Todas", na organização da conta)."""
    clean = _check_fields(dict(fields, name=name))
    actor = _require_write()
    organization_id = _active_organization_id() or actor.organization_id
    columns = ["organization_id", "created_by_user_id"] + list(clean)
    values = [organization_id, _actor_user_id(actor)] + list(clean.values())
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO sens_projects ({}) VALUES ({})".format(", ".join(columns), ", ".join("?" * len(columns))),
            values,
        )
        project_id = int(cursor.lastrowid)
    _audit("sensorial.project.create", "sens_project", project_id, organization_id, write=True)
    return project_id


def list_projects() -> List[Dict[str, Any]]:
    """Projetos da organização ativa (todos em "Todas as organizações")."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        if organization_id:
            rows = conn.execute(_PROJECT_SELECT + " WHERE p.organization_id = ? ORDER BY p.updated_at DESC, p.id DESC",
                                (organization_id,)).fetchall()
        else:
            rows = conn.execute(_PROJECT_SELECT + " ORDER BY p.updated_at DESC, p.id DESC").fetchall()
    _audit("sensorial.project.list", "sens_project", None, organization_id or 0)
    return [dict(row) for row in rows]


def get_project(project_id: int) -> Optional[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        if organization_id:
            row = conn.execute(_PROJECT_SELECT + " WHERE p.id = ? AND p.organization_id = ?",
                               (project_id, organization_id)).fetchone()
        else:
            row = conn.execute(_PROJECT_SELECT + " WHERE p.id = ?", (project_id,)).fetchone()
    _audit("sensorial.project.read", "sens_project", project_id, organization_id or 0)
    return dict(row) if row else None


def project_settings(project: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Configuração do projeto (desenho, índices, limpeza, claims), sempre um dict."""
    if not project:
        return {}
    settings = _json_or(project.get("settings_json"), {})
    return settings if isinstance(settings, dict) else {}


def update_project(project_id: int, **changes) -> bool:
    """Atualiza só os campos informados. Devolve False se nada mudou."""
    clean = _check_fields(changes)
    if not clean:
        return False
    actor = _require_write()
    _assert_project_authorship(project_id, actor, "sens_project.update")
    organization_id = _active_organization_id()
    assignments = ", ".join("{} = ?".format(column) for column in clean)
    assignments += ", updated_at = datetime('now','localtime')"
    if _DATA_FIELDS.intersection(clean):
        assignments += ", data_version = data_version + 1"
    with _connect() as conn:
        if organization_id:
            result = conn.execute("UPDATE sens_projects SET {} WHERE id = ? AND organization_id = ?".format(assignments),
                                  list(clean.values()) + [project_id, organization_id])
        else:
            result = conn.execute("UPDATE sens_projects SET {} WHERE id = ?".format(assignments),
                                  list(clean.values()) + [project_id])
    if result.rowcount:
        _audit("sensorial.project.update", "sens_project", project_id, organization_id or 0, write=True)
        return True
    return False


def delete_project(project_id: int) -> bool:
    """Remove o projeto e tudo o que pende dele: banco, base de conhecimento, disco e inbox."""
    actor = _require_write()
    _assert_project_authorship(project_id, actor, "sens_project.delete")
    organization_id = _active_organization_id()
    with _connect() as conn:
        try:
            project_org = _project_org(conn, project_id, organization_id)
        except ValueError:
            return False
        result = conn.execute("DELETE FROM sens_projects WHERE id = ?", (project_id,))
    if not result.rowcount:
        return False
    _audit("sensorial.project.delete", "sens_project", project_id, project_org, write=True)
    _remove_from_knowledge_base(project_id)
    try:
        sensorial_store.delete_project_dir(project_org, project_id)
    except Exception:
        pass
    try:
        from utils import sensorial_imports

        sensorial_imports.remove_project_inbox(project_org, project_id)
    except Exception:
        pass
    return True


def _remove_from_knowledge_base(project_id: int, file_ids: Iterable[str] = ()) -> None:
    """Best-effort: a verdade é o banco; a OpenAI fora do ar não impede a exclusão."""
    try:
        from utils import kb_cleanup

        if project_id:
            kb_cleanup.remove_documents_for_project(project_id, tuple(file_ids), module_key=MODULE_KEY)
        else:
            kb_cleanup.remove_files(tuple(file_ids), module_key=MODULE_KEY)
    except Exception:
        pass


def summary_counts() -> Dict[str, int]:
    """Números do card da Visão geral, sem chamar serviço externo."""
    try:
        init_db()
        organization_id = _active_organization_id()
        with _connect() as conn:
            where = "WHERE p.organization_id = ?" if organization_id else ""
            params: Tuple = (organization_id,) if organization_id else ()
            row = conn.execute(
                """
                SELECT COUNT(*) AS projects,
                       COALESCE(SUM((SELECT COUNT(*) FROM sens_participants x WHERE x.project_id = p.id)), 0)
                           AS participants,
                       COALESCE(SUM((SELECT COUNT(*) FROM sens_files x
                                     WHERE x.project_id = p.id AND x.is_active = 1)), 0) AS files
                FROM sens_projects p {}
                """.format(where),
                params,
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("projects", "participants", "files")}
    except Exception:
        return {"projects": 0, "participants": 0, "files": 0}


# ---------------------------------------------------------------------------
# Arquivos
# ---------------------------------------------------------------------------

def add_files(project_id: int, files: Iterable[Dict[str, Any]]) -> Dict[str, list]:
    """Grava os arquivos sem repetir o que já está no projeto.

    Cada item traz `filename`, `role` e o conteúdo em `content` (bytes) ou
    `path` (arquivo no disco, movido se `move=True`). Opcionais: `rel_path`
    (caminho na pasta do estudo, só como metadado), `run_id`, `meta`,
    `sha256`/`source_sha256` (já calculados no envio), `extension`, `table`
    (DataFrame normalizado, gravado em Parquet com `table_version`) e
    `supersede` (desativa os outros arquivos ativos do mesmo papel, como uma
    rodada nova do pipeline). Duplicata é o mesmo sha256 ou o mesmo original.
    Tabela, `storage="disk"` ou mais de `MAX_BLOB_BYTES` vão para o disco.
    """
    items = list(files)
    for item in items:
        size = len(item["content"]) if item.get("content") is not None else Path(item["path"]).stat().st_size
        if not size:
            raise ValueError("Arquivo vazio: {}".format(item.get("filename")))
        item["_size"] = size
        to_disk = item.get("table") is not None or item.get("storage") == "disk" or size > MAX_BLOB_BYTES
        item["_storage"] = "disk" if to_disk else "blob"
    actor = _require_write()
    organization_id = _active_organization_id()
    added: list = []
    duplicates: list = []
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
    for item in items:
        if item.get("source_sha256"):
            item["source_sha256"] = str(item["source_sha256"]).lower()
        if item.get("sha256"):
            item["sha256"] = str(item["sha256"]).lower()
        elif item.get("content") is not None:
            item["sha256"] = hashlib.sha256(item["content"]).hexdigest()
        else:
            digest = hashlib.sha256()
            with Path(item["path"]).open("rb") as handle:
                for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                    digest.update(block)
            item["sha256"] = digest.hexdigest()
    with _connect() as conn:
        for item in items:
            keys = [item["sha256"]] + ([item["source_sha256"]] if item.get("source_sha256") else [])
            placeholders = ",".join("?" * len(keys))
            existing = conn.execute(
                "SELECT id FROM sens_files WHERE project_id = ? AND (sha256 IN ({0}) OR source_sha256 IN ({0}))"
                .format(placeholders),
                [project_id] + keys + keys,
            ).fetchone()
            if existing:
                duplicates.append(item["filename"])
                continue
            storage = item["_storage"]
            orig_path = None
            content_blob = None
            table_version = None
            if storage == "disk":
                extension = item.get("extension") or Path(str(item["filename"])).suffix
                source = item["content"] if item.get("content") is not None else Path(item["path"])
                orig_path = sensorial_store.save_original(project_org, project_id, item["sha256"], extension, source,
                                                          move=bool(item.get("move")))
                if item.get("table") is not None:
                    table_version = int(item.get("table_version") or 1)
                    sensorial_store.write_table(
                        item["table"], sensorial_store.table_path(project_org, project_id, item["sha256"],
                                                                  table_version))
            else:
                content = item["content"] if item.get("content") is not None else Path(item["path"]).read_bytes()
                content_blob = zlib.compress(content)
            if item.get("supersede"):
                conn.execute(
                    "UPDATE sens_files SET is_active = 0 WHERE project_id = ? AND role = ? AND is_active = 1",
                    (project_id, item["role"]),
                )
            cursor = conn.execute(
                """
                INSERT INTO sens_files (
                    organization_id, project_id, filename, rel_path, role, run_id, sha256, source_sha256,
                    size_bytes, storage, content, orig_path, table_version, meta_json, created_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_org, project_id, item["filename"], item.get("rel_path"), item["role"],
                 item.get("run_id") or None, item["sha256"], item.get("source_sha256") or None, item["_size"],
                 storage, content_blob, orig_path, table_version,
                 json.dumps(item.get("meta") or {}, ensure_ascii=False, default=str), _actor_user_id(actor)),
            )
            added.append((int(cursor.lastrowid), item["filename"]))
        if added:
            _bump_version(conn, project_id)
    if added:
        _audit("sensorial.file.create", "sens_project", project_id, project_org, write=True)
    return {"added": added, "duplicates": duplicates}


_FILE_COLUMNS = ("id, filename, rel_path, role, run_id, sha256, source_sha256, size_bytes, storage, orig_path, "
                 "table_version, meta_json, is_active, created_by_user_id, created_at")


def _decode_file(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["meta"] = _json_or(item.pop("meta_json"), {})
    item["is_active"] = bool(item["is_active"])
    return item


def list_files(project_id: int) -> List[Dict[str, Any]]:
    """Metadados dos arquivos; o conteúdo nunca sai por aqui."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute("SELECT {} FROM sens_files WHERE project_id = ? ORDER BY id".format(_FILE_COLUMNS),
                            (project_id,)).fetchall()
    return [_decode_file(row) for row in rows]


def get_file_content(project_id: int, file_id: int) -> Optional[bytes]:
    """Bytes de um arquivo (imagem, documento, original de tabela)."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        row = conn.execute("SELECT storage, content, orig_path FROM sens_files WHERE id = ? AND project_id = ?",
                           (file_id, project_id)).fetchone()
    if row is None:
        return None
    if row["storage"] == "blob":
        return zlib.decompress(row["content"]) if row["content"] is not None else None
    path = sensorial_store.resolve(row["orig_path"]) if row["orig_path"] else None
    return path.read_bytes() if path is not None and path.is_file() else None


def set_file_active(project_id: int, file_id: int, active: bool) -> bool:
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        result = conn.execute("UPDATE sens_files SET is_active = ? WHERE id = ? AND project_id = ?",
                              (1 if active else 0, file_id, project_id))
        if result.rowcount:
            _bump_version(conn, project_id)
    if result.rowcount:
        _audit("sensorial.file.update", "sens_file", file_id, project_org, write=True)
    return bool(result.rowcount)


def delete_file(project_id: int, file_id: int) -> bool:
    """Apaga o arquivo do banco e do disco (original e tabela normalizada)."""
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        row = conn.execute("SELECT sha256, orig_path FROM sens_files WHERE id = ? AND project_id = ?",
                           (file_id, project_id)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM sens_files WHERE id = ?", (file_id,))
        _bump_version(conn, project_id)
    _audit("sensorial.file.delete", "sens_file", file_id, project_org, write=True)
    try:
        sensorial_store.delete_files([row["orig_path"]])
        sensorial_store.delete_tables(project_org, project_id, row["sha256"])
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Participantes
# ---------------------------------------------------------------------------

def upsert_participants(project_id: int, rows: Iterable[Dict[str, Any]], *, only_missing: bool = False,
                        source: str = "manual") -> int:
    """Cria ou atualiza participantes pelo código, com o perfil num dict (sexo, grupo...).

    `only_missing=True` é o caminho da semente vinda de uma planilha: preenche
    o que está vazio e nunca sobrescreve o que alguém editou na tela.
    """
    items = [row for row in rows if str(row.get("code") or "").strip()]
    if not items:
        return 0
    _require_write()
    organization_id = _active_organization_id()
    changed = 0
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        for item in items:
            code = str(item["code"]).strip()
            profile = {str(k): (str(v).strip() if v is not None else "") for k, v in (item.get("profile") or {}).items()}
            existing = conn.execute("SELECT * FROM sens_participants WHERE project_id = ? AND code = ?",
                                    (project_id, code)).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO sens_participants (organization_id, project_id, code, profile_json, notes, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (project_org, project_id, code, json.dumps(profile, ensure_ascii=False),
                     (str(item.get("notes") or "").strip() or None), source),
                )
                changed += 1
                continue
            current = _json_or(existing["profile_json"], {})
            merged = dict(current)
            for key, value in profile.items():
                if only_missing and str(current.get(key) or "").strip():
                    continue
                merged[key] = value
            notes = existing["notes"]
            if "notes" in item and not (only_missing and notes):
                notes = str(item.get("notes") or "").strip() or None
            if merged != current or notes != existing["notes"]:
                conn.execute(
                    "UPDATE sens_participants SET profile_json = ?, notes = ?, "
                    "updated_at = datetime('now','localtime') WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), notes, existing["id"]),
                )
                changed += 1
        if changed:
            _bump_version(conn, project_id)
    if changed:
        _audit("sensorial.participant.update", "sens_project", project_id, project_org, write=True)
    return changed


def list_participants(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute("SELECT * FROM sens_participants WHERE project_id = ? ORDER BY code",
                            (project_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["profile"] = _json_or(item.pop("profile_json"), {})
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Sessões: só as decisões do usuário
# ---------------------------------------------------------------------------

def set_session_status(project_id: int, sessao_id: str, *, layer: str = "todas", status: str, reason: str = "",
                       participant_code_override: Optional[str] = None,
                       condition_override: Optional[str] = None) -> None:
    """Inclui, exclui ou devolve ao automático uma sessão (numa camada ou em todas)."""
    if status not in SESSION_STATUSES:
        raise ValueError("Status de sessao desconhecido: {!r}".format(status))
    if layer not in SESSION_LAYERS:
        raise ValueError("Camada desconhecida: {!r}".format(layer))
    if status == "excluida" and not str(reason or "").strip():
        raise ValueError("Excluir uma sessao da analise exige um motivo.")
    if not str(sessao_id or "").strip():
        raise ValueError("Sessao sem identificador.")
    actor = _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        conn.execute(
            """
            INSERT INTO sens_sessions (organization_id, project_id, sessao_id, layer, status, reason,
                                       participant_code_override, condition_override, updated_by_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (project_id, sessao_id, layer) DO UPDATE SET
                status = excluded.status,
                reason = excluded.reason,
                participant_code_override = excluded.participant_code_override,
                condition_override = excluded.condition_override,
                updated_by_user_id = excluded.updated_by_user_id,
                updated_at = datetime('now','localtime')
            """,
            (project_org, project_id, str(sessao_id).strip(), layer, status, str(reason or "").strip() or None,
             (participant_code_override or "").strip() or None, (condition_override or "").strip() or None,
             _actor_user_id(actor)),
        )
        _bump_version(conn, project_id)
    _audit("sensorial.session.update", "sens_project", project_id, project_org, write=True)


def list_session_overrides(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute("SELECT * FROM sens_sessions WHERE project_id = ? ORDER BY sessao_id, layer",
                            (project_id,)).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Análises de IA
# ---------------------------------------------------------------------------

def _decode_analysis(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    citations = _json_or(item.get("citations"), [])
    item["citations"] = citations if isinstance(citations, list) else []
    item["search"] = _json_or(item.pop("search_json", None), {})
    item["filters"] = _json_or(item.pop("filters_json", None), {})
    return item


def save_analysis(project_id: int, *, model: str, mode: str, analysis_text: str, citations: Optional[list] = None,
                  search: Optional[dict] = None, filters: Optional[dict] = None,
                  data_version: Optional[int] = None) -> int:
    actor = _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        cursor = conn.execute(
            """
            INSERT INTO sens_analyses (organization_id, project_id, model, mode, analysis_text, citations,
                                       search_json, filters_json, data_version, created_by_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_org, project_id, model, mode, str(analysis_text or "").strip(),
             json.dumps(citations or [], ensure_ascii=False), json.dumps(search or {}, ensure_ascii=False),
             json.dumps(filters or {}, ensure_ascii=False), data_version, _actor_user_id(actor)),
        )
        analysis_id = int(cursor.lastrowid)
    _audit("sensorial.analysis.create", "sens_analysis", analysis_id, project_org, write=True)
    return analysis_id


def list_analyses(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute("SELECT * FROM sens_analyses WHERE project_id = ? ORDER BY created_at DESC, id DESC",
                            (project_id,)).fetchall()
    return [_decode_analysis(row) for row in rows]


def set_analysis_kb_file(project_id: int, analysis_id: int, kb_file_id: str) -> None:
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        conn.execute("UPDATE sens_analyses SET kb_file_id = ? WHERE id = ? AND project_id = ?",
                     (kb_file_id, analysis_id, project_id))
    _audit("sensorial.analysis.update", "sens_analysis", analysis_id, project_org, write=True)


def delete_analyses(project_id: int, analysis_ids: Iterable[int]) -> int:
    """Apaga análises e a cópia que cada uma deixou na base de conhecimento."""
    ids = [int(value) for value in analysis_ids]
    if not ids:
        return 0
    _require_write()
    organization_id = _active_organization_id()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        kb_files = [row["kb_file_id"] for row in conn.execute(
            "SELECT kb_file_id FROM sens_analyses WHERE project_id = ? AND id IN ({})".format(placeholders),
            [project_id] + ids) if row["kb_file_id"]]
        deleted = conn.execute("DELETE FROM sens_analyses WHERE project_id = ? AND id IN ({})".format(placeholders),
                               [project_id] + ids).rowcount
    if deleted:
        _audit("sensorial.analysis.delete", "sens_project", project_id, project_org, write=True)
    if kb_files:
        _remove_from_knowledge_base(0, kb_files)
    return deleted


def audit_export(project_id: int, kind: str) -> None:
    """Registra um download do projeto, de qualquer papel (`sensorial.export.<tipo>`)."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
    _audit("sensorial.export.{}".format(kind), "sens_project", project_id, project_org, write=True)


# ---------------------------------------------------------------------------
# Leitura para o modelo (sem sessão)
# ---------------------------------------------------------------------------

def load_project_bundle(project_id: int, organization_id: int) -> Dict[str, Any]:
    """Tudo o que o modelo precisa, lido pela organização DO PROJETO.

    Sem dependência de sessão, para caber num `st.cache_data`: quem chama já
    autorizou o acesso ao obter o projeto por `get_project`, e passa a
    organização dele. As tabelas não vêm aqui: cada arquivo traz o caminho da
    tabela normalizada, que o modelo lê do disco.
    """
    with _connect() as conn:
        project = conn.execute("SELECT * FROM sens_projects WHERE id = ? AND organization_id = ?",
                               (project_id, organization_id)).fetchone()
        if project is None:
            raise ValueError("Projeto nao encontrado para a organizacao informada.")
        files = [_decode_file(row) for row in conn.execute(
            "SELECT {} FROM sens_files WHERE project_id = ? AND is_active = 1 ORDER BY id".format(_FILE_COLUMNS),
            (project_id,))]
        participants = []
        for row in conn.execute("SELECT code, profile_json, notes, source FROM sens_participants "
                                "WHERE project_id = ? ORDER BY code", (project_id,)):
            item = dict(row)
            item["profile"] = _json_or(item.pop("profile_json"), {})
            participants.append(item)
        sessions = [dict(row) for row in conn.execute(
            "SELECT sessao_id, layer, status, reason, participant_code_override, condition_override "
            "FROM sens_sessions WHERE project_id = ?", (project_id,))]
    for item in files:
        item["table_path"] = (str(sensorial_store.table_path(organization_id, project_id, item["sha256"],
                                                             item["table_version"]))
                              if item.get("table_version") else None)
        item["orig_abspath"] = (str(sensorial_store.resolve(item["orig_path"])) if item.get("orig_path") else None)
    project = dict(project)
    return {
        "project": project,
        "settings": project_settings(project),
        "files": files,
        "participants": participants,
        "sessions": sessions,
    }


def read_file_table(file_item: Dict[str, Any]) -> pd.DataFrame:
    """A tabela normalizada de um arquivo do bundle (vazia se não houver)."""
    path = file_item.get("table_path")
    if not path or not Path(path).is_file():
        return pd.DataFrame()
    return sensorial_store.read_table(Path(path))
