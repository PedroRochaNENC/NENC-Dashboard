"""Local authentication, organization isolation, and page authorization.

The functions in this module are the server-side authority for users, sessions,
roles, and module access. Streamlit pages must call the guard functions at their
entry point; hiding a page from navigation is not authorization.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple


MODULE_KEYS: Tuple[str, ...] = (
    "teste_sensorial",
    "jornada_compra",
    "prosodia",
)
MODULE_LABELS = {
    "teste_sensorial": "Teste Sensorial",
    "jornada_compra": "Jornada de Compra",
    "prosodia": "NencLex",
}

PASSWORD_MIN_LENGTH = 12
SESSION_HOURS = 12
SESSION_STATE_KEY = "_nenc_auth_session_token"
ACTIVE_ORGANIZATION_STATE_KEY = "_nenc_active_organization_id"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SECRET_METADATA_KEYS = {
    "password",
    "password_hash",
    "hash",
    "token",
    "session_token",
    "secret",
    "api_key",
    "key",
}


class AuthenticationError(ValueError):
    """Credentials or the account state do not permit a login."""


class AuthorizationError(PermissionError):
    """The signed-in identity cannot perform the requested server-side action."""


class BootstrapConfigurationError(RuntimeError):
    """Initial administrator configuration is missing one or more required values."""


@dataclass(frozen=True)
class User:
    """Identity data safe to use in a Streamlit page."""

    id: int
    name: str
    email: str
    phone: str
    organization_id: int
    organization_name: str
    is_organization_admin: bool
    is_platform_admin: bool
    is_active: bool
    modules: Tuple[str, ...]

    @property
    def is_admin(self) -> bool:
        return self.is_organization_admin or self.is_platform_admin


@dataclass(frozen=True)
class Organization:
    id: int
    name: str
    is_active: bool
    whatsapp_numbers: str = ""


def _database_path(database_path: Optional[os.PathLike] = None) -> Path:
    configured_path = os.environ.get("NENC_DB_PATH")
    if database_path is not None:
        return Path(database_path)
    if configured_path:
        return Path(configured_path).expanduser()
    return Path(__file__).resolve().parent.parent / "prosodia.db"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Optional[datetime] = None) -> str:
    return (value or _now()).replace(microsecond=0).isoformat()


@contextmanager
def connection(database_path: Optional[os.PathLike] = None) -> Iterator[sqlite3.Connection]:
    """Open a connection with foreign-key enforcement and transactional cleanup."""

    path = _database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(str(path))
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    try:
        yield database
        database.commit()
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()


def initialize_auth_schema(database_path: Optional[os.PathLike] = None) -> None:
    """Create idempotent identity, access, session, and audit tables."""

    allowed_modules = ", ".join("'{}'".format(key) for key in MODULE_KEYS)
    with connection(database_path) as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                phone TEXT NOT NULL,
                organization_id INTEGER NOT NULL REFERENCES organizations(id),
                password_hash TEXT NOT NULL,
                is_organization_admin INTEGER NOT NULL DEFAULT 0
                    CHECK (is_organization_admin IN (0, 1)),
                is_platform_admin INTEGER NOT NULL DEFAULT 0
                    CHECK (is_platform_admin IN (0, 1)),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS user_module_access (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                module_key TEXT NOT NULL CHECK (module_key IN ("""
            + allowed_modules
            + """)),
                PRIMARY KEY (user_id, module_key)
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                ON auth_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry
                ON auth_sessions(expires_at);

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER REFERENCES organizations(id),
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_audit_log_organization
                ON audit_log(organization_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_log_actor
                ON audit_log(actor_user_id, created_at);
            """
        )
        org_cols = {row["name"] for row in database.execute("PRAGMA table_info(organizations)").fetchall()}
        if "whatsapp_numbers" not in org_cols:
            database.execute("ALTER TABLE organizations ADD COLUMN whatsapp_numbers TEXT DEFAULT ''")


def normalize_email(email: str) -> str:
    normalized = (email or "").strip().casefold()
    if not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Informe um e-mail valido.")
    return normalized


def _required_text(value: str, label: str) -> str:
    normalized = " ".join((value or "").strip().split())
    if not normalized:
        raise ValueError("{} e obrigatorio.".format(label))
    return normalized


def validate_password(password: str) -> None:
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise ValueError(
            "A senha deve conter pelo menos {} caracteres.".format(PASSWORD_MIN_LENGTH)
        )
    if len(password) > 1024:
        raise ValueError("A senha excede o tamanho permitido.")


