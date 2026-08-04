"""
Prosodia DB — Camada SQLite para persistência de projetos, áudios, análises e
verificações de qualidade do módulo de Prosódia.

Banco: nenc-dashboard/prosodia.db  (criado automaticamente na primeira chamada)
"""

import sqlite3
import json
import io
import csv
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from utils import auth

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "prosodia.db"

DEFAULT_QR_VERIFICATION_TEXT = (
    "Não delete essas informações. Elas estão associadas ao serviço que você está avaliando. "
    "Peço que envie agora esse código, antes da sua mensagem."
)


# ---------------------------------------------------------------------------
# Conexão
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
    """Use the server-validated organization selected for the current session."""

    return auth.active_organization_id()


def _audit(action: str, target_type: str, target_id: Optional[int], organization_id: int) -> None:
    auth.audit_business_access(action, target_type, target_id, organization_id)


def _claim_external_project_resources(
    whatsapp_campaign_id: Optional[int], api_project_id: Optional[int]
) -> None:
    from utils.organization_data import claim_external_resource

    if whatsapp_campaign_id is not None:
        claim_external_resource("whatsapp_campaign", whatsapp_campaign_id)
    if api_project_id is not None:
        claim_external_resource("whatsapp_api_project", api_project_id)


def _project_is_visible(conn: sqlite3.Connection, project_id: int, organization_id: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM projects WHERE id = ? AND organization_id = ?",
            (project_id, organization_id),
        ).fetchone()
    )


def _audio_is_visible(conn: sqlite3.Connection, audio_id: int, organization_id: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM audios WHERE id = ? AND organization_id = ?",
            (audio_id, organization_id),
        ).fetchone()
    )


def _require_visible_project(conn: sqlite3.Connection, project_id: int, organization_id: int) -> None:
    if not _project_is_visible(conn, project_id, organization_id):
        raise ValueError("Projeto nao encontrado para a organizacao ativa.")


def _require_visible_audio(conn: sqlite3.Connection, audio_id: int, organization_id: int) -> None:
    if not _audio_is_visible(conn, audio_id, organization_id):
        raise ValueError("Audio nao encontrado para a organizacao ativa.")


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info({})".format(table_name))}
    if column_name not in columns:
        conn.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table_name, column_name, definition))


def _legacy_organization_id(conn: sqlite3.Connection) -> int:
    raw_organization_id = os.environ.get("NENC_LEGACY_ORGANIZATION_ID", "").strip()
    if not raw_organization_id:
        raise RuntimeError(
            "A migracao de dados legados exige NENC_LEGACY_ORGANIZATION_ID. "
            "Defina a organizacao que possui os dados existentes antes de continuar."
        )
    try:
        organization_id = int(raw_organization_id)
    except ValueError as error:
        raise RuntimeError("NENC_LEGACY_ORGANIZATION_ID deve ser um inteiro valido.") from error
    organization = conn.execute(
        "SELECT id FROM organizations WHERE id = ?", (organization_id,)
    ).fetchone()
    if organization is None:
        raise RuntimeError("NENC_LEGACY_ORGANIZATION_ID nao corresponde a uma organizacao existente.")
    return organization_id


