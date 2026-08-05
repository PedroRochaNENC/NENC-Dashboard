"""
Teste Sensorial DB — Camada SQLite para persistência de projetos, datasets
(EEG/Periféricos/PSD) e análises com IA do módulo de Teste Sensorial.

Banco: nenc-dashboard/prosodia.db (ou NENC_DB_PATH)
"""

import sqlite3
import json
import io
import zlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Any

import pandas as pd
from utils import auth

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "prosodia.db"


# ---------------------------------------------------------------------------
# Conexão e Auxiliares
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
    try:
        user = auth.current_user()
        if user is not None:
            return auth.active_organization_id(user)
    except Exception:
        pass
    try:
        return auth.active_organization_id()
    except Exception:
        return 1


def _dataframe_to_blob(df: Optional[pd.DataFrame]) -> Optional[bytes]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    try:
        json_str = df.to_json(orient="table", date_format="iso")
        return zlib.compress(json_str.encode("utf-8"))
    except Exception:
        return None


def _blob_to_dataframe(blob: Optional[bytes]) -> pd.DataFrame:
    if not blob:
        return pd.DataFrame()
    try:
        decompressed = zlib.decompress(blob).decode("utf-8")
        return pd.read_json(io.StringIO(decompressed), orient="table")
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Inicialização do Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Cria as tabelas se ainda não existirem."""
    auth.initialize_auth_schema()
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ts_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                produto_estimulo TEXT,
                historico TEXT,
                problemas TEXT,
                questions TEXT,
                briefing_text TEXT,
                vector_store_id TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS ts_datasets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES ts_projects(id) ON DELETE CASCADE,
                indicadores_blob BLOB,
                perifericos_blob BLOB,
                psd_results_blob BLOB,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS ts_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                project_id INTEGER NOT NULL REFERENCES ts_projects(id) ON DELETE CASCADE,
                model TEXT,
                analysis_text TEXT,
                citations TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_ts_projects_org ON ts_projects(organization_id);
            CREATE INDEX IF NOT EXISTS idx_ts_datasets_proj ON ts_datasets(project_id);
            CREATE INDEX IF NOT EXISTS idx_ts_analyses_proj ON ts_analyses(project_id);
            """
        )


# ---------------------------------------------------------------------------
# Projetos (ts_projects)
# ---------------------------------------------------------------------------