def hash_password(password: str) -> str:
    """Create a salted, memory-hard scrypt password hash."""

    validate_password(password)
    n_value, r_value, p_value = 2**14, 8, 1
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n_value,
        r=r_value,
        p=p_value,
        maxmem=128 * 1024 * 1024,
        dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        n_value,
        r_value,
        p_value,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a hash while using a constant-time digest comparison."""

    try:
        algorithm, n_value, r_value, p_value, encoded_salt, encoded_digest = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        candidate_digest = hashlib.scrypt(
            (password or "").encode("utf-8"),
            salt=salt,
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            maxmem=128 * 1024 * 1024,
            dklen=len(expected_digest),
        )
        return hmac.compare_digest(candidate_digest, expected_digest)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return False


def _row_to_user(database: sqlite3.Connection, row: sqlite3.Row) -> User:
    modules = tuple(
        module_row["module_key"]
        for module_row in database.execute(
            """
            SELECT module_key FROM user_module_access
            WHERE user_id = ?
            ORDER BY module_key
            """,
            (row["id"],),
        )
    )
    return User(
        id=int(row["id"]),
        name=row["name"],
        email=row["email"],
        phone=row["phone"],
        organization_id=int(row["organization_id"]),
        organization_name=row["organization_name"],
        is_organization_admin=bool(row["is_organization_admin"]),
        is_platform_admin=bool(row["is_platform_admin"]),
        is_active=bool(row["is_active"]),
        modules=modules,
    )


def _user_by_clause(
    database: sqlite3.Connection, where_clause: str, values: Sequence[Any]
) -> Optional[User]:
    row = database.execute(
        """
        SELECT users.*, organizations.name AS organization_name,
               organizations.is_active AS organization_is_active
        FROM users
        JOIN organizations ON organizations.id = users.organization_id
        WHERE """
        + where_clause,
        tuple(values),
    ).fetchone()
    return _row_to_user(database, row) if row else None


def get_user(user_id: int, database_path: Optional[os.PathLike] = None) -> Optional[User]:
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        return _user_by_clause(database, "users.id = ?", (user_id,))


def get_user_by_email(email: str, database_path: Optional[os.PathLike] = None) -> Optional[User]:
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        return _user_by_clause(database, "users.email = ?", (normalize_email(email),))


def _safe_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    if not metadata:
        return None
    safe = {
        key: value
        for key, value in metadata.items()
        if key.casefold() not in _SECRET_METADATA_KEYS
    }
    return json.dumps(safe, ensure_ascii=True, sort_keys=True) if safe else None


def _audit(
    database: sqlite3.Connection,
    organization_id: Optional[int],
    actor_user_id: Optional[int],
    action: str,
    target_type: str,
    target_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    database.execute(
        """
        INSERT INTO audit_log (
            organization_id, actor_user_id, action, target_type, target_id,
            metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            actor_user_id,
            action,
            target_type,
            str(target_id) if target_id is not None else None,
            _safe_metadata(metadata),
            _timestamp(),
        ),
    )


def _organization_row(database: sqlite3.Connection, organization_id: int) -> sqlite3.Row:
    organization = database.execute(
        "SELECT * FROM organizations WHERE id = ?", (organization_id,)
    ).fetchone()
    if organization is None:
        raise ValueError("Organizacao nao encontrada.")
    return organization


def _trusted_actor(database: sqlite3.Connection, actor: User) -> User:
    trusted_actor = _user_by_clause(database, "users.id = ?", (actor.id,))
    if trusted_actor is None or not trusted_actor.is_active:
        raise AuthorizationError("A conta administrativa nao esta ativa.")
    return trusted_actor


def _validate_modules(module_keys: Sequence[str]) -> Tuple[str, ...]:
    normalized = tuple(sorted(set(module_keys)))
    invalid = set(normalized).difference(MODULE_KEYS)
    if invalid:
        raise ValueError("Modulo invalido: {}.".format(", ".join(sorted(invalid))))
    return normalized


def _active_admin_count(
    database: sqlite3.Connection, organization_id: int, exclude_user_id: Optional[int] = None
) -> int:
    query = """
        SELECT COUNT(*) AS total FROM users
        WHERE organization_id = ? AND is_active = 1 AND is_organization_admin = 1
    """
    values: List[Any] = [organization_id]
    if exclude_user_id is not None:
        query += " AND id != ?"
        values.append(exclude_user_id)
    return int(database.execute(query, tuple(values)).fetchone()["total"])


