"""
Arquivos do Teste Sensorial guardados no disco do servidor.

As saídas do pipeline de um estudo passam de 100 MB (o PSD por janela do EEG
tem dezenas de milhares de linhas × 80 colunas); dentro do SQLite elas
inchariam o banco, os backups e cada leitura. Ficam numa pasta do mesmo volume
do banco (em produção, `./data`), e `sens_files` guarda só o índice:

- `<org>/<projeto>/orig/<sha256>.<ext>`: o arquivo como chegou (CSV, gz, xlsx);
- `<org>/<projeto>/tab/<sha256>.v<versão>.parquet`: a tabela normalizada que o
  modelo lê — sem colunas com nome de participante, só o código.

O nome no disco é o sha256 do conteúdo: nada do participante vai para o
caminho. Toda escrita vai para um temporário da própria pasta e é renomeada no
fim; uma leitura nunca encontra um arquivo pela metade.
"""

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional, Sequence, Union

import pandas as pd

_CHUNK = 4 * 1024 * 1024


def store_root() -> Path:
    """Raiz: `NENC_SENSORIAL_DIR`, ou `sensorial_data` ao lado do banco."""
    configured = os.environ.get("NENC_SENSORIAL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    default_db = Path(__file__).resolve().parent.parent / "prosodia.db"
    database = Path(os.environ.get("NENC_DB_PATH", str(default_db))).expanduser()
    return database.parent / "sensorial_data"


def project_dir(organization_id: int, project_id: int) -> Path:
    return store_root() / str(int(organization_id)) / str(int(project_id))


def resolve(rel_path: str) -> Path:
    """Caminho absoluto de um arquivo guardado, recusando qualquer coisa fora da raiz."""
    root = store_root().resolve()
    candidate = (root / PurePosixPath(str(rel_path))).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("Caminho fora da pasta do Teste Sensorial.")
    return candidate


def _relative(path: Path) -> str:
    return path.resolve().relative_to(store_root().resolve()).as_posix()


def _clean_extension(extension: str) -> str:
    extension = "".join(ch for ch in str(extension or "").lower() if ch.isalnum() or ch == ".")
    extension = extension if extension.startswith(".") else "." + extension if extension else ""
    return extension[:12]


def save_original(
    organization_id: int,
    project_id: int,
    sha256: str,
    extension: str,
    source: Union[bytes, Path],
    *,
    move: bool = False,
) -> str:
    """Guarda o original e devolve o caminho relativo à raiz.

    `source` são bytes ou um arquivo (o do inbox, por exemplo); com `move=True`
    o arquivo é movido em vez de copiado. Se o mesmo sha já está guardado, nada
    é reescrito.
    """
    sha256 = str(sha256).lower()
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("sha256 inválido.")
    target_dir = project_dir(organization_id, project_id) / "orig"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "{}{}".format(sha256, _clean_extension(extension))
    if target.exists():
        if move and isinstance(source, Path):
            Path(source).unlink(missing_ok=True)
        return _relative(target)
    if isinstance(source, Path):
        if move:
            try:
                os.replace(source, target)
                return _relative(target)
            except OSError:
                pass  # outro volume: copia
        handle, temporary = tempfile.mkstemp(dir=str(target_dir), suffix=".part")
        os.close(handle)
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        if move:
            Path(source).unlink(missing_ok=True)
        return _relative(target)
    handle, temporary = tempfile.mkstemp(dir=str(target_dir), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as output:
            data = bytes(source)
            for start in range(0, len(data), _CHUNK):
                output.write(data[start:start + _CHUNK])
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _relative(target)


def table_path(organization_id: int, project_id: int, sha256: str, version: int) -> Path:
    return project_dir(organization_id, project_id) / "tab" / "{}.v{}.parquet".format(str(sha256).lower(), int(version))


def write_table(frame: pd.DataFrame, path: Path) -> Path:
    """Grava a tabela normalizada em Parquet (zstd), de forma atômica."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
    os.close(handle)
    try:
        frame.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_table(path: Path, columns: Optional[Sequence[str]] = None) -> pd.DataFrame:
    return pd.read_parquet(path, columns=list(columns) if columns else None)


def delete_files(rel_paths: Iterable[str]) -> int:
    removed = 0
    for rel_path in rel_paths:
        if not rel_path:
            continue
        try:
            path = resolve(rel_path)
        except ValueError:
            continue
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def delete_tables(organization_id: int, project_id: int, sha256: str) -> None:
    """Apaga todas as versões da tabela normalizada de um arquivo."""
    folder = project_dir(organization_id, project_id) / "tab"
    for path in folder.glob("{}.v*.parquet".format(str(sha256).lower())):
        path.unlink(missing_ok=True)


def delete_project_dir(organization_id: int, project_id: int) -> None:
    folder = project_dir(organization_id, project_id)
    root = store_root().resolve()
    try:
        target = folder.resolve()
    except OSError:
        return
    if target == root or root not in target.parents:
        return
    shutil.rmtree(target, ignore_errors=True)