def _migrate_organization_ownership(conn: sqlite3.Connection) -> None:
    """Backfill legacy rows only when an explicit organization has been selected."""

    legacy_projects = conn.execute(
        "SELECT COUNT(*) AS total FROM projects WHERE organization_id IS NULL"
    ).fetchone()["total"]
    if legacy_projects:
        organization_id = _legacy_organization_id(conn)
        conn.execute(
            "UPDATE projects SET organization_id = ? WHERE organization_id IS NULL",
            (organization_id,),
        )

    ownership_sources = (
        ("audios", "project_id", "projects"),
        ("analyses", "audio_id", "audios"),
        ("quality_checks", "audio_id", "audios"),
        ("project_analyses", "project_id", "projects"),
        ("high_activations", "audio_id", "audios"),
    )
    for table_name, foreign_key, parent_table in ownership_sources:
        conn.execute(
            """
            UPDATE {table_name}
            SET organization_id = (
                SELECT organization_id
                FROM {parent_table}
                WHERE {parent_table}.id = {table_name}.{foreign_key}
            )
            WHERE organization_id IS NULL
            """.format(
                table_name=table_name,
                foreign_key=foreign_key,
                parent_table=parent_table,
            )
        )
        unresolved = conn.execute(
            "SELECT COUNT(*) AS total FROM {} WHERE organization_id IS NULL".format(table_name)
        ).fetchone()["total"]
        if unresolved:
            raise RuntimeError(
                "A migracao deixou registros sem organizacao em {}.".format(table_name)
            )

    for table_name in (
        "projects",
        "audios",
        "analyses",
        "quality_checks",
        "project_analyses",
        "high_activations",
    ):
        invalid = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM {table_name}
            WHERE organization_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM organizations
                  WHERE organizations.id = {table_name}.organization_id
              )
            """.format(table_name=table_name)
        ).fetchone()["total"]
        if invalid:
            raise RuntimeError(
                "A migracao encontrou organizacoes invalidas em {}.".format(table_name)
            )


# ---------------------------------------------------------------------------
# Inicialização do schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Cria as tabelas se ainda não existirem."""
    auth.initialize_auth_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                name         TEXT    NOT NULL,
                especialidade TEXT,
                historico    TEXT,
                problemas    TEXT,
                questions    TEXT,
                entities     TEXT,
                briefing_filename TEXT,
                briefing_text TEXT,
                whatsapp_campaign_id INTEGER,
                api_project_id INTEGER,
                quality_thresholds TEXT,
                created_at   TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS audios (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id         INTEGER NOT NULL REFERENCES organizations(id),
                project_id              INTEGER NOT NULL
                                        REFERENCES projects(id) ON DELETE CASCADE,
                session_id              TEXT    NOT NULL,
                prosodia_json           BLOB,
                transcricao_csv         BLOB,
                sincronizado_csv        BLOB,
                openai_file_id_prosodia TEXT,
                openai_file_id_transcricao TEXT,
                created_at              TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS analyses (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                audio_id       INTEGER NOT NULL
                               REFERENCES audios(id) ON DELETE CASCADE,
                model          TEXT,
                analysis_text  TEXT,
                citations      TEXT,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS quality_checks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                audio_id       INTEGER NOT NULL
                               REFERENCES audios(id) ON DELETE CASCADE,
                overall_status TEXT,
                checks_json    TEXT,
                coverage_json  TEXT,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS project_analyses (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                project_id     INTEGER NOT NULL
                               REFERENCES projects(id) ON DELETE CASCADE,
                model          TEXT,
                analysis_text  TEXT,
                citations      TEXT,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS high_activations (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                audio_id       INTEGER NOT NULL
                               REFERENCES audios(id) ON DELETE CASCADE,
                moments_json   TEXT,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            );
            """
        )

        # Migração incremental para bancos já existentes.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
        if "briefing_filename" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN briefing_filename TEXT")
        if "briefing_text" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN briefing_text TEXT")
        if "whatsapp_campaign_id" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN whatsapp_campaign_id INTEGER")
        if "quality_thresholds" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN quality_thresholds TEXT")
        if "api_project_id" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN api_project_id INTEGER")
        if "entities" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN entities TEXT")
        if "qr_verification_text" not in cols:
            conn.execute("ALTER TABLE projects ADD COLUMN qr_verification_text TEXT")

        audio_cols = {r["name"] for r in conn.execute("PRAGMA table_info(audios)").fetchall()}
        if "whatsapp_message_id" not in audio_cols:
            conn.execute("ALTER TABLE audios ADD COLUMN whatsapp_message_id TEXT")

        for table_name in (
            "projects",
            "audios",
            "analyses",
            "quality_checks",
            "project_analyses",
            "high_activations",
        ):
            _ensure_column(conn, table_name, "organization_id", "INTEGER")

        _migrate_organization_ownership(conn)
        for table_name in (
            "projects",
            "audios",
            "analyses",
            "quality_checks",
            "project_analyses",
            "high_activations",
        ):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_{}_organization ON {}(organization_id)".format(
                    table_name, table_name
                )
            )


# ---------------------------------------------------------------------------
# CRUD — Projects
# ---------------------------------------------------------------------------

def create_project(
    name: str,
    especialidade: str = "",
    historico: str = "",
    problemas: str = "",
    questions: str = "",
    entities: str = "",
    briefing_filename: str = "",
    briefing_text: str = "",
    whatsapp_campaign_id: Optional[int] = None,
    quality_thresholds: Optional[str] = None,
    api_project_id: Optional[int] = None,
    qr_verification_text: Optional[str] = None,
) -> int:
    """Cria um novo projeto. Retorna o ID gerado."""
    _claim_external_project_resources(whatsapp_campaign_id, api_project_id)
    organization_id = _active_organization_id()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO projects (
                    organization_id,
                    name,
                    especialidade,
                    historico,
                    problemas,
                    questions,
                    entities,
                    briefing_filename,
                    briefing_text,
                    whatsapp_campaign_id,
                    quality_thresholds,
                    api_project_id,
                    qr_verification_text
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                organization_id,
                name,
                especialidade,
                historico,
                problemas,
                questions,
                entities,
                briefing_filename,
                briefing_text,
                whatsapp_campaign_id,
                quality_thresholds,
                api_project_id,
                qr_verification_text,
            ),
        )
        project_id = cur.lastrowid
    _audit("prosodia.project.create", "project", project_id, organization_id)
    return project_id


def get_projects() -> List[Dict]:
    """Retorna todos os projetos com contagem de áudios."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, COUNT(a.id) AS n_audios
            FROM projects p
            LEFT JOIN audios a ON a.project_id = p.id AND a.organization_id = p.organization_id
            WHERE p.organization_id = ?
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """,
            (organization_id,),
        ).fetchall()
    _audit("prosodia.project.list", "project", None, organization_id)
    return [dict(r) for r in rows]


def get_project(project_id: int) -> Optional[Dict]:
    """Retorna um projeto pelo ID, ou None se não encontrado."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ? AND organization_id = ?",
            (project_id, organization_id),
        ).fetchone()
    _audit("prosodia.project.read", "project", project_id, organization_id)
    return dict(row) if row else None


def update_project(
    project_id: int,
    name: str,
    especialidade: str = "",
    historico: str = "",
    problemas: str = "",
    questions: str = "",
    entities: str = "",
    briefing_filename: str = "",
    briefing_text: str = "",
    whatsapp_campaign_id: Optional[int] = None,
    quality_thresholds: Optional[str] = None,
    api_project_id: Optional[int] = None,
    qr_verification_text: Optional[str] = None,
) -> None:
    """Atualiza os campos de um projeto existente."""
    _claim_external_project_resources(whatsapp_campaign_id, api_project_id)
    organization_id = _active_organization_id()
    with _connect() as conn:
        result = conn.execute(
            """UPDATE projects
               SET name=?,
                   especialidade=?,
                   historico=?,
                   problemas=?,
                   questions=?,
                   entities=?,
                   briefing_filename=?,
                   briefing_text=?,
                   whatsapp_campaign_id=?,
                   quality_thresholds=?,
                   api_project_id=?,
                   qr_verification_text=?
               WHERE id=? AND organization_id=?""",
            (
                name,
                especialidade,
                historico,
                problemas,
                questions,
                entities,
                briefing_filename,
                briefing_text,
                whatsapp_campaign_id,
                quality_thresholds,
                api_project_id,
                qr_verification_text,
                project_id,
                organization_id,
            ),
        )
    if result.rowcount:
        _audit("prosodia.project.update", "project", project_id, organization_id)


def delete_project(project_id: int) -> None:
    """Remove projeto (e em cascata: áudios, análises, quality_checks)."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        result = conn.execute(
            "DELETE FROM projects WHERE id = ? AND organization_id = ?",
            (project_id, organization_id),
        )
    if result.rowcount:
        _audit("prosodia.project.delete", "project", project_id, organization_id)


# ---------------------------------------------------------------------------
# CRUD — Audios
# ---------------------------------------------------------------------------

def create_audio(
    project_id: int,
    session_id: str,
    prosodia_json: Optional[bytes] = None,
    transcricao_csv: Optional[bytes] = None,
    sincronizado_csv: Optional[bytes] = None,
    whatsapp_message_id: Optional[str] = None,
) -> int:
    """Salva um áudio com seus arquivos brutos. Retorna o ID gerado."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        _require_visible_project(conn, project_id, organization_id)
        cur = conn.execute(
            """INSERT INTO audios
               (organization_id, project_id, session_id, prosodia_json, transcricao_csv, sincronizado_csv,
                whatsapp_message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (organization_id, project_id, session_id, prosodia_json, transcricao_csv, sincronizado_csv,
             whatsapp_message_id),
        )
        audio_id = cur.lastrowid
    _audit("prosodia.audio.create", "audio", audio_id, organization_id)
    return audio_id


def get_audios(project_id: int) -> List[Dict]:
    """Retorna todos os áudios de um projeto com status de KB e análise."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                a.*,
                (SELECT overall_status FROM quality_checks q
                 WHERE q.audio_id = a.id AND q.organization_id = a.organization_id
                 ORDER BY q.created_at DESC LIMIT 1) AS quality_status,
                (SELECT COUNT(*) FROM analyses an
                 WHERE an.audio_id = a.id AND an.organization_id = a.organization_id) AS n_analyses
            FROM audios a
            WHERE a.project_id = ? AND a.organization_id = ?
            ORDER BY a.created_at DESC
            """,
            (project_id, organization_id),
        ).fetchall()
    _audit("prosodia.audio.list", "project", project_id, organization_id)
    return [dict(r) for r in rows]


def _ts_to_seconds(ts: str) -> float:
    parts = str(ts).strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _calculate_duration(
    prosodia_json: Optional[bytes],
    transcricao_csv: Optional[bytes],
    sincronizado_csv: Optional[bytes],
) -> float:
    max_time = 0.0

    # 1. Try sincronizado_csv
    if sincronizado_csv:
        try:
            content = sincronizado_csv.decode("utf-8", errors="ignore")
            reader = csv.reader(io.StringIO(content))
            header = next(reader, None)
            if header:
                header = [c.strip() for c in header]
                end_s_idx = None
                start_s_idx = None
                if "end_s" in header:
                    end_s_idx = header.index("end_s")
                elif "start_s" in header:
                    start_s_idx = header.index("start_s")
                
                if end_s_idx is not None or start_s_idx is not None:
                    idx = end_s_idx if end_s_idx is not None else start_s_idx
                    for row in reader:
                        if len(row) > idx:
                            try:
                                val = float(row[idx])
                                if val > max_time:
                                    max_time = val
                            except ValueError:
                                pass
        except Exception:
            pass

    # 2. Try prosodia_json (VAD)
    if prosodia_json:
        try:
            obj = json.loads(prosodia_json.decode("utf-8", errors="ignore"))
            vad_raw = obj.get("result", obj).get("vad", [])
            for seg in vad_raw:
                end = float(seg.get("end", 0))
                if end > max_time:
                    max_time = end
        except Exception:
            pass

    # 3. Try transcricao_csv
    if transcricao_csv:
        try:
            content = transcricao_csv.decode("utf-8", errors="ignore")
            reader = csv.reader(io.StringIO(content))
            header = next(reader, None)
            if header:
                header = [c.strip() for c in header]
                if "Timestamp" in header:
                    ts_idx = header.index("Timestamp")
                    for row in reader:
                        if len(row) > ts_idx:
                            sec = _ts_to_seconds(row[ts_idx])
                            if sec > max_time:
                                max_time = sec
        except Exception:
            pass

    return max_time


def _format_duration(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "00:00"
    s = int(round(seconds))
    hrs = s // 3600
    mins = (s % 3600) // 60
    secs = s % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def get_audios_for_interviews(project_id: int) -> List[Dict]:
    """
    Retorna áudios de um projeto com resumo da última verificação de qualidade
    e contagem de análises, pronto para tabela da página Entrevistas.
    """
    organization_id = _active_organization_id()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.project_id,
                a.session_id,
                a.created_at,
                a.prosodia_json,
                a.transcricao_csv,
                a.sincronizado_csv,
                a.openai_file_id_prosodia,
                a.openai_file_id_transcricao,
                (
                    SELECT q.overall_status
                    FROM quality_checks q
                    WHERE q.audio_id = a.id AND q.organization_id = a.organization_id
                    ORDER BY q.created_at DESC
                    LIMIT 1
                ) AS quality_status,
                (
                    SELECT q.checks_json
                    FROM quality_checks q
                    WHERE q.audio_id = a.id AND q.organization_id = a.organization_id
                    ORDER BY q.created_at DESC
                    LIMIT 1
                ) AS checks_json,
                (
                    SELECT q.coverage_json
                    FROM quality_checks q
                    WHERE q.audio_id = a.id AND q.organization_id = a.organization_id
                    ORDER BY q.created_at DESC
                    LIMIT 1
                ) AS coverage_json,
                (
                    SELECT COUNT(*)
                    FROM analyses an
                    WHERE an.audio_id = a.id AND an.organization_id = a.organization_id
                ) AS n_analyses
            FROM audios a
            WHERE a.project_id = ? AND a.organization_id = ?
            ORDER BY a.created_at DESC
            """,
            (project_id, organization_id),
        ).fetchall()

    _audit("prosodia.audio.interviews", "project", project_id, organization_id)

    result: List[Dict] = []
    for row in rows:
        d = dict(row)

        checks = json.loads(d.get("checks_json") or "[]")
        coverage = json.loads(d.get("coverage_json") or "[]")

        n_pass = sum(1 for c in checks if c.get("status") == "pass")
        n_warn = sum(1 for c in checks if c.get("status") == "warn")
        n_fail = sum(1 for c in checks if c.get("status") == "fail")

        n_cov_total = len(coverage)
        n_ai_found = sum(1 for c in coverage if c.get("covered_ai") is True)
        n_kw_found = sum(1 for c in coverage if c.get("covered_keywords") is True)

        d["checks_ok"] = n_pass
        d["checks_warn"] = n_warn
        d["checks_fail"] = n_fail

        d["coverage_total"] = n_cov_total
        d["coverage_ai_found"] = n_ai_found
        d["coverage_kw_found"] = n_kw_found

        d["coverage_ai_pct"] = (n_ai_found / n_cov_total * 100.0) if n_cov_total else 0.0
        d["coverage_kw_pct"] = (n_kw_found / n_cov_total * 100.0) if n_cov_total else 0.0

        d["kb_ok"] = bool(
            d.get("openai_file_id_prosodia") or d.get("openai_file_id_transcricao")
        )
        d["quality_status"] = d.get("quality_status") or "pending"

        # Calculate duration
        dur_s = _calculate_duration(
            d.get("prosodia_json"),
            d.get("transcricao_csv"),
            d.get("sincronizado_csv"),
        )
        d["duration_str"] = _format_duration(dur_s)

        result.append(d)

    return result


def get_audio(audio_id: int) -> Optional[Dict]:
    """Retorna um áudio pelo ID."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM audios WHERE id = ? AND organization_id = ?",
            (audio_id, organization_id),
        ).fetchone()
    _audit("prosodia.audio.read", "audio", audio_id, organization_id)
    return dict(row) if row else None


def delete_audio(audio_id: int) -> None:
    """Remove um áudio (cascata: análises, quality_checks)."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        result = conn.execute(
            "DELETE FROM audios WHERE id = ? AND organization_id = ?",
            (audio_id, organization_id),
        )
    if result.rowcount:
        _audit("prosodia.audio.delete", "audio", audio_id, organization_id)


def update_audio_openai_ids(
    audio_id: int,
    file_id_prosodia: Optional[str],
    file_id_transcricao: Optional[str],
) -> None:
    """Atualiza os IDs de arquivo OpenAI de um áudio."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        result = conn.execute(
            """UPDATE audios
               SET openai_file_id_prosodia=?, openai_file_id_transcricao=?
               WHERE id=? AND organization_id=?""",
            (file_id_prosodia, file_id_transcricao, audio_id, organization_id),
        )
    if result.rowcount:
        _audit("prosodia.audio.update_openai_ids", "audio", audio_id, organization_id)


def update_audio_content(
    audio_id: int,
    prosodia_json: bytes,
    transcricao_csv: bytes,
    sincronizado_csv: bytes,
) -> None:
    """Atualiza os blobs de conteúdo (prosódia, transcrição, sincronizado) de um áudio."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        result = conn.execute(
            """UPDATE audios
               SET prosodia_json=?, transcricao_csv=?, sincronizado_csv=?
               WHERE id=? AND organization_id=?""",
            (prosodia_json, transcricao_csv, sincronizado_csv, audio_id, organization_id),
        )
    if result.rowcount:
        _audit("prosodia.audio.update_content", "audio", audio_id, organization_id)