def _active_platform_admin_count(
    database: sqlite3.Connection, exclude_user_id: Optional[int] = None
) -> int:
    query = """
        SELECT COUNT(*) AS total FROM users
        WHERE is_active = 1 AND is_platform_admin = 1
    """
    values: List[Any] = []
    if exclude_user_id is not None:
        query += " AND id != ?"
        values.append(exclude_user_id)
    return int(database.execute(query, tuple(values)).fetchone()["total"])


def _revoke_user_sessions(database: sqlite3.Connection, user_id: int) -> None:
    database.execute(
        """
        UPDATE auth_sessions SET revoked_at = ?
        WHERE user_id = ? AND revoked_at IS NULL
        """,
        (_timestamp(), user_id),
    )


def create_organization(
    name: str,
    actor: Optional[User] = None,
    database_path: Optional[os.PathLike] = None,
    _bootstrap: bool = False,
) -> Organization:
    """Create an organization, restricted to platform administrators after bootstrap."""

    initialize_auth_schema(database_path)
    organization_name = _required_text(name, "Organizacao")
    with connection(database_path) as database:
        if _bootstrap:
            if database.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]:
                raise AuthorizationError("O bootstrap inicial ja foi concluido.")
            actor_id = None
        else:
            if actor is None:
                raise AuthorizationError("Esta acao exige um administrador global.")
            trusted_actor = _trusted_actor(database, actor)
            if not trusted_actor.is_platform_admin:
                raise AuthorizationError("Somente administradores globais criam organizacoes.")
            actor_id = trusted_actor.id
        try:
            cursor = database.execute(
                """
                INSERT INTO organizations (name, is_active, created_at, updated_at)
                VALUES (?, 1, ?, ?)
                """,
                (organization_name, _timestamp(), _timestamp()),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("Ja existe uma organizacao com esse nome.") from error
        organization_id = int(cursor.lastrowid)
        _audit(
            database,
            organization_id,
            actor_id,
            "organization.created",
            "organization",
            organization_id,
        )
        return Organization(organization_id, organization_name, True)


def list_organizations(
    actor: User,
    include_inactive: bool = False,
    database_path: Optional[os.PathLike] = None,
) -> List[Organization]:
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        trusted_actor = _trusted_actor(database, actor)
        if not trusted_actor.is_platform_admin:
            row = database.execute(
                "SELECT id, name, is_active, whatsapp_numbers FROM organizations WHERE id = ?",
                (trusted_actor.organization_id,),
            ).fetchone()
            if row:
                return [
                    Organization(
                        int(row["id"]), row["name"], bool(row["is_active"]), row["whatsapp_numbers"] or ""
                    )
                ]
            return [
                Organization(
                    trusted_actor.organization_id,
                    trusted_actor.organization_name,
                    True,
                    "",
                )
            ]
        query = "SELECT id, name, is_active, whatsapp_numbers FROM organizations"
        if not include_inactive:
            query += " WHERE is_active = 1"
        query += " ORDER BY name COLLATE NOCASE"
        return [
            Organization(int(row["id"]), row["name"], bool(row["is_active"]), row["whatsapp_numbers"] or "")
            for row in database.execute(query)
        ]


def update_organization(
    actor: User,
    organization_id: int,
    name: Optional[str] = None,
    whatsapp_numbers: Optional[str] = None,
    is_active: Optional[bool] = None,
    database_path: Optional[os.PathLike] = None,
) -> None:
    """Atualiza dados da organização (nome, status ativo ou números de WhatsApp cadastrados)."""
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        trusted_actor = _trusted_actor(database, actor)
        if not trusted_actor.is_platform_admin and (
            not trusted_actor.is_organization_admin or trusted_actor.organization_id != organization_id
        ):
            raise AuthorizationError("Voce nao tem permissao para editar esta organizacao.")
        _organization_row(database, organization_id)

        updates = []
        params = []
        if name is not None and trusted_actor.is_platform_admin:
            updates.append("name = ?")
            params.append(_required_text(name, "Nome da organizacao"))
        if whatsapp_numbers is not None:
            updates.append("whatsapp_numbers = ?")
            params.append(whatsapp_numbers.strip())
        if is_active is not None and trusted_actor.is_platform_admin:
            updates.append("is_active = ?")
            params.append(int(is_active))

        if updates:
            updates.append("updated_at = ?")
            params.append(_timestamp())
            params.append(organization_id)
            query = "UPDATE organizations SET {} WHERE id = ?".format(", ".join(updates))
            try:
                database.execute(query, params)
            except sqlite3.IntegrityError as error:
                raise ValueError("Ja existe uma organizacao com esse nome.") from error
            _audit(
                database,
                organization_id,
                trusted_actor.id,
                "organization.updated",
                "organization",
                organization_id,
            )


def get_organization_whatsapp_numbers(
    organization_id: int,
    database_path: Optional[os.PathLike] = None,
) -> List[str]:
    """Retorna a lista de números de WhatsApp da organização cadastrados na administração."""
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        row = database.execute(
            "SELECT whatsapp_numbers FROM organizations WHERE id = ?",
            (organization_id,),
        ).fetchone()
        if not row or not row["whatsapp_numbers"]:
            return []
        raw_numbers = str(row["whatsapp_numbers"]).replace("\n", ",").split(",")
        cleaned = []
        for n in raw_numbers:
            digits = "".join(filter(str.isdigit, n.strip()))
            if digits and digits not in cleaned:
                cleaned.append(digits)
        return cleaned


def set_organization_active(
    actor: User,
    organization_id: int,
    is_active: bool,
    database_path: Optional[os.PathLike] = None,
) -> None:
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        trusted_actor = _trusted_actor(database, actor)
        if not trusted_actor.is_platform_admin:
            raise AuthorizationError("Somente administradores globais gerenciam organizacoes.")
        _organization_row(database, organization_id)
        database.execute(
            "UPDATE organizations SET is_active = ?, updated_at = ? WHERE id = ?",
            (int(is_active), _timestamp(), organization_id),
        )
        if not is_active:
            database.execute(
                """
                UPDATE auth_sessions SET revoked_at = ?
                WHERE user_id IN (SELECT id FROM users WHERE organization_id = ?)
                  AND revoked_at IS NULL
                """,
                (_timestamp(), organization_id),
            )
        _audit(
            database,
            organization_id,
            trusted_actor.id,
            "organization.{}".format("activated" if is_active else "deactivated"),
            "organization",
            organization_id,
        )


def _assert_user_management_scope(
    actor: User,
    target_organization_id: int,
    is_platform_admin: bool = False,
) -> None:
    if not actor.is_admin:
        raise AuthorizationError("Esta acao exige permissao de administrador.")
    if not actor.is_platform_admin and target_organization_id != actor.organization_id:
        raise AuthorizationError("Administradores so gerenciam a propria organizacao.")
    if is_platform_admin and not actor.is_platform_admin:
        raise AuthorizationError("Somente administradores globais podem conceder esse papel.")


def create_user(
    name: str,
    email: str,
    phone: str,
    organization_id: int,
    password: str,
    module_keys: Sequence[str] = (),
    is_organization_admin: bool = False,
    is_platform_admin: bool = False,
    actor: Optional[User] = None,
    database_path: Optional[os.PathLike] = None,
    _bootstrap: bool = False,
) -> User:
    """Create a user after checking the actor's current database role."""

    initialize_auth_schema(database_path)
    normalized_name = _required_text(name, "Nome")
    normalized_email = normalize_email(email)
    normalized_phone = _required_text(phone, "Telefone")
    normalized_modules = _validate_modules(module_keys)
    password_hash = hash_password(password)
    with connection(database_path) as database:
        organization = _organization_row(database, organization_id)
        if not bool(organization["is_active"]):
            raise ValueError("A organizacao esta inativa.")
        if _bootstrap:
            if database.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]:
                raise AuthorizationError("O bootstrap inicial ja foi concluido.")
            actor_id = None
        else:
            if actor is None:
                raise AuthorizationError("Esta acao exige um administrador autenticado.")
            trusted_actor = _trusted_actor(database, actor)
            _assert_user_management_scope(
                trusted_actor, organization_id, is_platform_admin=is_platform_admin
            )
            actor_id = trusted_actor.id
        try:
            cursor = database.execute(
                """
                INSERT INTO users (
                    name, email, phone, organization_id, password_hash,
                    is_organization_admin, is_platform_admin, is_active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    normalized_name,
                    normalized_email,
                    normalized_phone,
                    organization_id,
                    password_hash,
                    int(is_organization_admin),
                    int(is_platform_admin),
                    _timestamp(),
                    _timestamp(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("Ja existe uma conta com esse e-mail.") from error
        user_id = int(cursor.lastrowid)
        database.executemany(
            "INSERT INTO user_module_access (user_id, module_key) VALUES (?, ?)",
            [(user_id, key) for key in normalized_modules],
        )
        _audit(
            database,
            organization_id,
            actor_id,
            "user.created",
            "user",
            user_id,
            {
                "module_keys": list(normalized_modules),
                "is_organization_admin": bool(is_organization_admin),
                "is_platform_admin": bool(is_platform_admin),
            },
        )
        created = _user_by_clause(database, "users.id = ?", (user_id,))
        if created is None:
            raise RuntimeError("Nao foi possivel carregar o usuario criado.")
        return created


def list_users(
    actor: User,
    organization_id: Optional[int] = None,
    search: str = "",
    include_inactive: bool = True,
    database_path: Optional[os.PathLike] = None,
) -> List[User]:
    """Return only the organization-scoped users visible to the supplied actor."""

    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        trusted_actor = _trusted_actor(database, actor)
        target_organization_id = organization_id or trusted_actor.organization_id
        _assert_user_management_scope(trusted_actor, target_organization_id)
        clauses = ["users.organization_id = ?"]
        values: List[Any] = [target_organization_id]
        if not include_inactive:
            clauses.append("users.is_active = 1")
        if search.strip():
            clauses.append("(users.name LIKE ? OR users.email LIKE ?)")
            pattern = "%{}%".format(search.strip())
            values.extend((pattern, pattern))
        rows = database.execute(
            """
            SELECT users.*, organizations.name AS organization_name,
                   organizations.is_active AS organization_is_active
            FROM users
            JOIN organizations ON organizations.id = users.organization_id
            WHERE """
            + " AND ".join(clauses)
            + " ORDER BY users.name COLLATE NOCASE",
            tuple(values),
        ).fetchall()
        return [_row_to_user(database, row) for row in rows]


def update_user(
    actor: User,
    user_id: int,
    *,
    name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    organization_id: Optional[int] = None,
    password: Optional[str] = None,
    is_active: Optional[bool] = None,
    is_organization_admin: Optional[bool] = None,
    is_platform_admin: Optional[bool] = None,
    module_keys: Optional[Sequence[str]] = None,
    database_path: Optional[os.PathLike] = None,
) -> User:
    """Edit account data and revoke all sessions when access is reduced."""

    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        trusted_actor = _trusted_actor(database, actor)
        target = _user_by_clause(database, "users.id = ?", (user_id,))
        if target is None:
            raise ValueError("Usuario nao encontrado.")
        _assert_user_management_scope(
            trusted_actor, target.organization_id, is_platform_admin=target.is_platform_admin
        )
        target_organization_id = organization_id or target.organization_id
        _assert_user_management_scope(
            trusted_actor, target_organization_id, is_platform_admin=bool(is_platform_admin)
        )
        _organization_row(database, target_organization_id)

        desired_active = target.is_active if is_active is None else bool(is_active)
        desired_org_admin = (
            target.is_organization_admin
            if is_organization_admin is None
            else bool(is_organization_admin)
        )
        desired_platform_admin = (
            target.is_platform_admin
            if is_platform_admin is None
            else bool(is_platform_admin)
        )
        if desired_platform_admin and not trusted_actor.is_platform_admin:
            raise AuthorizationError("Somente administradores globais podem conceder esse papel.")
        if (
            target.is_organization_admin
            and (not desired_active or not desired_org_admin or target_organization_id != target.organization_id)
            and _active_admin_count(database, target.organization_id, target.id) == 0
        ):
            raise ValueError("A organizacao precisa manter um administrador ativo.")
        if (
            target.is_platform_admin
            and (not desired_active or not desired_platform_admin)
            and _active_platform_admin_count(database, target.id) == 0
        ):
            raise ValueError("A plataforma precisa manter um administrador global ativo.")

        updates: Dict[str, Any] = {}
        if name is not None:
            updates["name"] = _required_text(name, "Nome")
        if email is not None:
            updates["email"] = normalize_email(email)
        if phone is not None:
            updates["phone"] = _required_text(phone, "Telefone")
        if organization_id is not None:
            updates["organization_id"] = target_organization_id
        if password is not None:
            updates["password_hash"] = hash_password(password)
        if is_active is not None:
            updates["is_active"] = int(desired_active)
        if is_organization_admin is not None:
            updates["is_organization_admin"] = int(desired_org_admin)
        if is_platform_admin is not None:
            updates["is_platform_admin"] = int(desired_platform_admin)
        if updates:
            updates["updated_at"] = _timestamp()
            assignments = ", ".join("{} = ?".format(column) for column in updates)
            try:
                database.execute(
                    "UPDATE users SET {} WHERE id = ?".format(assignments),
                    tuple(updates.values()) + (target.id,),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("Ja existe uma conta com esse e-mail.") from error

        access_reduced = False
        if module_keys is not None:
            desired_modules = _validate_modules(module_keys)
            access_reduced = not set(target.modules).issubset(set(desired_modules))
            database.execute("DELETE FROM user_module_access WHERE user_id = ?", (target.id,))
            database.executemany(
                "INSERT INTO user_module_access (user_id, module_key) VALUES (?, ?)",
                [(target.id, key) for key in desired_modules],
            )
        revoke_sessions = (
            password is not None
            or is_active is False
            or access_reduced
            or organization_id is not None
            or is_organization_admin is not None
            or is_platform_admin is not None
        )
        if revoke_sessions:
            _revoke_user_sessions(database, target.id)
        _audit(
            database,
            target_organization_id,
            trusted_actor.id,
            "user.updated",
            "user",
            target.id,
            {
                "changed_fields": sorted(
                    key for key in updates if key != "password_hash"
                ) + (["module_keys"] if module_keys is not None else []),
                "sessions_revoked": revoke_sessions,
            },
        )
        updated = _user_by_clause(database, "users.id = ?", (target.id,))
        if updated is None:
            raise RuntimeError("Nao foi possivel carregar o usuario atualizado.")
        return updated


def delete_user(
    actor: User, user_id: int, database_path: Optional[os.PathLike] = None
) -> None:
    """Permanently remove an account while preserving the last-admin protections."""

    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        trusted_actor = _trusted_actor(database, actor)
        target = _user_by_clause(database, "users.id = ?", (user_id,))
        if target is None:
            raise ValueError("Usuario nao encontrado.")
        _assert_user_management_scope(
            trusted_actor, target.organization_id, is_platform_admin=target.is_platform_admin
        )
        if target.is_organization_admin and _active_admin_count(
            database, target.organization_id, target.id
        ) == 0:
            raise ValueError("A organizacao precisa manter um administrador ativo.")
        if target.is_platform_admin and _active_platform_admin_count(database, target.id) == 0:
            raise ValueError("A plataforma precisa manter um administrador global ativo.")
        _audit(
            database,
            target.organization_id,
            trusted_actor.id,
            "user.deleted",
            "user",
            target.id,
        )
        database.execute("DELETE FROM users WHERE id = ?", (target.id,))


def create_session(
    user_id: int,
    database_path: Optional[os.PathLike] = None,
    duration: timedelta = timedelta(hours=SESSION_HOURS),
) -> str:
    initialize_auth_schema(database_path)
    session_token = secrets.token_urlsafe(48)
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    with connection(database_path) as database:
        database.execute(
            """
            INSERT INTO auth_sessions (session_hash, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_hash, user_id, _timestamp(_now() + duration), _timestamp()),
        )
    return session_token


