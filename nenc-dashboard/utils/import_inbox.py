"""
Motor das importações pendentes: o que o script de envio mandou e ainda não
foi revisado, para qualquer módulo por projeto.

Cada módulo cria um `Inbox` com as suas tabelas, papéis e limites
(`utils/jornada_imports.py`, `utils/sensorial_imports.py`). Duas metades,
com guardas diferentes:

- **API** (`api/main.py`, token da organização, sem conta logada): cria o lote,
  recebe os arquivos em blocos e fecha. Toda função recebe a organização de
  forma explícita e confere que o projeto é dela. Nada aqui grava em tabela de
  análise: os arquivos ficam no inbox, fora do banco.
- **Tela** (Uploads, sessão com escrita): lista, grava ou descarta. Gravar é do
  módulo (`apply`), com as funções guardadas do banco dele: o autor de cada
  arquivo é quem revisou, porque o token não diz quem enviou.

O inbox fica em `NENC_IMPORT_INBOX` ou, sem a variável, em `jornada_inbox/`
ao lado do banco, numa subpasta por módulo (a Jornada usa a raiz, como
sempre usou): `<org>/<projeto>/<lote>/<arquivo>.bin`. Lote parado vence em
`NENC_IMPORT_TTL_DAYS` dias (30 por padrão) e seus arquivos são apagados.
"""

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from utils import auth

STATUSES = ("recebendo", "pronta", "gravando", "gravada", "descartada", "expirada")
OPEN_STATUSES = ("recebendo", "pronta")
BATCH_MAX_BYTES = 10 * 1024 * 1024 * 1024


class ImportRefused(ValueError):
    """Recusa com mensagem para quem envia (vira 4xx na API)."""


class ChunkOutOfOrder(ImportRefused):
    """Bloco que não começa onde o arquivo parou; `expected` diz o offset certo."""

    def __init__(self, expected: int):
        super().__init__("Bloco fora de ordem: esperado o offset {}.".format(expected))
        self.expected = int(expected)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ttl_days() -> int:
    try:
        return max(1, int(os.environ.get("NENC_IMPORT_TTL_DAYS", "30")))
    except ValueError:
        return 30


def clean_rel_path(rel_path: str) -> str:
    """Caminho relativo só como metadado: sem raiz, sem `..`, com barras normais."""
    parts = [part for part in PurePosixPath(str(rel_path).replace("\\", "/")).parts
             if part not in ("", ".", "..", "/")]
    if not parts:
        raise ImportRefused("Caminho do arquivo vazio.")
    return "/".join(parts)[:500]


def _actor_id(actor) -> Optional[int]:
    user_id = getattr(actor, "id", None)
    return user_id if isinstance(user_id, int) else None


