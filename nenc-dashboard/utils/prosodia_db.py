"""
Prosodia DB — Camada SQLite para persistência de projetos, áudios, análises e
verificações de qualidade do módulo de Prosódia.

Banco: nenc-dashboard/prosodia.db  (criado automaticamente na primeira chamada)
"""

import sqlite3
import json
import io
from pathlib import Path
from typing import Dict, List, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "prosodia.db"


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Inicialização do schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Cria as tabelas se ainda não existirem."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                especialidade TEXT,
                historico    TEXT,
                problemas    TEXT,
                questions    TEXT,
                created_at   TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS audios (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
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
                audio_id       INTEGER NOT NULL
                               REFERENCES audios(id) ON DELETE CASCADE,
                model          TEXT,
                analysis_text  TEXT,
                citations      TEXT,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS quality_checks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                audio_id       INTEGER NOT NULL
                               REFERENCES audios(id) ON DELETE CASCADE,
                overall_status TEXT,
                checks_json    TEXT,
                coverage_json  TEXT,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS project_analyses (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id     INTEGER NOT NULL
                               REFERENCES projects(id) ON DELETE CASCADE,
                model          TEXT,
                analysis_text  TEXT,
                citations      TEXT,
                created_at     TEXT    DEFAULT (datetime('now','localtime'))
            );
            """
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
) -> int:
    """Cria um novo projeto. Retorna o ID gerado."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO projects (name, especialidade, historico, problemas, questions)
               VALUES (?, ?, ?, ?, ?)""",
            (name, especialidade, historico, problemas, questions),
        )
        return cur.lastrowid


def get_projects() -> List[Dict]:
    """Retorna todos os projetos com contagem de áudios."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*, COUNT(a.id) AS n_audios
            FROM projects p
            LEFT JOIN audios a ON a.project_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: int) -> Optional[Dict]:
    """Retorna um projeto pelo ID, ou None se não encontrado."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    return dict(row) if row else None


def update_project(
    project_id: int,
    name: str,
    especialidade: str = "",
    historico: str = "",
    problemas: str = "",
    questions: str = "",
) -> None:
    """Atualiza os campos de um projeto existente."""
    with _connect() as conn:
        conn.execute(
            """UPDATE projects
               SET name=?, especialidade=?, historico=?, problemas=?, questions=?
               WHERE id=?""",
            (name, especialidade, historico, problemas, questions, project_id),
        )


def delete_project(project_id: int) -> None:
    """Remove projeto (e em cascata: áudios, análises, quality_checks)."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


# ---------------------------------------------------------------------------
# CRUD — Audios
# ---------------------------------------------------------------------------

def create_audio(
    project_id: int,
    session_id: str,
    prosodia_json: Optional[bytes] = None,
    transcricao_csv: Optional[bytes] = None,
    sincronizado_csv: Optional[bytes] = None,
) -> int:
    """Salva um áudio com seus arquivos brutos. Retorna o ID gerado."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO audios
               (project_id, session_id, prosodia_json, transcricao_csv, sincronizado_csv)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, session_id, prosodia_json, transcricao_csv, sincronizado_csv),
        )
        return cur.lastrowid


def get_audios(project_id: int) -> List[Dict]:
    """Retorna todos os áudios de um projeto com status de KB e análise."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                a.*,
                (SELECT overall_status FROM quality_checks q
                 WHERE q.audio_id = a.id ORDER BY q.created_at DESC LIMIT 1) AS quality_status,
                (SELECT COUNT(*) FROM analyses an WHERE an.audio_id = a.id) AS n_analyses
            FROM audios a
            WHERE a.project_id = ?
            ORDER BY a.created_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_audios_for_interviews(project_id: int) -> List[Dict]:
    """
    Retorna áudios de um projeto com resumo da última verificação de qualidade
    e contagem de análises, pronto para tabela da página Entrevistas.
    """
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
                    WHERE q.audio_id = a.id
                    ORDER BY q.created_at DESC
                    LIMIT 1
                ) AS quality_status,
                (
                    SELECT q.checks_json
                    FROM quality_checks q
                    WHERE q.audio_id = a.id
                    ORDER BY q.created_at DESC
                    LIMIT 1
                ) AS checks_json,
                (
                    SELECT q.coverage_json
                    FROM quality_checks q
                    WHERE q.audio_id = a.id
                    ORDER BY q.created_at DESC
                    LIMIT 1
                ) AS coverage_json,
                (
                    SELECT COUNT(*)
                    FROM analyses an
                    WHERE an.audio_id = a.id
                ) AS n_analyses
            FROM audios a
            WHERE a.project_id = ?
            ORDER BY a.created_at DESC
            """,
            (project_id,),
        ).fetchall()

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

        result.append(d)

    return result


def get_audio(audio_id: int) -> Optional[Dict]:
    """Retorna um áudio pelo ID."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM audios WHERE id = ?", (audio_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_audio(audio_id: int) -> None:
    """Remove um áudio (cascata: análises, quality_checks)."""
    with _connect() as conn:
        conn.execute("DELETE FROM audios WHERE id = ?", (audio_id,))


def update_audio_openai_ids(
    audio_id: int,
    file_id_prosodia: Optional[str],
    file_id_transcricao: Optional[str],
) -> None:
    """Atualiza os IDs de arquivo OpenAI de um áudio."""
    with _connect() as conn:
        conn.execute(
            """UPDATE audios
               SET openai_file_id_prosodia=?, openai_file_id_transcricao=?
               WHERE id=?""",
            (file_id_prosodia, file_id_transcricao, audio_id),
        )


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
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO analyses (audio_id, model, analysis_text, citations)
               VALUES (?, ?, ?, ?)""",
            (audio_id, model, analysis_text, json.dumps(citations or [])),
        )
        return cur.lastrowid


def get_latest_analysis(audio_id: int) -> Optional[Dict]:
    """Retorna a análise mais recente de um áudio, ou None."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM analyses WHERE audio_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (audio_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d.get("citations") or "[]")
    return d


def get_analyses(audio_id: int) -> List[Dict]:
    """Retorna todas as análises de um áudio, mais recentes primeiro."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE audio_id = ? ORDER BY created_at DESC",
            (audio_id,),
        ).fetchall()
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
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO project_analyses (project_id, model, analysis_text, citations)
               VALUES (?, ?, ?, ?)""",
            (project_id, model, analysis_text, json.dumps(citations or [])),
        )
        return cur.lastrowid


def get_latest_project_analysis(project_id: int) -> Optional[Dict]:
    """Retorna a análise geral mais recente de um projeto, ou None."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM project_analyses WHERE project_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["citations"] = json.loads(d.get("citations") or "[]")
    return d


def get_project_analyses(project_id: int) -> List[Dict]:
    """Retorna histórico de análises gerais de um projeto, mais recentes primeiro."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_analyses WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()

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
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO quality_checks
               (audio_id, overall_status, checks_json, coverage_json)
               VALUES (?, ?, ?, ?)""",
            (audio_id, overall_status, json.dumps(checks), json.dumps(coverage)),
        )
        return cur.lastrowid


def get_latest_quality_check(audio_id: int) -> Optional[Dict]:
    """Retorna a verificação de qualidade mais recente de um áudio, ou None."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM quality_checks WHERE audio_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (audio_id,),
        ).fetchone()
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