def user_for_session(
    session_token: Optional[str], database_path: Optional[os.PathLike] = None
) -> Optional[User]:
    """Check session expiry, revocation, user activity, and organization activity."""

    if not session_token:
        return None
    initialize_auth_schema(database_path)
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    with connection(database_path) as database:
        row = database.execute(
            """
            SELECT users.*, organizations.name AS organization_name,
                   organizations.is_active AS organization_is_active
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            JOIN organizations ON organizations.id = users.organization_id
            WHERE auth_sessions.session_hash = ?
              AND auth_sessions.revoked_at IS NULL
              AND auth_sessions.expires_at > ?
              AND users.is_active = 1
              AND organizations.is_active = 1
            """,
            (session_hash, _timestamp()),
        ).fetchone()
        return _row_to_user(database, row) if row else None


def revoke_session(
    session_token: Optional[str], database_path: Optional[os.PathLike] = None
) -> None:
    if not session_token:
        return
    initialize_auth_schema(database_path)
    session_hash = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    with connection(database_path) as database:
        session = database.execute(
            "SELECT user_id FROM auth_sessions WHERE session_hash = ?", (session_hash,)
        ).fetchone()
        database.execute(
            """
            UPDATE auth_sessions SET revoked_at = ?
            WHERE session_hash = ? AND revoked_at IS NULL
            """,
            (_timestamp(), session_hash),
        )
        if session is not None:
            user = _user_by_clause(database, "users.id = ?", (session["user_id"],))
            _audit(
                database,
                user.organization_id if user else None,
                session["user_id"],
                "session.revoked",
                "session",
            )


