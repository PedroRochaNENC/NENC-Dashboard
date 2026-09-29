"""
Jornada DB — camada SQLite dos projetos da Jornada de Compra.

Segue as mesmas regras do `prosodia_db`: toda escrita passa pela guarda do
modulo, o projeto tem autor, e as linhas filhas carregam a organizacao DO
PROJETO (nao a selecionada na sessao — em "Todas as organizacoes" ela e 0 e
violaria a chave estrangeira).

O banco guarda o que o usuario enviou e o que ele decidiu: arquivos brutos,
participantes, exclusoes de gravacao, ajustes do catalogo de AOIs. Tudo o que
se calcula a partir disso (unidades, segundos, metricas) sai de
`utils/jornada_model.py`, e `data_version` diz quando esse calculo envelheceu.

Banco: nenc-dashboard/prosodia.db (ou NENC_DB_PATH)
"""

import hashlib
import io
import json
import os
import sqlite3
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import pandas as pd

from utils import auth

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "prosodia.db"

MODULE_KEY = "jornada_compra"

# Colunas que `update_project` aceita. Autor, organizacao e versao tem dono
# proprio e nunca chegam por aqui.
PROJECT_FIELDS = (
    "name",
    "categoria",
    "especialidade",
    "historico",
    "problemas",
    "questions",
    "marcas",
    "marca_foco",
    "briefing_filename",
    "briefing_text",
    "settings_json",
    "quality_thresholds",
)
# Campos que mudam o resultado da analise: alterar qualquer um deles envelhece
# as analises de IA ja geradas e o cache do modelo.
_DATA_FIELDS = {"marcas", "marca_foco", "settings_json", "quality_thresholds"}

RECORDING_STATUSES = ("auto", "incluida", "excluida")
UNIT_CHOICES = ("segundos", "amostras")
AOI_KINDS = ("produto", "preco", "embalagem", "fora", "outro")

# Teto por arquivo de dados. Video tem caminho proprio (utils/jornada_media.py)
# e nunca entra no SQLite.
MAX_FILE_BYTES = 25 * 1024 * 1024

_CHILD_TABLES = (
    "jc_datasets",
    "jc_interviews",
    "jc_analyses",
    "jc_files",
    "jc_participants",
    "jc_recordings",
    "jc_aoi_catalog",
    "jc_media",
)


# ---------------------------------------------------------------------------
# Conexao e guardas
# ---------------------------------------------------------------------------

def _database_path() -> Path:
    return Path(os.environ.get("NENC_DB_PATH", str(_DEFAULT_DB_PATH))).expanduser()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    database_path = _database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(database_path))
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
    """Organizacao validada pelo servidor; 0 e "Todas as organizacoes"."""

    return auth.active_organization_id()


def _audit(
    action: str,
    target_type: str,
    target_id: Optional[int],
    organization_id: int,
    write: bool = False,
) -> None:
    auth.audit_business_access(
        action, target_type, target_id, organization_id, write=write
    )


def _require_write():
    """Autoridade do servidor para toda funcao deste modulo que altera estado.

    Esconder o botao organiza a tela; quem nega e esta guarda. Retorna a conta
    autorizada para que o chamador registre a autoria.
    """

    return auth.assert_module_write(MODULE_KEY)


def _actor_user_id(actor) -> Optional[int]:
    user_id = getattr(actor, "id", None)
    return int(user_id) if isinstance(user_id, int) else None


