"""
Jornada DB — Camada SQLite para persistência de projetos, datasets, entrevistas
e análises com IA do módulo de Jornada de Compra.

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
                vector_store_id TEXT,
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

            CREATE INDEX IF NOT EXISTS idx_jc_projects_org ON jc_projects(organization_id);
            CREATE INDEX IF NOT EXISTS idx_jc_datasets_proj ON jc_datasets(project_id);
            CREATE INDEX IF NOT EXISTS idx_jc_interviews_proj ON jc_interviews(project_id);
            CREATE INDEX IF NOT EXISTS idx_jc_analyses_proj ON jc_analyses(project_id);
            """
        )


# ---------------------------------------------------------------------------
# Projetos (jc_projects)
# ---------------------------------------------------------------------------

def list_projects() -> List[Dict[str, Any]]:
    """Lista todos os projetos da organização ativa ou todos se 'Todas as Organizações' (0) estiver selecionada."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        if not org_id:
            rows = conn.execute(
                """
                SELECT p.*, o.name AS organization_name,
                       (SELECT COUNT(*) FROM jc_interviews i WHERE i.project_id = p.id) as total_interviews,
                       (SELECT COUNT(*) FROM jc_datasets d WHERE d.project_id = p.id) as total_datasets,
                       (SELECT COUNT(*) FROM jc_analyses a WHERE a.project_id = p.id) as total_analyses
                FROM jc_projects p
                LEFT JOIN organizations o ON o.id = p.organization_id
                ORDER BY p.updated_at DESC, p.id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.*, o.name AS organization_name,
                       (SELECT COUNT(*) FROM jc_interviews i WHERE i.project_id = p.id) as total_interviews,
                       (SELECT COUNT(*) FROM jc_datasets d WHERE d.project_id = p.id) as total_datasets,
                       (SELECT COUNT(*) FROM jc_analyses a WHERE a.project_id = p.id) as total_analyses
                FROM jc_projects p
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
                "SELECT * FROM jc_projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM jc_projects WHERE id = ? AND organization_id = ?",
                (project_id, org_id),
            ).fetchone()
        return dict(row) if row else None


def create_project(
    name: str,
    categoria: str = "",
    historico: str = "",
    problemas: str = "",
    questions: str = "",
    marcas: str = "",
    briefing_text: str = "",
    vector_store_id: str = "",
) -> int:
    """Cria um novo projeto de Jornada de Compra."""
    org_id = _active_organization_id()
    if not org_id:
        user = auth.current_user()
        org_id = user.organization_id if user else 1
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO jc_projects (
                organization_id, name, categoria, historico, problemas,
                questions, marcas, briefing_text, vector_store_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                org_id,
                name.strip(),
                categoria.strip(),
                historico.strip(),
                problemas.strip(),
                questions.strip(),
                marcas.strip(),
                briefing_text.strip(),
                vector_store_id.strip(),
            ),
        )
        return cursor.lastrowid