def authenticate(
    email: str, password: str, database_path: Optional[os.PathLike] = None
) -> Tuple[User, str]:
    """Validate credentials and create a new opaque, server-revocable session."""

    initialize_auth_schema(database_path)
    try:
        normalized_email = normalize_email(email)
    except ValueError as error:
        raise AuthenticationError("E-mail ou senha invalidos.") from error
    with connection(database_path) as database:
        row = database.execute(
            """
            SELECT users.*, organizations.name AS organization_name,
                   organizations.is_active AS organization_is_active
            FROM users
            JOIN organizations ON organizations.id = users.organization_id
            WHERE users.email = ?
            """,
            (normalized_email,),
        ).fetchone()
        valid = (
            row is not None
            and bool(row["is_active"])
            and bool(row["organization_is_active"])
            and verify_password(password, row["password_hash"])
        )
        if not valid:
            if row is not None:
                _audit(
                    database,
                    row["organization_id"],
                    row["id"],
                    "login.failed",
                    "user",
                    row["id"],
                )
            raise AuthenticationError("E-mail ou senha invalidos, ou conta inativa.")
        user = _row_to_user(database, row)
        database.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (_timestamp(), _timestamp(), user.id),
        )
        _audit(
            database,
            user.organization_id,
            user.id,
            "login.succeeded",
            "user",
            user.id,
        )
    return user, create_session(user.id, database_path=database_path)