def _assert_project_authorship(project_id: int, actor, action: str) -> None:
    """Um administrador de organizacao nao altera projeto de outro.

    Mesma politica do NencBoost: liberam o projeto a falta de autor (legado),
    o autor removido e o autor ser administrador global. A consulta roda em
    conexao propria, fechada antes da decisao, para a recusa ser registrada
    sem transacao pendente.
    """

    if getattr(actor, "is_platform_admin", False) is True:
        return
    with _connect() as conn:
        row = conn.execute(
            "SELECT created_by_user_id, organization_id FROM jc_projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            return
        creator_id = row["created_by_user_id"]
        organization_id = row["organization_id"]
        if creator_id is None or creator_id == _actor_user_id(actor):
            return
        creator = conn.execute(
            "SELECT is_platform_admin FROM users WHERE id = ?", (creator_id,)
        ).fetchone()
        if creator is None or bool(creator["is_platform_admin"]):
            return

    reason = "Este projeto foi criado por outro administrador da organizacao."
    auth.audit_authorization_denied(
        actor if isinstance(actor, auth.User) else None,
        action,
        "jc_project",
        project_id,
        organization_id,
        reason,
    )
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
    """Organizacao dona do projeto, exigindo que ele seja visivel.

    Com uma organizacao selecionada, o projeto precisa ser dela. Em "Todas"
    (0) qualquer projeto serve, e a organizacao devolvida e a dele — e ela que
    as linhas filhas gravam.
    """

    if organization_id:
        row = conn.execute(
            "SELECT organization_id FROM jc_projects WHERE id = ? AND organization_id = ?",
            (project_id, organization_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT organization_id FROM jc_projects WHERE id = ?", (project_id,)
        ).fetchone()
    if row is None:
        raise ValueError("Projeto nao encontrado para a organizacao ativa.")
    return int(row["organization_id"])


def _bump_version(conn: sqlite3.Connection, project_id: int) -> None:
    """Marca que os dados da analise mudaram, na mesma transacao da escrita."""

    conn.execute(
        """
        UPDATE jc_projects
        SET data_version = data_version + 1,
            updated_at = datetime('now','localtime')
        WHERE id = ?
        """,
        (project_id,),
    )


def _ensure_column(
    conn: sqlite3.Connection, table_name: str, column_name: str, definition: str
) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info({})".format(table_name))}
    if column_name not in columns:
        conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table_name, column_name, definition))


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
    """Cria e migra as tabelas. Idempotente; as paginas chamam uma vez por execucao."""

    auth.initialize_auth_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jc_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                categoria TEXT,
                historico TEXT,
                problemas TEXT,
                questions TEXT,
                marcas TEXT,
                briefing_text TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS jc_datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                tabelas_blob BLOB,
                por_marca_blob BLOB,
                medias_blob BLOB,
                visual_share_blob BLOB,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS jc_interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                titulo TEXT NOT NULL,
                participante_id TEXT,
                texto TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS jc_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                model TEXT,
                analysis_text TEXT,
                citations TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS jc_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                kind TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content BLOB NOT NULL,
                meta_json TEXT NOT NULL DEFAULT '{}',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, sha256)
            );

            CREATE TABLE IF NOT EXISTS jc_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                profile TEXT,
                tempo_informado TEXT,
                notes TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, code)
            );

            CREATE TABLE IF NOT EXISTS jc_recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                participant_code TEXT NOT NULL,
                task TEXT NOT NULL,
                store TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'auto'
                    CHECK (status IN ('auto','incluida','excluida')),
                reason TEXT,
                unit_override TEXT
                    CHECK (unit_override IS NULL OR unit_override IN ('segundos','amostras')),
                updated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, participant_code, task, store)
            );

            CREATE TABLE IF NOT EXISTS jc_aoi_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                store TEXT NOT NULL DEFAULT '',
                aoi TEXT NOT NULL,
                kind TEXT CHECK (kind IS NULL OR kind IN ('produto','preco','embalagem','fora','outro')),
                brand TEXT,
                line TEXT,
                product TEXT,
                part TEXT,
                element TEXT,
                attrs_json TEXT,
                shelf_weight REAL,
                include INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, store, aoi)
            );

            CREATE TABLE IF NOT EXISTS jc_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES jc_projects(id) ON DELETE CASCADE,
                participant_code TEXT NOT NULL,
                task TEXT NOT NULL,
                store TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                rel_path TEXT NOT NULL,
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE (project_id, sha256)
            );
            """
        )

        # Bancos criados pela versao de 0dc853f: as colunas novas chegam por
        # ALTER, e as antigas ficam como estao.
        for column, definition in (
            ("especialidade", "TEXT"),
            ("briefing_filename", "TEXT"),
            ("marca_foco", "TEXT"),
            ("settings_json", "TEXT"),
            ("quality_thresholds", "TEXT"),
            (
                "created_by_user_id",
                "INTEGER REFERENCES users(id) ON DELETE SET NULL",
            ),
            ("data_version", "INTEGER NOT NULL DEFAULT 0"),
        ):
            _ensure_column(conn, "jc_projects", column, definition)
        for column, definition in (
            ("mode", "TEXT"),
            ("search_json", "TEXT"),
            ("filters_json", "TEXT"),
            ("data_version", "INTEGER"),
            ("kb_file_id", "TEXT"),
            (
                "created_by_user_id",
                "INTEGER REFERENCES users(id) ON DELETE SET NULL",
            ),
        ):
            _ensure_column(conn, "jc_analyses", column, definition)

        # A versao anterior gravava filhos com a organizacao da sessao, que em
        # "Todas" podia nao ser a do projeto. O reparo e idempotente.
        for table_name in _CHILD_TABLES:
            conn.execute(
                """
                UPDATE {table}
                SET organization_id = (
                    SELECT p.organization_id FROM jc_projects p
                    WHERE p.id = {table}.project_id
                )
                WHERE EXISTS (
                    SELECT 1 FROM jc_projects p
                    WHERE p.id = {table}.project_id
                      AND p.organization_id IS NOT {table}.organization_id
                )
                """.format(table=table_name)
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_{t}_project ON {t}(project_id)".format(
                    t=table_name
                )
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jc_projects_org ON jc_projects(organization_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jc_files_active ON jc_files(project_id, is_active)"
        )


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
           (SELECT COUNT(*) FROM jc_participants x WHERE x.project_id = p.id)
               AS n_participants,
           (SELECT COUNT(*) FROM jc_files x WHERE x.project_id = p.id AND x.is_active = 1)
               AS n_files,
           (SELECT COUNT(*) FROM jc_media x WHERE x.project_id = p.id) AS n_media,
           (SELECT COUNT(*) FROM jc_analyses x WHERE x.project_id = p.id) AS n_analyses,
           (SELECT COUNT(*) FROM jc_interviews x WHERE x.project_id = p.id)
               AS n_interviews
    FROM jc_projects p
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
            # Sem ordenar chaves: a ordem dos atributos e dos grupos e a que a
            # equipe escreveu, e aparece assim nas telas.
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
    """Cria um projeto na organizacao ativa. Retorna o id."""

    clean = _check_fields(dict(fields, name=name))
    actor = _require_write()
    organization_id = _active_organization_id()
    if not organization_id:
        # Em "Todas as organizacoes" o projeto nasce na organizacao da conta.
        organization_id = actor.organization_id
    columns = ["organization_id", "created_by_user_id"] + list(clean)
    values = [organization_id, _actor_user_id(actor)] + list(clean.values())
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO jc_projects ({}) VALUES ({})".format(
                ", ".join(columns), ", ".join("?" * len(columns))
            ),
            values,
        )
        project_id = int(cursor.lastrowid)
    _audit("jornada.project.create", "jc_project", project_id, organization_id, write=True)
    return project_id


def list_projects() -> List[Dict[str, Any]]:
    """Projetos da organizacao ativa (todos em "Todas as organizacoes")."""

    organization_id = _active_organization_id()
    with _connect() as conn:
        if organization_id:
            rows = conn.execute(
                _PROJECT_SELECT
                + " WHERE p.organization_id = ? ORDER BY p.updated_at DESC, p.id DESC",
                (organization_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                _PROJECT_SELECT + " ORDER BY p.updated_at DESC, p.id DESC"
            ).fetchall()
    _audit("jornada.project.list", "jc_project", None, organization_id or 0)
    return [dict(row) for row in rows]


# Nome da versao de 0dc853f; `projetos.py` antigo chamava este.
get_projects = list_projects


def get_project(project_id: int) -> Optional[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        if organization_id:
            row = conn.execute(
                _PROJECT_SELECT + " WHERE p.id = ? AND p.organization_id = ?",
                (project_id, organization_id),
            ).fetchone()
        else:
            row = conn.execute(_PROJECT_SELECT + " WHERE p.id = ?", (project_id,)).fetchone()
    _audit("jornada.project.read", "jc_project", project_id, organization_id or 0)
    return dict(row) if row else None


def project_settings(project: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Configuracao do projeto (lojas, perfis, parametros), sempre um dict."""

    if not project:
        return {}
    settings = _json_or(project.get("settings_json"), {})
    return settings if isinstance(settings, dict) else {}