def get_projects() -> List[Dict[str, Any]]:
    """Lista todos os projetos da organização ativa ou todos para admin global."""
    init_db()
    org_id = _active_organization_id()
    is_admin = auth.is_current_user_platform_admin()
    with _connect() as conn:
        if not org_id or is_admin:
            rows = conn.execute(
                """
                SELECT p.*, o.name AS organization_name,
                       (SELECT COUNT(*) FROM ts_datasets d WHERE d.project_id = p.id) as total_datasets,
                       (SELECT COUNT(*) FROM ts_analyses a WHERE a.project_id = p.id) as total_analyses
                FROM ts_projects p
                LEFT JOIN organizations o ON o.id = p.organization_id
                ORDER BY p.updated_at DESC, p.id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.*, o.name AS organization_name,
                       (SELECT COUNT(*) FROM ts_datasets d WHERE d.project_id = p.id) as total_datasets,
                       (SELECT COUNT(*) FROM ts_analyses a WHERE a.project_id = p.id) as total_analyses
                FROM ts_projects p
                LEFT JOIN organizations o ON o.id = p.organization_id
                WHERE p.organization_id = ?
                ORDER BY p.updated_at DESC, p.id DESC
                """,
                (org_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_project(project_id: int) -> Optional[Dict[str, Any]]:
    """Retorna detalhes de um projeto específico."""
    init_db()
    org_id = _active_organization_id()
    is_admin = auth.is_current_user_platform_admin()
    with _connect() as conn:
        if not org_id or is_admin:
            row = conn.execute(
                "SELECT * FROM ts_projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM ts_projects WHERE id = ? AND organization_id = ?",
                (project_id, org_id),
            ).fetchone()
        return dict(row) if row else None


def create_project(
    name: str,
    produto_estimulo: str = "",
    historico: str = "",
    problemas: str = "",
    questions: str = "",
    briefing_text: str = "",
    vector_store_id: str = "",
) -> int:
    """Cria um novo projeto de Teste Sensorial."""
    org_id = _active_organization_id()
    if not org_id:
        user = auth.current_user()
        org_id = user.organization_id if user else 1
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO ts_projects (
                organization_id, name, produto_estimulo, historico, problemas,
                questions, briefing_text, vector_store_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                org_id,
                name.strip(),
                produto_estimulo.strip(),
                historico.strip(),
                problemas.strip(),
                questions.strip(),
                briefing_text.strip(),
                vector_store_id.strip(),
            ),
        )
        return cursor.lastrowid


def update_project(
    project_id: int,
    name: Optional[str] = None,
    produto_estimulo: Optional[str] = None,
    historico: Optional[str] = None,
    problemas: Optional[str] = None,
    questions: Optional[str] = None,
    briefing_text: Optional[str] = None,
    vector_store_id: Optional[str] = None,
) -> bool:
    """Atualiza dados de um projeto existente."""
    init_db()
    org_id = _active_organization_id()
    current = get_project(project_id)
    if not current:
        return False

    new_name = name if name is not None else current["name"]
    new_prod = produto_estimulo if produto_estimulo is not None else current["produto_estimulo"]
    new_hist = historico if historico is not None else current["historico"]
    new_prob = problemas if problemas is not None else current["problemas"]
    new_q = questions if questions is not None else current["questions"]
    new_btext = briefing_text if briefing_text is not None else current["briefing_text"]
    new_vs = vector_store_id if vector_store_id is not None else current["vector_store_id"]

    with _connect() as conn:
        conn.execute(
            """
            UPDATE ts_projects
            SET name = ?, produto_estimulo = ?, historico = ?, problemas = ?,
                questions = ?, briefing_text = ?, vector_store_id = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND organization_id = ?
            """,
            (
                new_name,
                new_prod,
                new_hist,
                new_prob,
                new_q,
                new_btext,
                new_vs,
                project_id,
                org_id,
            ),
        )
        return True


def delete_project(project_id: int) -> bool:
    """Exclui um projeto e todos os seus registros associados."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM ts_projects WHERE id = ? AND organization_id = ?",
            (project_id, org_id),
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Datasets (ts_datasets)
# ---------------------------------------------------------------------------

def save_dataset(
    project_id: int,
    indicadores: Optional[pd.DataFrame] = None,
    perifericos: Optional[pd.DataFrame] = None,
    psd_results: Optional[pd.DataFrame] = None,
) -> int:
    """Salva os DataFrames de EEG/Periféricos/PSD para um projeto."""
    init_db()
    org_id = _active_organization_id()
    
    ind_blob = _dataframe_to_blob(indicadores)
    per_blob = _dataframe_to_blob(perifericos)
    psd_blob = _dataframe_to_blob(psd_results)

    with _connect() as conn:
        conn.execute(
            "DELETE FROM ts_datasets WHERE project_id = ? AND organization_id = ?",
            (project_id, org_id),
        )
        cursor = conn.execute(
            """
            INSERT INTO ts_datasets (
                organization_id, project_id, indicadores_blob, perifericos_blob, psd_results_blob
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                org_id,
                project_id,
                ind_blob,
                per_blob,
                psd_blob,
            ),
        )
        conn.execute(
            "UPDATE ts_projects SET updated_at = datetime('now','localtime') WHERE id = ?",
            (project_id,),
        )
        return cursor.lastrowid


def get_dataset(project_id: int) -> Dict[str, pd.DataFrame]:
    """Recupera os DataFrames de EEG/Periféricos/PSD de um projeto."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT indicadores_blob, perifericos_blob, psd_results_blob
            FROM ts_datasets
            WHERE project_id = ? AND organization_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (project_id, org_id),
        ).fetchone()

        if not row:
            return {}

        res = {}
        ind = _blob_to_dataframe(row["indicadores_blob"])
        per = _blob_to_dataframe(row["perifericos_blob"])
        psd = _blob_to_dataframe(row["psd_results_blob"])

        if not ind.empty:
            res["indicadores"] = ind
        if not per.empty:
            res["perifericos"] = per
        if not psd.empty:
            res["psd_results"] = psd

        return res


# ---------------------------------------------------------------------------
# Análises de IA (ts_analyses)
# ---------------------------------------------------------------------------

def save_analysis(
    project_id: int,
    analysis_text: str,
    model: str = "gpt-4.1-mini",
    citations: Optional[List[str]] = None,
) -> int:
    """Salva um relatório de análise de IA gerado."""
    init_db()
    org_id = _active_organization_id()
    citations_json = json.dumps(citations or [], ensure_ascii=False)
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO ts_analyses (organization_id, project_id, model, analysis_text, citations)
            VALUES (?, ?, ?, ?, ?)
            """,
            (org_id, project_id, model, analysis_text.strip(), citations_json),
        )
        conn.execute(
            "UPDATE ts_projects SET updated_at = datetime('now','localtime') WHERE id = ?",
            (project_id,),
        )
        return cursor.lastrowid


def get_analyses(project_id: int) -> List[Dict[str, Any]]:
    """Retorna histórico de análises salvas para o projeto."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM ts_analyses
            WHERE project_id = ? AND organization_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (project_id, org_id),
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["citations"] = json.loads(item["citations"]) if item["citations"] else []
            except Exception:
                item["citations"] = []
            result.append(item)
        return result