class Inbox:
    """Lotes de importação de um módulo.

    `db` é o módulo de banco (`jornada_db`, `sensorial_db`): dele vêm a
    conexão, a guarda de escrita, a organização ativa e a auditoria, sempre
    lidos na hora da chamada. `known(conn, project_id)` diz o que o projeto já
    tem, além do pendente; `decorate(item)` completa um arquivo listado.
    """

    def __init__(
        self,
        *,
        db: ModuleType,
        audit_prefix: str,
        projects_table: str,
        batches_table: str,
        files_table: str,
        target_type: str,
        subdir: str,
        roles: Sequence[str],
        chunk_max_bytes: int,
        file_limit: Callable[[str], int],
        known: Callable[..., Dict[str, List[str]]],
        decorate: Optional[Callable[[Dict], None]] = None,
    ):
        self.db = db
        self.audit_prefix = audit_prefix
        self.projects_table = projects_table
        self.batches_table = batches_table
        self.files_table = files_table
        self.target_type = target_type
        self.subdir = subdir
        self.roles = tuple(roles)
        self.chunk_max_bytes = int(chunk_max_bytes)
        self.file_limit = file_limit
        self.known = known
        self.decorate = decorate

    # -- caminhos ------------------------------------------------------------

    def root(self) -> Path:
        configured = os.environ.get("NENC_IMPORT_INBOX", "").strip()
        base = Path(configured).expanduser() if configured else self.db._database_path().parent / "jornada_inbox"
        return base / self.subdir if self.subdir else base

    def batch_dir(self, organization_id: int, project_id: int, batch_id: int) -> Path:
        return self.root() / str(int(organization_id)) / str(int(project_id)) / str(int(batch_id))

    def file_path(self, batch: Dict, file_id: int) -> Path:
        return self.batch_dir(batch["organization_id"], batch["project_id"], batch["id"]) / "{}.bin".format(
            int(file_id))

    def remove_dir(self, path: Path) -> None:
        root = self.root().resolve()
        try:
            target = path.resolve()
        except OSError:
            return
        if target == root or root not in target.parents:
            return
        shutil.rmtree(target, ignore_errors=True)

    def remove_project_inbox(self, organization_id: int, project_id: int) -> None:
        """Chamado ao excluir o projeto: o que estava pendente sai do disco."""
        self.remove_dir(self.root() / str(int(organization_id)) / str(int(project_id)))

    # -- consultas internas --------------------------------------------------

    def _project_of_org(self, conn, organization_id: int, project_id: int) -> Dict:
        row = conn.execute(
            "SELECT id, name FROM {} WHERE id = ? AND organization_id = ?".format(self.projects_table),
            (int(project_id), int(organization_id)),
        ).fetchone()
        if row is None:
            raise ImportRefused("Projeto não encontrado nesta organização.")
        return dict(row)

    def _batch_of_org(self, conn, organization_id: int, batch_id: int) -> Dict:
        row = conn.execute(
            "SELECT * FROM {} WHERE id = ? AND organization_id = ?".format(self.batches_table),
            (int(batch_id), int(organization_id)),
        ).fetchone()
        if row is None:
            raise ImportRefused("Importação não encontrada nesta organização.")
        return dict(row)

    def _audit_system(self, organization_id: int, action: str, batch_id: int, details: Dict) -> None:
        auth.audit_system_event(organization_id, "{}.import.{}".format(self.audit_prefix, action),
                                self.target_type, batch_id, details)

    # -- API: organização explícita ------------------------------------------

    def list_projects(self, organization_id: int) -> List[Dict]:
        """Projetos da organização do token: só id e nome."""
        self.db.init_db()
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT id, name FROM {} WHERE organization_id = ? ORDER BY name".format(self.projects_table),
                (int(organization_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def expire_stale(self, now_at: Optional[datetime] = None) -> int:
        """Lotes abertos há mais que o prazo viram `expirada` e perdem os arquivos."""
        limit = ((now_at or datetime.now()) - timedelta(days=_ttl_days())).strftime("%Y-%m-%d %H:%M:%S")
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT id, organization_id, project_id FROM {} "
                "WHERE status IN ('recebendo', 'pronta') AND COALESCE(updated_at, created_at) < ?".format(
                    self.batches_table),
                (limit,),
            ).fetchall()
            for row in rows:
                conn.execute("UPDATE {} SET status = 'expirada', updated_at = ? WHERE id = ?".format(
                    self.batches_table), (now(), row["id"]))
        for row in rows:
            self.remove_dir(self.batch_dir(row["organization_id"], row["project_id"], row["id"]))
        return len(rows)

    def open_batch(self, organization_id: int, project_id: int, source_label: str = "",
                   client_info: Optional[Dict] = None, resume: bool = True) -> Dict[str, object]:
        """Lote para um envio: retoma o que ficou recebendo ou abre outro.

        Um envio interrompido deixa o lote em `recebendo`. Retomá-lo evita que os
        arquivos que já chegaram fiquem presos num lote que nunca fecha: o script
        pula o que já está lá e o protocolo de blocos continua o arquivo do meio.
        """
        self.db.init_db()
        self.expire_stale()
        label = str(source_label or "")[:300]
        client = json.dumps(client_info or {}, ensure_ascii=False)[:2000]
        with self.db._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._project_of_org(conn, organization_id, project_id)
            row = conn.execute(
                "SELECT id FROM {} WHERE organization_id = ? AND project_id = ? "
                "AND status = 'recebendo' ORDER BY id DESC LIMIT 1".format(self.batches_table),
                (int(organization_id), int(project_id)),
            ).fetchone() if resume else None
            if row is not None:
                batch_id = int(row["id"])
                conn.execute("UPDATE {} SET source_label = ?, client_json = ?, updated_at = ? WHERE id = ?".format(
                    self.batches_table), (label, client, now(), batch_id))
            else:
                cursor = conn.execute(
                    "INSERT INTO {} (organization_id, project_id, status, source_label, client_json, "
                    "created_at, updated_at) VALUES (?, ?, 'recebendo', ?, ?, ?, ?)".format(self.batches_table),
                    (int(organization_id), int(project_id), label, client, now(), now()),
                )
                batch_id = int(cursor.lastrowid)
        self._audit_system(organization_id, "resumed" if row is not None else "received", batch_id,
                           {"origem": "api", "project_id": int(project_id)})
        return {"batch_id": batch_id, "resumed": row is not None}

    def create_batch(self, organization_id: int, project_id: int, source_label: str = "",
                     client_info: Optional[Dict] = None) -> int:
        """Sempre um lote novo (a API usa `open_batch`, que retoma)."""
        return int(self.open_batch(organization_id, project_id, source_label, client_info, resume=False)["batch_id"])

    def known_hashes(self, organization_id: int, project_id: int) -> Dict[str, List[str]]:
        """O que o projeto já tem (ou tem pendente): o script não reenvia."""
        with self.db._connect() as conn:
            self._project_of_org(conn, organization_id, project_id)
            known = self.known(conn, int(project_id))
            pending = []
            for row in conn.execute(
                "SELECT f.sha256, f.source_sha256 FROM {} f JOIN {} b ON b.id = f.batch_id "
                "WHERE b.project_id = ? AND b.status IN ('recebendo', 'pronta') AND f.status = 'completo'".format(
                    self.files_table, self.batches_table),
                (int(project_id),),
            ):
                pending += [value for value in (row["sha256"], row["source_sha256"]) if value]
        result = {key: sorted(set(values)) for key, values in known.items()}
        result["pending"] = sorted(set(pending))
        return result

    def receive_chunk(
        self,
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
        if role not in self.roles:
            raise ImportRefused("Papel de arquivo desconhecido: {}.".format(role))
        sha256 = str(sha256 or "").lower()
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ImportRefused("sha256 inválido.")
        size, offset = int(size), int(offset)
        limit = self.file_limit(role)
        if size <= 0 or size > limit:
            raise ImportRefused("Tamanho fora do limite para {} ({} MB).".format(role, limit // (1024 * 1024)))
        if len(data) > self.chunk_max_bytes:
            raise ImportRefused("Bloco maior que {} MB.".format(self.chunk_max_bytes // (1024 * 1024)))
        rel_path = clean_rel_path(rel_path)
        files = self.files_table
        with self.db._connect() as conn:
            # Um bloco por vez no lote: ler o quanto chegou e anexar no disco precisa ser atômico.
            conn.execute("BEGIN IMMEDIATE")
            batch = self._batch_of_org(conn, organization_id, batch_id)
            if batch["status"] != "recebendo":
                raise ImportRefused("A importação não está mais recebendo arquivos ({}).".format(batch["status"]))
            folder = self.batch_dir(batch["organization_id"], batch["project_id"], batch_id)
            row = conn.execute("SELECT * FROM {} WHERE batch_id = ? AND rel_path = ?".format(files),
                               (int(batch_id), rel_path)).fetchone()
            if row is not None and (row["sha256"] != sha256 or int(row["size_bytes"]) != size):
                if offset != 0:
                    raise ImportRefused("O arquivo mudou no meio do envio; recomece este arquivo do início.")
                # Mudou entre um envio e outro (lote retomado): recomeça do zero.
                conn.execute("DELETE FROM {} WHERE id = ?".format(files), (row["id"],))
                conn.commit()
                for suffix in (".part", ".bin"):
                    (folder / "{}{}".format(row["id"], suffix)).unlink(missing_ok=True)
                conn.execute("BEGIN IMMEDIATE")
                row = None
            if row is None:
                if offset != 0:
                    raise ImportRefused("O primeiro bloco precisa começar no offset 0.")
                total = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM {} WHERE batch_id = ?".format(files),
                                     (int(batch_id),)).fetchone()[0]
                if total + size > BATCH_MAX_BYTES:
                    raise ImportRefused("A importação passaria de {} GB.".format(BATCH_MAX_BYTES // 1024 ** 3))
                cursor = conn.execute(
                    "INSERT INTO {} (batch_id, rel_path, role, meta_json, sha256, source_sha256, size_bytes, "
                    "received_bytes, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'recebendo', ?)".format(files),
                    (int(batch_id), rel_path, role, json.dumps(meta or {}, ensure_ascii=False), sha256,
                     (str(source_sha256).lower() if source_sha256 else None), size, now()),
                )
                row = conn.execute("SELECT * FROM {} WHERE id = ?".format(files), (cursor.lastrowid,)).fetchone()
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
                conn.execute("UPDATE {} SET status = 'invalido', received_bytes = 0 WHERE id = ?".format(files),
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
                    conn.execute("UPDATE {} SET status = 'invalido', received_bytes = 0 WHERE id = ?".format(files),
                                 (row["id"],))
                    conn.commit()
                    raise ImportRefused("O sha256 do arquivo recebido não confere; envie de novo.")
                os.replace(part, folder / "{}.bin".format(row["id"]))
                status = "completo"
            conn.execute("UPDATE {} SET received_bytes = ?, status = ? WHERE id = ?".format(files),
                         (received, status, row["id"]))
            conn.execute("UPDATE {} SET updated_at = ? WHERE id = ?".format(self.batches_table),
                         (now(), int(batch_id)))
        return {"file_id": row["id"], "received": received, "complete": status == "completo"}

    def file_status(self, organization_id: int, batch_id: int, rel_path: str) -> Dict[str, object]:
        """Quanto de um arquivo já chegou: o script retoma a partir daí."""
        with self.db._connect() as conn:
            self._batch_of_org(conn, organization_id, batch_id)
            row = conn.execute(
                "SELECT id, received_bytes, status FROM {} WHERE batch_id = ? AND rel_path = ?".format(
                    self.files_table),
                (int(batch_id), clean_rel_path(rel_path)),
            ).fetchone()
        if row is None:
            return {"received": 0, "complete": False}
        return {"file_id": row["id"], "received": int(row["received_bytes"]), "complete": row["status"] == "completo"}

    def close_batch(self, organization_id: int, batch_id: int, summary: Optional[Dict] = None,
                    ignored: Sequence[Dict] = ()) -> Dict[str, object]:
        """Fecha o lote para revisão. Arquivo incompleto impede o fechamento."""
        with self.db._connect() as conn:
            batch = self._batch_of_org(conn, organization_id, batch_id)
            if batch["status"] != "recebendo":
                raise ImportRefused("A importação já foi fechada ({}).".format(batch["status"]))
            incomplete = [r["rel_path"] for r in conn.execute(
                "SELECT rel_path FROM {} WHERE batch_id = ? AND status != 'completo'".format(self.files_table),
                (int(batch_id),))]
            if incomplete:
                raise ImportRefused("Arquivos incompletos: {}.".format(", ".join(incomplete[:5])))
            count = conn.execute("SELECT COUNT(*) FROM {} WHERE batch_id = ?".format(self.files_table),
                                 (int(batch_id),)).fetchone()[0]
            # Sem arquivo novo não há o que revisar: o lote não vira pendência vazia.
            status = "pronta" if count else "descartada"
            conn.execute(
                "UPDATE {} SET status = ?, summary_json = ?, ignored_json = ?, closed_at = ?, "
                "updated_at = ? WHERE id = ?".format(self.batches_table),
                (status, json.dumps(summary or {}, ensure_ascii=False)[:20000],
                 json.dumps(list(ignored)[:2000], ensure_ascii=False), now(), now(), int(batch_id)),
            )
        if not count:
            self.remove_dir(self.batch_dir(batch["organization_id"], batch["project_id"], batch_id))
        self._audit_system(organization_id, "closed", batch_id, {"origem": "api", "arquivos": int(count)})
        return {"batch_id": int(batch_id), "files": int(count), "status": status}

    # -- Tela: sessão, guardas de escrita ------------------------------------

    @staticmethod
    def _decode(row) -> Dict:
        item = dict(row)
        for key, default in (("summary_json", {}), ("ignored_json", []), ("client_json", {})):
            try:
                item[key.replace("_json", "")] = json.loads(item.pop(key) or "null") or default
            except ValueError:
                item[key.replace("_json", "")] = default
        return item

    def list_batches(self, project_id: int, statuses: Iterable[str] = OPEN_STATUSES) -> List[Dict]:
        """Lotes do projeto (da organização ativa), com o tamanho e a contagem por papel."""
        self.expire_stale()
        statuses = tuple(statuses)
        with self.db._connect() as conn:
            self.db._project_org(conn, project_id, self.db._active_organization_id())
            rows = conn.execute(
                "SELECT * FROM {} WHERE project_id = ? AND status IN ({}) ORDER BY id DESC".format(
                    self.batches_table, ",".join("?" * len(statuses))),
                [int(project_id)] + list(statuses),
            ).fetchall()
            batches = [self._decode(row) for row in rows]
            for batch in batches:
                counts = conn.execute(
                    "SELECT role, COUNT(*) AS files, SUM(size_bytes) AS bytes FROM {} "
                    "WHERE batch_id = ? GROUP BY role".format(self.files_table), (batch["id"],)).fetchall()
                batch["roles"] = {row["role"]: {"files": row["files"], "bytes": row["bytes"]} for row in counts}
        return batches

    def batch_files(self, project_id: int, batch_id: int) -> List[Dict]:
        with self.db._connect() as conn:
            self.db._project_org(conn, project_id, self.db._active_organization_id())
            rows = conn.execute(
                "SELECT f.* FROM {} f JOIN {} b ON b.id = f.batch_id "
                "WHERE f.batch_id = ? AND b.project_id = ? ORDER BY f.role, f.rel_path".format(
                    self.files_table, self.batches_table),
                (int(batch_id), int(project_id)),
            ).fetchall()
        files = []
        for row in rows:
            item = dict(row)
            item["meta"] = json.loads(item.pop("meta_json") or "{}")
            if self.decorate is not None:
                self.decorate(item)
            files.append(item)
        return files

    def project_batch(self, conn, project_id: int, batch_id: int) -> Dict:
        """O lote, se for do projeto e o projeto for visível na organização ativa."""
        self.db._project_org(conn, project_id, self.db._active_organization_id())
        row = conn.execute("SELECT * FROM {} WHERE id = ? AND project_id = ?".format(self.batches_table),
                           (int(batch_id), int(project_id))).fetchone()
        if row is None:
            raise ValueError("Importação não encontrada neste projeto.")
        return dict(row)

    def missing_files(self, project_id: int, batch_id: int) -> List[str]:
        """Arquivos completos do lote que não estão no inbox (ex.: banco restaurado sem a pasta)."""
        with self.db._connect() as conn:
            batch = self.project_batch(conn, project_id, batch_id)
        return [item["rel_path"] for item in self.batch_files(project_id, batch_id)
                if item["status"] == "completo" and not self.file_path(batch, item["id"]).exists()]

    def read_file(self, project_id: int, batch_id: int, file_id: int) -> bytes:
        """Conteúdo de um arquivo pendente para a prévia."""
        with self.db._connect() as conn:
            batch = self.project_batch(conn, project_id, batch_id)
        return self.file_path(batch, file_id).read_bytes()

    def apply(self, project_id: int, batch_id: int,
              work: Callable[[object, Dict], Tuple[Dict, Iterable[int]]]) -> Dict:
        """Grava o lote como o revisor: `work(actor, batch)` devolve o relatório e os ids gravados.

        O lote é reservado antes (dois revisores clicando juntos não gravam duas
        vezes) e volta a ficar pronto se algo falhar: gravar de novo é seguro,
        porque os arquivos não duplicam. Os não gravados ficam `descartado` e
        voltam num próximo envio.
        """
        actor = self.db._require_write()
        with self.db._connect() as conn:
            batch = self.project_batch(conn, project_id, batch_id)
            claimed = conn.execute(
                "UPDATE {} SET status = 'gravando', updated_at = ? WHERE id = ? AND status = 'pronta'".format(
                    self.batches_table),
                (now(), int(batch_id)),
            ).rowcount
        if not claimed:
            raise ValueError("Só uma importação pronta pode ser gravada ({}).".format(batch["status"]))
        try:
            report, recorded = work(actor, batch)
            with self.db._connect() as conn:
                conn.execute(
                    "UPDATE {} SET status = 'gravada', decided_at = ?, decided_by_user_id = ?, "
                    "updated_at = ? WHERE id = ?".format(self.batches_table),
                    (now(), _actor_id(actor), now(), int(batch_id)),
                )
                conn.execute("UPDATE {} SET status = 'descartado' WHERE batch_id = ?".format(self.files_table),
                             (int(batch_id),))
                conn.executemany("UPDATE {} SET status = 'gravado' WHERE id = ?".format(self.files_table),
                                 [(int(file_id),) for file_id in recorded])
        except BaseException:
            with self.db._connect() as conn:
                conn.execute("UPDATE {} SET status = 'pronta', updated_at = ? WHERE id = ? "
                             "AND status = 'gravando'".format(self.batches_table), (now(), int(batch_id)))
            raise
        self.db._audit("{}.import.apply".format(self.audit_prefix), self.target_type, int(batch_id),
                       batch["organization_id"], write=True)
        self.remove_dir(self.batch_dir(batch["organization_id"], project_id, batch_id))
        return report

    def discard_batch(self, project_id: int, batch_id: int) -> None:
        actor = self.db._require_write()
        with self.db._connect() as conn:
            batch = self.project_batch(conn, project_id, batch_id)
            if batch["status"] not in OPEN_STATUSES:
                raise ValueError("Esta importação já foi decidida ({}).".format(batch["status"]))
            conn.execute(
                "UPDATE {} SET status = 'descartada', decided_at = ?, decided_by_user_id = ?, "
                "updated_at = ? WHERE id = ?".format(self.batches_table),
                (now(), _actor_id(actor), now(), int(batch_id)),
            )
        self.db._audit("{}.import.discard".format(self.audit_prefix), self.target_type, int(batch_id),
                       batch["organization_id"], write=True)
        self.remove_dir(self.batch_dir(batch["organization_id"], project_id, batch_id))