def update_project(project_id: int, **changes) -> bool:
    """Atualiza so os campos informados. Devolve False se nada mudou."""

    clean = _check_fields(changes)
    if not clean:
        return False
    actor = _require_write()
    _assert_project_authorship(project_id, actor, "jc_project.update")
    organization_id = _active_organization_id()
    assignments = ", ".join("{} = ?".format(column) for column in clean)
    assignments += ", updated_at = datetime('now','localtime')"
    if _DATA_FIELDS.intersection(clean):
        assignments += ", data_version = data_version + 1"
    with _connect() as conn:
        if organization_id:
            result = conn.execute(
                "UPDATE jc_projects SET {} WHERE id = ? AND organization_id = ?".format(assignments),
                list(clean.values()) + [project_id, organization_id],
            )
        else:
            result = conn.execute(
                "UPDATE jc_projects SET {} WHERE id = ?".format(assignments),
                list(clean.values()) + [project_id],
            )
    if result.rowcount:
        _audit("jornada.project.update", "jc_project", project_id, organization_id or 0, write=True)
        return True
    return False


def delete_project(project_id: int) -> bool:
    """Remove o projeto e tudo que pende dele, no banco, na base e no disco."""

    actor = _require_write()
    _assert_project_authorship(project_id, actor, "jc_project.delete")
    organization_id = _active_organization_id()
    with _connect() as conn:
        try:
            project_org = _project_org(conn, project_id, organization_id)
        except ValueError:
            return False
        media_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT rel_path FROM jc_media WHERE project_id = ?", (project_id,)
            )
        ]
        result = conn.execute("DELETE FROM jc_projects WHERE id = ?", (project_id,))
    if not result.rowcount:
        return False
    _audit("jornada.project.delete", "jc_project", project_id, project_org, write=True)
    _remove_from_knowledge_base(project_id)
    _remove_media_files(project_org, project_id, [row["rel_path"] for row in media_rows])
    return True