# ---------------------------------------------------------------------------
# CRUD — Analyses
# ---------------------------------------------------------------------------

def save_analysis(
    audio_id: int,
    model: str,
    analysis_text: str,
    citations: Optional[list] = None,
) -> int:
    """Salva uma análise de IA. Retorna o ID gerado."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        _require_visible_audio(conn, audio_id, organization_id)
        cur = conn.execute(
            """INSERT INTO analyses (organization_id, audio_id, model, analysis_text, citations)
               VALUES (?, ?, ?, ?, ?)""",
            (organization_id, audio_id, model, analysis_text, json.dumps(citations or [])),
        )
        analysis_id = cur.lastrowid
    _audit("prosodia.analysis.create", "analysis", analysis_id, organization_id)
    return analysis_id


def get_latest_analysis(audio_id: int) -> Optional[Dict]:
    """Retorna a análise mais recente de um áudio, ou None."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM analyses WHERE audio_id = ? AND organization_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (audio_id, organization_id),
        ).fetchone()
    _audit("prosodia.analysis.read_latest", "audio", audio_id, organization_id)
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d.get("citations") or "[]")
    return d


def get_analyses(audio_id: int) -> List[Dict]:
    """Retorna todas as análises de um áudio, mais recentes primeiro."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE audio_id = ? AND organization_id = ? ORDER BY created_at DESC",
            (audio_id, organization_id),
        ).fetchall()
    _audit("prosodia.analysis.list", "audio", audio_id, organization_id)
    result = []
    for r in rows:
        d = dict(r)
        d["citations"] = json.loads(d.get("citations") or "[]")
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# CRUD — Project Analyses (Análise Geral)
# ---------------------------------------------------------------------------

def save_project_analysis(
    project_id: int,
    model: str,
    analysis_text: str,
    citations: Optional[list] = None,
) -> int:
    """Salva uma análise geral de projeto. Retorna o ID gerado."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        _require_visible_project(conn, project_id, organization_id)
        cur = conn.execute(
            """INSERT INTO project_analyses
               (organization_id, project_id, model, analysis_text, citations)
               VALUES (?, ?, ?, ?, ?)""",
            (organization_id, project_id, model, analysis_text, json.dumps(citations or [])),
        )
        analysis_id = cur.lastrowid
    _audit("prosodia.project_analysis.create", "project_analysis", analysis_id, organization_id)
    return analysis_id


