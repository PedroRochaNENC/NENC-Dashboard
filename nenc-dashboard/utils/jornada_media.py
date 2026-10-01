"""
Videos das gravacoes da Jornada de Compra, guardados no disco do servidor.

Um video de eye tracking passa de 100 MB; dentro do SQLite ele incharia o
banco, os backups e cada leitura de linha. O arquivo fica numa pasta do mesmo
volume do banco (em producao, `./data`), e `jc_media` guarda so o indice.

O nome no disco e o sha256 do conteudo: nada do participante vai para o
caminho, e o mesmo video enviado duas vezes vira um arquivo so.
"""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Dict, Iterable, Union

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm")
_CHUNK = 4 * 1024 * 1024


def media_root() -> Path:
    """Pasta raiz dos videos: `NENC_MEDIA_DIR`, ou `jornada_media` ao lado do banco."""

    configured = os.environ.get("NENC_MEDIA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    default_db = Path(__file__).resolve().parent.parent / "prosodia.db"
    database = Path(os.environ.get("NENC_DB_PATH", str(default_db))).expanduser()
    return database.parent / "jornada_media"


def project_dir(organization_id: int, project_id: int) -> Path:
    return media_root() / str(int(organization_id)) / str(int(project_id))


def resolve(rel_path: str) -> Path:
    """Caminho absoluto de um video, recusando qualquer coisa fora da raiz."""

    root = media_root().resolve()
    candidate = (root / PurePosixPath(str(rel_path))).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("Caminho de video fora da pasta de midia.")
    return candidate


def save_video(
    organization_id: int,
    project_id: int,
    filename: str,
    source: Union[bytes, BinaryIO],
) -> Dict[str, object]:
    """Grava o video em blocos e devolve sha256, tamanho e caminho relativo.

    Escreve num temporario da propria pasta e renomeia no fim: um player nunca
    encontra um arquivo pela metade, e uma falha nao deixa lixo com nome valido.
    """

    extension = Path(str(filename)).suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        raise ValueError(
            "Formato de video nao suportado: {}. Use {}.".format(
                extension or "(sem extensao)", ", ".join(VIDEO_EXTENSIONS)
            )
        )
    target_dir = project_dir(organization_id, project_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    handle, temporary = tempfile.mkstemp(dir=str(target_dir), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as output:
            if isinstance(source, (bytes, bytearray, memoryview)):
                chunk = bytes(source)
                digest.update(chunk)
                output.write(chunk)
                size = len(chunk)
            else:
                if hasattr(source, "seek"):
                    source.seek(0)
                while True:
                    chunk = source.read(_CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output.write(chunk)
                    size += len(chunk)
        if not size:
            raise ValueError("Video vazio: {}".format(filename))
        sha256 = digest.hexdigest()
        final_path = target_dir / "{}{}".format(sha256, extension)
        if final_path.exists():
            os.remove(temporary)
        else:
            os.replace(temporary, final_path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise

    rel_path = PurePosixPath(
        str(int(organization_id)), str(int(project_id)), final_path.name
    ).as_posix()
    return {"sha256": sha256, "size_bytes": size, "rel_path": rel_path}


def adopt_video(organization_id: int, project_id: int, filename: str, path: Path) -> Dict[str, object]:
    """Move para a pasta de mídia um vídeo que já está em disco (o inbox da importação).

    O inbox fica no mesmo volume, então mover é instantâneo mesmo para GBs; se
    não der para renomear (outro disco), copia em blocos pelo `save_video`.
    """

    path = Path(path)
    extension = Path(str(filename)).suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        raise ValueError("Formato de video nao suportado: {}.".format(extension or "(sem extensao)"))
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    if not size:
        raise ValueError("Video vazio: {}".format(filename))
    sha256 = digest.hexdigest()
    target_dir = project_dir(organization_id, project_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / "{}{}".format(sha256, extension)
    if final_path.exists():
        path.unlink(missing_ok=True)
    else:
        try:
            os.replace(path, final_path)
        except OSError:
            with path.open("rb") as handle:
                stored = save_video(organization_id, project_id, filename, handle)
            path.unlink(missing_ok=True)
            return stored
    rel_path = PurePosixPath(str(int(organization_id)), str(int(project_id)), final_path.name).as_posix()
    return {"sha256": sha256, "size_bytes": size, "rel_path": rel_path}


def delete_files(rel_paths: Iterable[str]) -> int:
    """Apaga videos pelo caminho relativo. Best-effort; devolve quantos sairam."""

    removed = 0
    for rel_path in rel_paths:
        if not rel_path:
            continue
        try:
            os.remove(resolve(rel_path))
            removed += 1
        except (OSError, ValueError):
            pass
    return removed


def delete_project_dir(organization_id: int, project_id: int) -> None:
    """Remove a pasta do projeto, com o que sobrou nela."""

    folder = project_dir(organization_id, project_id)
    try:
        resolve(folder.relative_to(media_root()).as_posix())
    except ValueError:
        return
    shutil.rmtree(folder, ignore_errors=True)