def _remove_from_knowledge_base(project_id: int, file_ids: Iterable[str] = ()) -> None:
    """Best-effort: a verdade e o banco; a OpenAI fora do ar nao impede a exclusao."""

    try:
        from utils import kb_cleanup

        if project_id:
            kb_cleanup.remove_documents_for_project(
                project_id, tuple(file_ids), module_key=MODULE_KEY
            )
        else:
            kb_cleanup.remove_files(tuple(file_ids), module_key=MODULE_KEY)
    except Exception:
        pass


def _remove_media_files(
    organization_id: int, project_id: int, rel_paths: Iterable[str]
) -> None:
    try:
        from utils import jornada_media

        jornada_media.delete_files(rel_paths)
        jornada_media.delete_project_dir(organization_id, project_id)
    except Exception:
        pass


def summary_counts() -> Dict[str, int]:
    """Numeros do card da Visao geral, sem chamar servico externo."""

    try:
        init_db()
        organization_id = _active_organization_id()
        with _connect() as conn:
            where = "WHERE p.organization_id = ?" if organization_id else ""
            params: Tuple = (organization_id,) if organization_id else ()
            row = conn.execute(
                """
                SELECT COUNT(*) AS projects,
                       COALESCE(SUM((SELECT COUNT(*) FROM jc_participants x
                                     WHERE x.project_id = p.id)), 0) AS participants,
                       COALESCE(SUM((SELECT COUNT(*) FROM jc_files x
                                     WHERE x.project_id = p.id AND x.is_active = 1)), 0)
                           AS files
                FROM jc_projects p {}
                """.format(where),
                params,
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("projects", "participants", "files")}
    except Exception:
        return {"projects": 0, "participants": 0, "files": 0}


# ---------------------------------------------------------------------------
# Arquivos de dados
# ---------------------------------------------------------------------------