def get_latest_project_analysis(project_id: int) -> Optional[Dict]:
    """Retorna a análise geral mais recente de um projeto, ou None."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM project_analyses WHERE project_id = ? AND organization_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, organization_id),
        ).fetchone()
    _audit("prosodia.project_analysis.read_latest", "project", project_id, organization_id)
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d.get("citations") or "[]")
    return d


def get_project_analyses(project_id: int) -> List[Dict]:
    """Retorna histórico de análises gerais de um projeto, mais recentes primeiro."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_analyses WHERE project_id = ? AND organization_id = ? ORDER BY created_at DESC",
            (project_id, organization_id),
        ).fetchall()

    _audit("prosodia.project_analysis.list", "project", project_id, organization_id)

    result = []
    for r in rows:
        d = dict(r)
        d["citations"] = json.loads(d.get("citations") or "[]")
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# CRUD — Quality Checks
# ---------------------------------------------------------------------------

def save_quality_check(
    audio_id: int,
    overall_status: str,
    checks: list,
    coverage: list,
) -> int:
    """Salva resultado de verificação de qualidade. Retorna o ID gerado."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        _require_visible_audio(conn, audio_id, organization_id)
        cur = conn.execute(
            """INSERT INTO quality_checks
               (organization_id, audio_id, overall_status, checks_json, coverage_json)
               VALUES (?, ?, ?, ?, ?)""",
            (organization_id, audio_id, overall_status, json.dumps(checks), json.dumps(coverage)),
        )
        quality_check_id = cur.lastrowid
    _audit("prosodia.quality_check.create", "quality_check", quality_check_id, organization_id)
    return quality_check_id


def get_latest_quality_check(audio_id: int) -> Optional[Dict]:
    """Retorna a verificação de qualidade mais recente de um áudio, ou None."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM quality_checks WHERE audio_id = ? AND organization_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (audio_id, organization_id),
        ).fetchone()
    _audit("prosodia.quality_check.read_latest", "audio", audio_id, organization_id)
    if not row:
        return None
    d = dict(row)
    d["checks"] = json.loads(d.get("checks_json") or "[]")
    d["coverage"] = json.loads(d.get("coverage_json") or "[]")
    return d