def can_access_module(user: User, module_key: str) -> bool:
    return module_key in MODULE_KEYS and (user.is_admin or module_key in user.modules)


def _streamlit() -> Any:
    import streamlit as st

    return st


def current_user(database_path: Optional[os.PathLike] = None) -> Optional[User]:
    """Read and validate the opaque session token on every Streamlit rerun."""

    st = _streamlit()
    user = user_for_session(st.session_state.get(SESSION_STATE_KEY), database_path)
    if user is None:
        st.session_state.pop(SESSION_STATE_KEY, None)
        st.session_state.pop(ACTIVE_ORGANIZATION_STATE_KEY, None)
    return user


def login(email: str, password: str, database_path: Optional[os.PathLike] = None) -> User:
    user, session_token = authenticate(email, password, database_path)
    _streamlit().session_state[SESSION_STATE_KEY] = session_token
    return user


def logout(database_path: Optional[os.PathLike] = None) -> None:
    st = _streamlit()
    session_token = st.session_state.pop(SESSION_STATE_KEY, None)
    st.session_state.pop(ACTIVE_ORGANIZATION_STATE_KEY, None)
    revoke_session(session_token, database_path)


def require_login(database_path: Optional[os.PathLike] = None) -> User:
    user = current_user(database_path)
    if user is None:
        st = _streamlit()
        st.error("Faca login para acessar esta pagina.")
        st.stop()
    return user