def update_project(
    project_id: int,
    name: Optional[str] = None,
    categoria: Optional[str] = None,
    historico: Optional[str] = None,
    problemas: Optional[str] = None,
    questions: Optional[str] = None,
    marcas: Optional[str] = None,
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
    new_cat = categoria if categoria is not None else current["categoria"]
    new_hist = historico if historico is not None else current["historico"]
    new_prob = problemas if problemas is not None else current["problemas"]
    new_q = questions if questions is not None else current["questions"]
    new_marcas = marcas if marcas is not None else current["marcas"]
    new_btext = briefing_text if briefing_text is not None else current["briefing_text"]
    new_vs = vector_store_id if vector_store_id is not None else current["vector_store_id"]

    with _connect() as conn:
        conn.execute(
            """
            UPDATE jc_projects
            SET name = ?, categoria = ?, historico = ?, problemas = ?,
                questions = ?, marcas = ?, briefing_text = ?, vector_store_id = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND organization_id = ?
            """,
            (
                new_name,
                new_cat,
                new_hist,
                new_prob,
                new_q,
                new_marcas,
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
            "DELETE FROM jc_projects WHERE id = ? AND organization_id = ?",
            (project_id, org_id),
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Datasets (jc_datasets)
# ---------------------------------------------------------------------------

def save_dataset(
    project_id: int,
    tabelas: Optional[pd.DataFrame] = None,
    por_marca: Optional[pd.DataFrame] = None,
    medias: Optional[pd.DataFrame] = None,
    visual_share: Optional[pd.DataFrame] = None,
) -> int:
    """Salva os DataFrames de Eye-Tracking para um projeto."""
    init_db()
    org_id = _active_organization_id()
    
    tabelas_blob = _dataframe_to_blob(tabelas)
    por_marca_blob = _dataframe_to_blob(por_marca)
    medias_blob = _dataframe_to_blob(medias)
    visual_share_blob = _dataframe_to_blob(visual_share)

    with _connect() as conn:
        # Remover dataset anterior se houver
        conn.execute(
            "DELETE FROM jc_datasets WHERE project_id = ? AND organization_id = ?",
            (project_id, org_id),
        )
        cursor = conn.execute(
            """
            INSERT INTO jc_datasets (
                organization_id, project_id, tabelas_blob, por_marca_blob,
                medias_blob, visual_share_blob
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                org_id,
                project_id,
                tabelas_blob,
                por_marca_blob,
                medias_blob,
                visual_share_blob,
            ),
        )
        conn.execute(
            "UPDATE jc_projects SET updated_at = datetime('now','localtime') WHERE id = ?",
            (project_id,),
        )
        return cursor.lastrowid


def get_dataset(project_id: int) -> Dict[str, pd.DataFrame]:
    """Recupera os DataFrames de Eye-Tracking de um projeto."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT tabelas_blob, por_marca_blob, medias_blob, visual_share_blob
            FROM jc_datasets
            WHERE project_id = ? AND organization_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (project_id, org_id),
        ).fetchone()

        if not row:
            return {}

        res = {}
        tabelas = _blob_to_dataframe(row["tabelas_blob"])
        por_marca = _blob_to_dataframe(row["por_marca_blob"])
        medias = _blob_to_dataframe(row["medias_blob"])
        visual_share = _blob_to_dataframe(row["visual_share_blob"])

        if not tabelas.empty:
            res["tabelas"] = tabelas
        if not por_marca.empty:
            res["por_marca"] = por_marca
        if not medias.empty:
            res["medias"] = medias
        if not visual_share.empty:
            res["visual_share"] = visual_share

        return res


# ---------------------------------------------------------------------------
# Entrevistas Qualitativas (jc_interviews)
# ---------------------------------------------------------------------------

def save_interview(
    project_id: int,
    titulo: str,
    texto: str,
    participante_id: str = "",
) -> int:
    """Salva uma entrevista qualitativa vinculada ao projeto."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO jc_interviews (organization_id, project_id, titulo, participante_id, texto)
            VALUES (?, ?, ?, ?, ?)
            """,
            (org_id, project_id, titulo.strip(), participante_id.strip(), texto.strip()),
        )
        conn.execute(
            "UPDATE jc_projects SET updated_at = datetime('now','localtime') WHERE id = ?",
            (project_id,),
        )
        return cursor.lastrowid


def get_interviews(project_id: int) -> List[Dict[str, Any]]:
    """Retorna lista de entrevistas salvas para um projeto."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jc_interviews
            WHERE project_id = ? AND organization_id = ?
            ORDER BY id ASC
            """,
            (project_id, org_id),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_interview(interview_id: int) -> bool:
    """Exclui uma entrevista."""
    init_db()
    org_id = _active_organization_id()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM jc_interviews WHERE id = ? AND organization_id = ?",
            (interview_id, org_id),
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Análises de IA (jc_analyses)
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
            INSERT INTO jc_analyses (organization_id, project_id, model, analysis_text, citations)
            VALUES (?, ?, ?, ?, ?)
            """,
            (org_id, project_id, model, analysis_text.strip(), citations_json),
        )
        conn.execute(
            "UPDATE jc_projects SET updated_at = datetime('now','localtime') WHERE id = ?",
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
            SELECT * FROM jc_analyses
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