def add_files(project_id: int, files: Iterable[Dict[str, Any]]) -> Dict[str, list]:
    """Grava os arquivos numa transacao so, sem repetir o que ja esta no projeto.

    Cada item traz `filename`, `kind`, `content` (bytes brutos) e `meta`
    (dict com o que a deteccao e a previa decidiram). O conteudo vai
    comprimido; o sha256 e do bruto, e e ele que impede duplicata.
    """

    items = list(files)
    for item in items:
        size = len(item.get("content") or b"")
        if not size:
            raise ValueError("Arquivo vazio: {}".format(item.get("filename")))
        if size > MAX_FILE_BYTES:
            raise ValueError(
                "Arquivo acima do limite de {} MB: {}".format(
                    MAX_FILE_BYTES // (1024 * 1024), item.get("filename")
                )
            )
    actor = _require_write()
    organization_id = _active_organization_id()
    added: list = []
    duplicates: list = []
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        for item in items:
            content = item["content"]
            digest = hashlib.sha256(content).hexdigest()
            existing = conn.execute(
                "SELECT id FROM jc_files WHERE project_id = ? AND sha256 = ?",
                (project_id, digest),
            ).fetchone()
            if existing:
                duplicates.append(item["filename"])
                continue
            cursor = conn.execute(
                """
                INSERT INTO jc_files (
                    organization_id, project_id, filename, kind, sha256, size_bytes,
                    content, meta_json, created_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_org,
                    project_id,
                    item["filename"],
                    item["kind"],
                    digest,
                    len(content),
                    zlib.compress(content),
                    json.dumps(item.get("meta") or {}, ensure_ascii=False, default=str),
                    _actor_user_id(actor),
                ),
            )
            added.append((int(cursor.lastrowid), item["filename"]))
        if added:
            _bump_version(conn, project_id)
    if added:
        _audit("jornada.file.create", "jc_project", project_id, project_org, write=True)
    return {"added": added, "duplicates": duplicates}


def list_files(project_id: int) -> List[Dict[str, Any]]:
    """Metadados dos arquivos; o blob nunca sai por aqui."""

    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute(
            """
            SELECT id, filename, kind, sha256, size_bytes, meta_json, is_active,
                   created_by_user_id, created_at
            FROM jc_files WHERE project_id = ? ORDER BY id
            """,
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["meta"] = _json_or(item.pop("meta_json"), {})
        item["is_active"] = bool(item["is_active"])
        result.append(item)
    return result


def get_file_content(project_id: int, file_id: int) -> Optional[bytes]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        row = conn.execute(
            "SELECT content FROM jc_files WHERE id = ? AND project_id = ?",
            (file_id, project_id),
        ).fetchone()
    return zlib.decompress(row["content"]) if row else None


def set_file_active(project_id: int, file_id: int, active: bool) -> bool:
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        result = conn.execute(
            "UPDATE jc_files SET is_active = ? WHERE id = ? AND project_id = ?",
            (1 if active else 0, file_id, project_id),
        )
        if result.rowcount:
            _bump_version(conn, project_id)
    if result.rowcount:
        _audit("jornada.file.update", "jc_file", file_id, project_org, write=True)
    return bool(result.rowcount)


def update_file_meta(project_id: int, file_id: int, changes: Dict[str, Any]) -> bool:
    """Corrige o que a deteccao atribuiu ao arquivo (loja, tarefa, grupo...)."""

    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        row = conn.execute(
            "SELECT meta_json FROM jc_files WHERE id = ? AND project_id = ?",
            (file_id, project_id),
        ).fetchone()
        if row is None:
            return False
        meta = _json_or(row["meta_json"], {})
        meta.update(changes)
        conn.execute(
            "UPDATE jc_files SET meta_json = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False, default=str), file_id),
        )
        _bump_version(conn, project_id)
    _audit("jornada.file.update", "jc_file", file_id, project_org, write=True)
    return True


def delete_file(project_id: int, file_id: int) -> bool:
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        result = conn.execute(
            "DELETE FROM jc_files WHERE id = ? AND project_id = ?", (file_id, project_id)
        )
        if result.rowcount:
            _bump_version(conn, project_id)
    if result.rowcount:
        _audit("jornada.file.delete", "jc_file", file_id, project_org, write=True)
    return bool(result.rowcount)


# ---------------------------------------------------------------------------
# Participantes
# ---------------------------------------------------------------------------

_PARTICIPANT_FIELDS = ("profile", "tempo_informado", "notes")


def upsert_participants(
    project_id: int,
    rows: Iterable[Dict[str, Any]],
    *,
    only_missing: bool = False,
    source: str = "manual",
) -> int:
    """Cria ou atualiza participantes pelo codigo.

    `only_missing=True` e o caminho da semente vinda de um upload: preenche o
    que esta vazio e nunca sobrescreve o que alguem ja editou na tela.
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
            values = {
                field: (str(item[field]).strip() if item.get(field) is not None else None)
                for field in _PARTICIPANT_FIELDS
                if field in item
            }
            existing = conn.execute(
                "SELECT * FROM jc_participants WHERE project_id = ? AND code = ?",
                (project_id, code),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO jc_participants (
                        organization_id, project_id, code, profile, tempo_informado,
                        notes, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_org,
                        project_id,
                        code,
                        values.get("profile") or None,
                        values.get("tempo_informado") or None,
                        values.get("notes") or None,
                        source,
                    ),
                )
                changed += 1
                continue
            updates = {}
            for field, value in values.items():
                if only_missing and existing[field]:
                    continue
                if (value or None) != (existing[field] or None):
                    updates[field] = value or None
            if updates:
                conn.execute(
                    "UPDATE jc_participants SET {}, updated_at = datetime('now','localtime') "
                    "WHERE id = ?".format(", ".join("{} = ?".format(k) for k in updates)),
                    list(updates.values()) + [existing["id"]],
                )
                changed += 1
        if changed:
            _bump_version(conn, project_id)
    if changed:
        _audit("jornada.participant.update", "jc_project", project_id, project_org, write=True)
    return changed


def list_participants(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute(
            "SELECT * FROM jc_participants WHERE project_id = ? ORDER BY code",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Gravacoes: so as decisoes do usuario
# ---------------------------------------------------------------------------

def set_recording_status(
    project_id: int,
    participant_code: str,
    task: str,
    store: str = "",
    *,
    status: str,
    reason: str = "",
    unit_override: Optional[str] = None,
) -> None:
    """Inclui, exclui ou devolve ao automatico uma gravacao (participante × tarefa × loja)."""

    if status not in RECORDING_STATUSES:
        raise ValueError("Status de gravacao desconhecido: {!r}".format(status))
    if status == "excluida" and not str(reason or "").strip():
        raise ValueError("Excluir uma gravacao da analise exige um motivo.")
    if unit_override is not None and unit_override not in UNIT_CHOICES:
        raise ValueError("Unidade desconhecida: {!r}".format(unit_override))
    actor = _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        conn.execute(
            """
            INSERT INTO jc_recordings (
                organization_id, project_id, participant_code, task, store,
                status, reason, unit_override, updated_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (project_id, participant_code, task, store) DO UPDATE SET
                status = excluded.status,
                reason = excluded.reason,
                unit_override = excluded.unit_override,
                updated_by_user_id = excluded.updated_by_user_id,
                updated_at = datetime('now','localtime')
            """,
            (
                project_org,
                project_id,
                participant_code,
                task,
                store or "",
                status,
                str(reason or "").strip() or None,
                unit_override,
                _actor_user_id(actor),
            ),
        )
        _bump_version(conn, project_id)
    _audit("jornada.recording.update", "jc_project", project_id, project_org, write=True)


def list_recording_overrides(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute(
            "SELECT * FROM jc_recordings WHERE project_id = ? ORDER BY participant_code, task",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Catalogo de AOIs: so os ajustes manuais
# ---------------------------------------------------------------------------

_AOI_FIELDS = ("kind", "brand", "line", "product", "part", "element", "shelf_weight", "include")


def save_aoi_overrides(project_id: int, rows: Iterable[Dict[str, Any]]) -> int:
    items = [row for row in rows if str(row.get("aoi") or "").strip()]
    if not items:
        return 0
    for item in items:
        kind = item.get("kind")
        if kind not in (None, "") and kind not in AOI_KINDS:
            raise ValueError("Tipo de AOI desconhecido: {!r}".format(kind))
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        for item in items:
            attrs = item.get("attrs")
            conn.execute(
                """
                INSERT INTO jc_aoi_catalog (
                    organization_id, project_id, store, aoi, kind, brand, line, product,
                    part, element, attrs_json, shelf_weight, include
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_id, store, aoi) DO UPDATE SET
                    kind = excluded.kind, brand = excluded.brand, line = excluded.line,
                    product = excluded.product, part = excluded.part,
                    element = excluded.element, attrs_json = excluded.attrs_json,
                    shelf_weight = excluded.shelf_weight, include = excluded.include,
                    updated_at = datetime('now','localtime')
                """,
                (
                    project_org,
                    project_id,
                    str(item.get("store") or ""),
                    str(item["aoi"]),
                    item.get("kind") or None,
                    item.get("brand") or None,
                    item.get("line") or None,
                    item.get("product") or None,
                    item.get("part") or None,
                    item.get("element") or None,
                    json.dumps(attrs, ensure_ascii=False) if attrs else None,
                    item.get("shelf_weight"),
                    0 if item.get("include") in (False, 0, "0") else 1,
                ),
            )
        _bump_version(conn, project_id)
    _audit("jornada.catalog.update", "jc_project", project_id, project_org, write=True)
    return len(items)


def delete_aoi_overrides(project_id: int, keys: Iterable[Tuple[str, str]]) -> int:
    pairs = [(str(store or ""), str(aoi)) for store, aoi in keys]
    if not pairs:
        return 0
    _require_write()
    organization_id = _active_organization_id()
    removed = 0
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        for store, aoi in pairs:
            removed += conn.execute(
                "DELETE FROM jc_aoi_catalog WHERE project_id = ? AND store = ? AND aoi = ?",
                (project_id, store, aoi),
            ).rowcount
        if removed:
            _bump_version(conn, project_id)
    if removed:
        _audit("jornada.catalog.delete", "jc_project", project_id, project_org, write=True)
    return removed


def list_aoi_overrides(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute(
            "SELECT * FROM jc_aoi_catalog WHERE project_id = ? ORDER BY store, aoi",
            (project_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["attrs"] = _json_or(item.pop("attrs_json"), {})
        item["include"] = bool(item["include"])
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Videos (o arquivo fica no disco; aqui so o indice)
# ---------------------------------------------------------------------------

def add_media(
    project_id: int,
    *,
    participant_code: str,
    task: str,
    store: str,
    filename: str,
    sha256: str,
    size_bytes: int,
    rel_path: str,
) -> Tuple[int, bool]:
    """Registra um video. Devolve (id, criado); um video repetido nao duplica."""

    actor = _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        existing = conn.execute(
            "SELECT id FROM jc_media WHERE project_id = ? AND sha256 = ?",
            (project_id, sha256),
        ).fetchone()
        if existing:
            return int(existing["id"]), False
        cursor = conn.execute(
            """
            INSERT INTO jc_media (
                organization_id, project_id, participant_code, task, store, filename,
                sha256, size_bytes, rel_path, created_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_org,
                project_id,
                participant_code,
                task,
                store or "",
                filename,
                sha256,
                int(size_bytes),
                rel_path,
                _actor_user_id(actor),
            ),
        )
        media_id = int(cursor.lastrowid)
    _audit("jornada.media.create", "jc_media", media_id, project_org, write=True)
    return media_id, True


def list_media(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute(
            "SELECT * FROM jc_media WHERE project_id = ? ORDER BY participant_code, task",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_media(project_id: int, media_id: int) -> Optional[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        row = conn.execute(
            "SELECT * FROM jc_media WHERE id = ? AND project_id = ?", (media_id, project_id)
        ).fetchone()
    return dict(row) if row else None


def delete_media(project_id: int, media_id: int) -> Optional[Dict[str, Any]]:
    """Remove o registro e o arquivo. Devolve o registro removido."""

    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        row = conn.execute(
            "SELECT * FROM jc_media WHERE id = ? AND project_id = ?", (media_id, project_id)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM jc_media WHERE id = ?", (media_id,))
    _audit("jornada.media.delete", "jc_media", media_id, project_org, write=True)
    try:
        from utils import jornada_media

        jornada_media.delete_files([row["rel_path"]])
    except Exception:
        pass
    return dict(row)


# ---------------------------------------------------------------------------
# Entrevistas qualitativas
# ---------------------------------------------------------------------------

def add_interview(
    project_id: int, titulo: str, texto: str, participante_id: str = ""
) -> int:
    if not str(titulo or "").strip() or not str(texto or "").strip():
        raise ValueError("Titulo e transcricao sao obrigatorios.")
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        cursor = conn.execute(
            """
            INSERT INTO jc_interviews (organization_id, project_id, titulo, participante_id, texto)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_org,
                project_id,
                titulo.strip(),
                str(participante_id or "").strip(),
                texto.strip(),
            ),
        )
        interview_id = int(cursor.lastrowid)
        _bump_version(conn, project_id)
    _audit("jornada.interview.create", "jc_interview", interview_id, project_org, write=True)
    return interview_id


def list_interviews(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute(
            "SELECT * FROM jc_interviews WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def delete_interview(project_id: int, interview_id: int) -> bool:
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        result = conn.execute(
            "DELETE FROM jc_interviews WHERE id = ? AND project_id = ?",
            (interview_id, project_id),
        )
        if result.rowcount:
            _bump_version(conn, project_id)
    if result.rowcount:
        _audit("jornada.interview.delete", "jc_interview", interview_id, project_org, write=True)
    return bool(result.rowcount)


# ---------------------------------------------------------------------------
# Analises de IA
# ---------------------------------------------------------------------------

def _decode_analysis(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    citations = _json_or(item.get("citations"), [])
    item["citations"] = citations if isinstance(citations, list) else []
    item["search"] = _json_or(item.pop("search_json", None), {})
    item["filters"] = _json_or(item.pop("filters_json", None), {})
    return item


def save_analysis(
    project_id: int,
    *,
    model: str,
    mode: str,
    analysis_text: str,
    citations: Optional[list] = None,
    search: Optional[dict] = None,
    filters: Optional[dict] = None,
    data_version: Optional[int] = None,
) -> int:
    actor = _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        cursor = conn.execute(
            """
            INSERT INTO jc_analyses (
                organization_id, project_id, model, mode, analysis_text, citations,
                search_json, filters_json, data_version, created_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_org,
                project_id,
                model,
                mode,
                str(analysis_text or "").strip(),
                json.dumps(citations or [], ensure_ascii=False),
                json.dumps(search or {}, ensure_ascii=False),
                json.dumps(filters or {}, ensure_ascii=False),
                data_version,
                _actor_user_id(actor),
            ),
        )
        analysis_id = int(cursor.lastrowid)
    _audit("jornada.analysis.create", "jc_analysis", analysis_id, project_org, write=True)
    return analysis_id


def list_analyses(project_id: int) -> List[Dict[str, Any]]:
    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        rows = conn.execute(
            "SELECT * FROM jc_analyses WHERE project_id = ? ORDER BY created_at DESC, id DESC",
            (project_id,),
        ).fetchall()
    return [_decode_analysis(row) for row in rows]


def get_latest_analysis(project_id: int) -> Optional[Dict[str, Any]]:
    analyses = list_analyses(project_id)
    return analyses[0] if analyses else None


def set_analysis_kb_file(project_id: int, analysis_id: int, kb_file_id: str) -> None:
    _require_write()
    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        conn.execute(
            "UPDATE jc_analyses SET kb_file_id = ? WHERE id = ? AND project_id = ?",
            (kb_file_id, analysis_id, project_id),
        )
    _audit("jornada.analysis.update", "jc_analysis", analysis_id, project_org, write=True)


def audit_export(project_id: int, kind: str) -> None:
    """Registra um download do projeto, de qualquer papel.

    Leitura comum nao vai ao audit_log (encheria a tabela a cada rerun), mas
    exportar e um clique explicito que tira os dados do app: fica registrado
    como `jornada.export.<tipo>`.
    """

    organization_id = _active_organization_id()
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
    _audit("jornada.export.{}".format(kind), "jc_project", project_id, project_org, write=True)


def delete_analyses(project_id: int, analysis_ids: Iterable[int]) -> int:
    """Apaga analises e a copia que cada uma deixou na base de conhecimento."""

    ids = [int(value) for value in analysis_ids]
    if not ids:
        return 0
    _require_write()
    organization_id = _active_organization_id()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        project_org = _project_org(conn, project_id, organization_id)
        kb_files = [
            row["kb_file_id"]
            for row in conn.execute(
                "SELECT kb_file_id FROM jc_analyses WHERE project_id = ? AND id IN ({})".format(
                    placeholders
                ),
                [project_id] + ids,
            )
            if row["kb_file_id"]
        ]
        deleted = conn.execute(
            "DELETE FROM jc_analyses WHERE project_id = ? AND id IN ({})".format(placeholders),
            [project_id] + ids,
        ).rowcount
    if deleted:
        _audit("jornada.analysis.delete", "jc_project", project_id, project_org, write=True)
    if kb_files:
        _remove_from_knowledge_base(0, kb_files)
    return deleted


# ---------------------------------------------------------------------------
# Leitura para o modelo (sem sessao) e legado
# ---------------------------------------------------------------------------

def load_project_bundle(project_id: int, organization_id: int) -> Dict[str, Any]:
    """Tudo que o modelo precisa, lido pela organizacao DO PROJETO.

    Sem dependencia de sessao, para caber num `st.cache_data`: quem chama ja
    autorizou o acesso ao obter o projeto por `get_project`, e passa a
    organizacao dele — que tambem entra na chave do cache.
    """

    with _connect() as conn:
        project = conn.execute(
            "SELECT * FROM jc_projects WHERE id = ? AND organization_id = ?",
            (project_id, organization_id),
        ).fetchone()
        if project is None:
            raise ValueError("Projeto nao encontrado para a organizacao informada.")
        files = []
        for row in conn.execute(
            """
            SELECT id, filename, kind, sha256, meta_json, content, created_at
            FROM jc_files WHERE project_id = ? AND is_active = 1 ORDER BY id
            """,
            (project_id,),
        ):
            files.append(
                {
                    "id": int(row["id"]),
                    "filename": row["filename"],
                    "kind": row["kind"],
                    "sha256": row["sha256"],
                    "meta": _json_or(row["meta_json"], {}),
                    "content": zlib.decompress(row["content"]),
                    "created_at": row["created_at"],
                }
            )
        participants = [
            dict(row)
            for row in conn.execute(
                "SELECT code, profile, tempo_informado, notes, source FROM jc_participants "
                "WHERE project_id = ? ORDER BY code",
                (project_id,),
            )
        ]
        recordings = [
            dict(row)
            for row in conn.execute(
                "SELECT participant_code, task, store, status, reason, unit_override "
                "FROM jc_recordings WHERE project_id = ?",
                (project_id,),
            )
        ]
        overrides = []
        for row in conn.execute(
            "SELECT * FROM jc_aoi_catalog WHERE project_id = ?", (project_id,)
        ):
            item = dict(row)
            item["attrs"] = _json_or(item.pop("attrs_json"), {})
            item["include"] = bool(item["include"])
            overrides.append(item)
        interviews = [
            dict(row)
            for row in conn.execute(
                "SELECT titulo, participante_id, texto FROM jc_interviews "
                "WHERE project_id = ? ORDER BY id",
                (project_id,),
            )
        ]
    project_dict = dict(project)
    return {
        "project": project_dict,
        "settings": project_settings(project_dict),
        "files": files,
        "participants": participants,
        "recordings": recordings,
        "aoi_overrides": overrides,
        "interviews": interviews,
    }


def _blob_to_dataframe(blob: Optional[bytes]) -> pd.DataFrame:
    if not blob:
        return pd.DataFrame()
    try:
        decompressed = zlib.decompress(blob).decode("utf-8")
        return pd.read_json(io.StringIO(decompressed), orient="table")
    except Exception:
        return pd.DataFrame()


def get_legacy_dataset(project_id: int) -> Dict[str, pd.DataFrame]:
    """Tabelas `Banco_*` gravadas pela versao de 0dc853f, somente leitura."""

    organization_id = _active_organization_id()
    with _connect() as conn:
        _project_org(conn, project_id, organization_id)
        row = conn.execute(
            """
            SELECT tabelas_blob, por_marca_blob, medias_blob, visual_share_blob
            FROM jc_datasets WHERE project_id = ? ORDER BY id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    if not row:
        return {}
    result = {}
    for key in ("tabelas", "por_marca", "medias", "visual_share"):
        frame = _blob_to_dataframe(row[key + "_blob"])
        if not frame.empty:
            result[key] = frame
    return result