def require_module(module_key: str, database_path: Optional[os.PathLike] = None) -> User:
    user = require_login(database_path)
    if not can_access_module(user, module_key):
        st = _streamlit()
        st.error("Voce nao tem acesso a este modulo.")
        st.stop()
    return user


def require_admin(
    platform_only: bool = False, database_path: Optional[os.PathLike] = None
) -> User:
    user = require_login(database_path)
    if (platform_only and not user.is_platform_admin) or (
        not platform_only and not user.is_admin
    ):
        st = _streamlit()
        st.error("Esta pagina e restrita a administradores.")
        st.stop()
    return user


def active_organization_id(
    user: Optional[User] = None, database_path: Optional[os.PathLike] = None
) -> int:
    """Return a server-validated organization target for business-data operations.
    Returns 0 when 'Todas as Organizações' is selected by a platform admin.
    """

    st = _streamlit()
    user = user or require_login(database_path)
    selected_id = st.session_state.get(ACTIVE_ORGANIZATION_STATE_KEY)
    if not user.is_platform_admin:
        return user.organization_id
    if selected_id == 0:
        return 0
    try:
        organization_id = int(selected_id) if selected_id is not None else user.organization_id
    except (TypeError, ValueError):
        organization_id = user.organization_id
    if organization_id == 0:
        return 0
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        organization = _organization_row(database, organization_id)
        if not bool(organization["is_active"]):
            raise AuthorizationError("A organizacao selecionada esta inativa.")
    return organization_id


def is_current_user_platform_admin(database_path: Optional[os.PathLike] = None) -> bool:
    """Return True if the active user is a platform administrator."""
    try:
        user = current_user(database_path)
        return bool(user and user.is_platform_admin)
    except Exception:
        return False