def get_project_questions(project_id: int) -> List[str]:
    """Retorna a lista de perguntas do projeto (uma por linha)."""
    project = get_project(project_id)
    if not project:
        return []
    raw = project.get("questions", "") or ""
    return [q.strip() for q in raw.splitlines() if q.strip()]


def save_high_activations(audio_id: int, moments: list) -> int:
    """Salva os momentos de maior ativação prosódica de um áudio/entrevista."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        _require_visible_audio(conn, audio_id, organization_id)
        cur = conn.execute(
            """INSERT INTO high_activations (organization_id, audio_id, moments_json)
               VALUES (?, ?, ?)""",
            (organization_id, audio_id, json.dumps(moments)),
        )
        activation_id = cur.lastrowid
    _audit("prosodia.high_activation.create", "high_activation", activation_id, organization_id)
    return activation_id


def get_latest_high_activations(audio_id: int) -> Optional[List[Dict]]:
    """Retorna a lista de momentos de maior ativação mais recente de um áudio, ou None."""
    organization_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM high_activations WHERE audio_id = ? AND organization_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (audio_id, organization_id),
        ).fetchone()
    _audit("prosodia.high_activation.read_latest", "audio", audio_id, organization_id)
    if not row:
        return None
    d = dict(row)
    return json.loads(d.get("moments_json") or "[]")
