"""Organization-scoped persistence for module data and vector-store ownership."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, Iterable, Optional

import pandas as pd

from utils import auth


_VECTOR_STORE_MODULES = frozenset(("jornada_compra", "prosodia"))
_EXTERNAL_RESOURCE_TYPES = frozenset(
    (
        "whatsapp_contact",
        "whatsapp_campaign",
        "whatsapp_api_project",
        "whatsapp_audio",
        "whatsapp_job",
    )
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _access_context(module_key: str) -> tuple[auth.User, int]:
    user = auth.require_module(module_key)
    return user, auth.active_organization_id(user)


def _initialize_schema() -> None:
    auth.initialize_auth_schema()
    allowed_modules = ", ".join("'{}'".format(key) for key in auth.MODULE_KEYS)
    with auth.connection() as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS organization_module_state (
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                module_key TEXT NOT NULL CHECK (module_key IN ("""
            + allowed_modules
            + """)),
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (organization_id, module_key)
            );

            CREATE TABLE IF NOT EXISTS organization_vector_stores (
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                module_key TEXT NOT NULL CHECK (module_key IN ("""
            + allowed_modules
            + """)),
                vector_store_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (organization_id, module_key)
            );

            CREATE TABLE IF NOT EXISTS organization_external_resources (
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (resource_type, resource_id)
            );

            CREATE INDEX IF NOT EXISTS idx_external_resources_organization
                ON organization_external_resources(organization_id, resource_type);
            """
        )


def _encode_state(state: Dict[str, Any]) -> str:
    encoded: Dict[str, Dict[str, Any]] = {}
    for key, value in state.items():
        if isinstance(value, pd.DataFrame):
            encoded[key] = {
                "type": "dataframe",
                "value": value.to_json(orient="table", date_format="iso"),
            }
            continue
        try:
            json.dumps(value, ensure_ascii=True)
        except (TypeError, ValueError) as error:
            raise ValueError("O estado do modulo contem um valor nao persistivel: {}.".format(key)) from error
        encoded[key] = {"type": "json", "value": value}
    return json.dumps(encoded, ensure_ascii=True, sort_keys=True)


def _decode_state(serialized_state: str) -> Dict[str, Any]:
    try:
        encoded = json.loads(serialized_state)
    except (TypeError, ValueError) as error:
        raise ValueError("O estado persistido do modulo esta corrompido.") from error
    if not isinstance(encoded, dict):
        raise ValueError("O estado persistido do modulo esta corrompido.")

    state: Dict[str, Any] = {}
    for key, item in encoded.items():
        if not isinstance(item, dict) or "type" not in item:
            raise ValueError("O estado persistido do modulo esta corrompido.")
        if item["type"] == "dataframe":
            state[key] = pd.read_json(StringIO(item["value"]), orient="table")
        elif item["type"] == "json":
            state[key] = item.get("value")
        else:
            raise ValueError("O estado persistido do modulo usa um formato desconhecido.")
    return state


def load_module_state(module_key: str) -> Dict[str, Any]:
    """Load module state visible to the selected, validated organization."""

    _, organization_id = _access_context(module_key)
    _initialize_schema()
    with auth.connection() as database:
        row = database.execute(
            """
            SELECT state_json
            FROM organization_module_state
            WHERE organization_id = ? AND module_key = ?
            """,
            (organization_id, module_key),
        ).fetchone()
    auth.audit_business_access("module_state.read", "module_state", None, organization_id)
    return _decode_state(row["state_json"]) if row else {}