def audit_business_access(
    action: str,
    target_type: str,
    target_id: Optional[int],
    organization_id: int,
    database_path: Optional[os.PathLike] = None,
) -> None:
    """Audit every cross-organization operation executed by a platform admin."""

    user = require_login(database_path)
    if not user.is_platform_admin or user.organization_id == organization_id:
        return
    target_org_id = organization_id if organization_id and organization_id > 0 else user.organization_id
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        _audit(
            database,
            target_org_id,
            user.id,
            "platform.{}".format(action),
            target_type,
            target_id,
        )


def _empty_identity_store(database_path: Optional[os.PathLike]) -> bool:
    initialize_auth_schema(database_path)
    with connection(database_path) as database:
        return not bool(database.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"])


def bootstrap_from_environment(database_path: Optional[os.PathLike] = None) -> bool:
    """Create the first organization and global administrator exactly once.

    The following variables must all be defined together: NENC_BOOTSTRAP_ORGANIZATION,
    NENC_BOOTSTRAP_NAME, NENC_BOOTSTRAP_EMAIL, NENC_BOOTSTRAP_PHONE, and
    NENC_BOOTSTRAP_PASSWORD. The variables are ignored after any account exists.
    """

    initialize_auth_schema(database_path)
    names = (
        "NENC_BOOTSTRAP_ORGANIZATION",
        "NENC_BOOTSTRAP_NAME",
        "NENC_BOOTSTRAP_EMAIL",
        "NENC_BOOTSTRAP_PHONE",
        "NENC_BOOTSTRAP_PASSWORD",
    )
    values = {name: os.environ.get(name, "").strip() for name in names}
    if not _empty_identity_store(database_path):
        return False
    if not any(values.values()):
        return False
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise BootstrapConfigurationError(
            "Bootstrap incompleto; defina tambem {}.".format(", ".join(missing))
        )
    organization = create_organization(
        values["NENC_BOOTSTRAP_ORGANIZATION"],
        database_path=database_path,
        _bootstrap=True,
    )
    create_user(
        name=values["NENC_BOOTSTRAP_NAME"],
        email=values["NENC_BOOTSTRAP_EMAIL"],
        phone=values["NENC_BOOTSTRAP_PHONE"],
        organization_id=organization.id,
        password=values["NENC_BOOTSTRAP_PASSWORD"],
        module_keys=MODULE_KEYS,
        is_organization_admin=True,
        is_platform_admin=True,
        database_path=database_path,
        _bootstrap=True,
    )
    return True


def render_login_page(database_path: Optional[os.PathLike] = None) -> Optional[User]:
    """Render the only unauthenticated interface and establish an opaque session."""

    st = _streamlit()
    try:
        bootstrapped = bootstrap_from_environment(database_path)
    except BootstrapConfigurationError as error:
        st.error(str(error))
        st.stop()
    user = current_user(database_path)
    if user is not None:
        return user
    st.title("NENC Insights")
    if bootstrapped:
        st.info("A conta administrativa inicial foi criada. Faca login para continuar.")
    elif _empty_identity_store(database_path):
        st.error("Nenhuma conta foi inicializada. Configure as variaveis NENC_BOOTSTRAP_*.")
    with st.form("nenc-login", clear_on_submit=False):
        email = st.text_input("E-mail", autocomplete="email")
        password = st.text_input("Senha", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Entrar", type="primary")
    if submitted:
        try:
            login(email, password, database_path)
        except AuthenticationError as error:
            st.error(str(error))
        else:
            st.rerun()
    return None


def render_auth_sidebar(user: User, database_path: Optional[os.PathLike] = None) -> None:
    """Display the verified identity, global target organization, and logout control."""

    st = _streamlit()
    with st.sidebar:
        st.caption("Sessao ativa")
        st.write(user.name)
        st.caption("{} | {}".format(user.organization_name, user.email))
        if user.is_platform_admin:
            organizations = list_organizations(user, database_path=database_path)
            labels = {0: "🌐 Todas as Organizações"}
            labels.update({organization.id: "🏢 {}".format(organization.name) for organization in organizations})
            available_ids = list(labels)
            selected_id = st.session_state.get(ACTIVE_ORGANIZATION_STATE_KEY, user.organization_id)
            if selected_id not in labels:
                selected_id = user.organization_id if user.organization_id in labels else available_ids[0]
            selected_index = available_ids.index(selected_id)
            st.selectbox(
                "Organizacao ativa",
                available_ids,
                index=selected_index,
                format_func=lambda organization_id: labels[organization_id],
                key=ACTIVE_ORGANIZATION_STATE_KEY,
            )
        if st.button("Sair", key="nenc-logout"):
            logout(database_path)
            st.rerun()