def save_module_state(module_key: str, state: Dict[str, Any]) -> None:
    """Persist module state only for the selected, validated organization."""

    if not isinstance(state, dict):
        raise ValueError("O estado do modulo precisa ser um dicionario.")
    _, organization_id = _access_context(module_key)
    serialized_state = _encode_state(state)
    _initialize_schema()
    with auth.connection() as database:
        database.execute(
            """
            INSERT INTO organization_module_state (
                organization_id, module_key, state_json, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(organization_id, module_key) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (organization_id, module_key, serialized_state, _timestamp()),
        )
    auth.audit_business_access("module_state.write", "module_state", None, organization_id)


def hydrate_session_state(module_key: str, session_keys: Iterable[str]) -> Dict[str, Any]:
    """Replace selected Streamlit keys with the active organization's persisted state."""

    import streamlit as st

    state = load_module_state(module_key)
    for session_key in session_keys:
        if session_key in state:
            st.session_state[session_key] = state[session_key]
        else:
            st.session_state.pop(session_key, None)
    return state


def save_session_state(module_key: str, session_keys: Iterable[str]) -> None:
    """Persist selected Streamlit keys for the active organization."""

    import streamlit as st

    save_module_state(
        module_key,
        {
            session_key: st.session_state[session_key]
            for session_key in session_keys
            if session_key in st.session_state
        },
    )


def _legacy_organization_id() -> Optional[int]:
    raw_organization_id = os.environ.get("NENC_LEGACY_ORGANIZATION_ID", "").strip()
    if not raw_organization_id:
        return None
    try:
        return int(raw_organization_id)
    except ValueError as error:
        raise ValueError("NENC_LEGACY_ORGANIZATION_ID deve ser um inteiro valido.") from error


def get_vector_store_id(
    module_key: str, legacy_environment_key: Optional[str] = None
) -> Optional[str]:
    """Return the vector store owned by the selected organization and module."""

    if module_key not in _VECTOR_STORE_MODULES:
        raise ValueError("Modulo sem suporte a base de conhecimento: {}.".format(module_key))
    _, organization_id = _access_context(module_key)
    _initialize_schema()
    with auth.connection() as database:
        row = database.execute(
            """
            SELECT vector_store_id
            FROM organization_vector_stores
            WHERE organization_id = ? AND module_key = ?
            """,
            (organization_id, module_key),
        ).fetchone()
        if row is not None:
            vector_store_id = row["vector_store_id"]
        else:
            legacy_value = (
                os.environ.get(legacy_environment_key, "").strip()
                if legacy_environment_key
                else ""
            )
            if legacy_value and _legacy_organization_id() == organization_id:
                now = _timestamp()
                database.execute(
                    """
                    INSERT INTO organization_vector_stores (
                        organization_id, module_key, vector_store_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (organization_id, module_key, legacy_value, now, now),
                )
                vector_store_id = legacy_value
            else:
                vector_store_id = None
    auth.audit_business_access("vector_store.read", "vector_store", None, organization_id)
    return vector_store_id


def save_vector_store_id(module_key: str, vector_store_id: str) -> None:
    """Assign a newly created vector store to the selected organization only."""

    if module_key not in _VECTOR_STORE_MODULES:
        raise ValueError("Modulo sem suporte a base de conhecimento: {}.".format(module_key))
    normalized_vector_store_id = (vector_store_id or "").strip()
    if not normalized_vector_store_id:
        raise ValueError("O identificador da base de conhecimento e obrigatorio.")
    _, organization_id = _access_context(module_key)
    _initialize_schema()
    now = _timestamp()
    with auth.connection() as database:
        database.execute(
            """
            INSERT INTO organization_vector_stores (
                organization_id, module_key, vector_store_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, module_key) DO UPDATE SET
                vector_store_id = excluded.vector_store_id,
                updated_at = excluded.updated_at
            """,
            (organization_id, module_key, normalized_vector_store_id, now, now),
        )
    auth.audit_business_access("vector_store.write", "vector_store", None, organization_id)


def _external_resource_context(resource_type: str, resource_id: Any) -> tuple[str, str, int]:
    if resource_type not in _EXTERNAL_RESOURCE_TYPES:
        raise ValueError("Tipo de recurso externo invalido: {}.".format(resource_type))
    normalized_resource_id = str(resource_id or "").strip()
    if not normalized_resource_id:
        raise ValueError("O identificador do recurso externo e obrigatorio.")
    _, organization_id = _access_context("prosodia")
    return resource_type, normalized_resource_id, organization_id


def claim_external_resource(
    resource_type: str,
    resource_id: Any,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    created: bool = False,
) -> None:
    """Verify or register an externally created resource for the active organization."""

    resource_type, normalized_resource_id, organization_id = _external_resource_context(
        resource_type, resource_id
    )
    try:
        metadata_json = (
            json.dumps(metadata, ensure_ascii=True, sort_keys=True)
            if metadata is not None
            else None
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Os metadados do recurso externo sao invalidos.") from error
    _initialize_schema()
    now = _timestamp()
    with auth.connection() as database:
        row = database.execute(
            """
            SELECT organization_id
            FROM organization_external_resources
            WHERE resource_type = ? AND resource_id = ?
            """,
            (resource_type, normalized_resource_id),
        ).fetchone()
        if row is not None and int(row["organization_id"]) != organization_id:
            raise auth.AuthorizationError(
                "Este recurso externo pertence a outra organizacao."
            )
        if row is None:
            if not created:
                raise auth.AuthorizationError(
                    "O recurso externo nao foi registrado para a organizacao ativa. "
                    "Recursos legados exigem migracao explicita."
                )
            database.execute(
                """
                INSERT INTO organization_external_resources (
                    resource_type, resource_id, organization_id, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    resource_type,
                    normalized_resource_id,
                    organization_id,
                    metadata_json or "{}",
                    now,
                    now,
                ),
            )
        elif metadata_json is not None:
            database.execute(
                """
                UPDATE organization_external_resources
                SET metadata_json = ?, updated_at = ?
                WHERE resource_type = ? AND resource_id = ? AND organization_id = ?
                """,
                (
                    metadata_json,
                    now,
                    resource_type,
                    normalized_resource_id,
                    organization_id,
                ),
            )
    auth.audit_business_access("whatsapp_resource.claim", resource_type, None, organization_id)


def migrate_legacy_external_resource(
    resource_type: str, resource_id: Any, metadata: Optional[Dict[str, Any]] = None
) -> None:
    """Adopt a legacy resource only for the organization declared by the server."""

    user = auth.require_admin(platform_only=True)
    _, _, organization_id = _external_resource_context(resource_type, resource_id)
    if _legacy_organization_id() != organization_id:
        raise auth.AuthorizationError(
            "NENC_LEGACY_ORGANIZATION_ID deve identificar a organizacao ativa."
        )
    claim_external_resource(resource_type, resource_id, metadata, created=True)
    auth.audit_business_access(
        "whatsapp_resource.legacy_migration", resource_type, None, organization_id
    )


def register_derived_external_resource(
    resource_type: str,
    resource_id: Any,
    parent_resource_type: str,
    parent_resource_id: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Register a resource discovered through an already owned external resource."""

    claim_external_resource(parent_resource_type, parent_resource_id)
    resource_metadata = dict(metadata or {})
    resource_metadata.update(
        {
            "parent_resource_type": parent_resource_type,
            "parent_resource_id": str(parent_resource_id),
        }
    )
    claim_external_resource(resource_type, resource_id, resource_metadata, created=True)


def list_external_resources(resource_type: str) -> list[Dict[str, Any]]:
    """List only externally hosted resources owned by the active organization."""

    if resource_type not in _EXTERNAL_RESOURCE_TYPES:
        raise ValueError("Tipo de recurso externo invalido: {}.".format(resource_type))
    _, organization_id = _access_context("prosodia")
    _initialize_schema()
    with auth.connection() as database:
        rows = database.execute(
            """
            SELECT resource_id, metadata_json
            FROM organization_external_resources
            WHERE organization_id = ? AND resource_type = ?
            ORDER BY created_at
            """,
            (organization_id, resource_type),
        ).fetchall()
    resources = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        resources.append({"id": row["resource_id"], "metadata": metadata})
    auth.audit_business_access("whatsapp_resource.list", resource_type, None, organization_id)
    return resources


def release_external_resource(resource_type: str, resource_id: Any) -> None:
    """Remove an owned resource mapping after the external resource is deleted."""

    resource_type, normalized_resource_id, organization_id = _external_resource_context(
        resource_type, resource_id
    )
    _initialize_schema()
    with auth.connection() as database:
        result = database.execute(
            """
            DELETE FROM organization_external_resources
            WHERE resource_type = ? AND resource_id = ? AND organization_id = ?
            """,
            (resource_type, normalized_resource_id, organization_id),
        )
    if not result.rowcount:
        raise auth.AuthorizationError("Este recurso externo nao pertence a organizacao ativa.")
    auth.audit_business_access("whatsapp_resource.release", resource_type, None, organization_id)