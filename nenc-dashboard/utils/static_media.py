"""
Copias temporarias de midia no diretorio `static/` servido pelo Streamlit.

`st.video` com caminho de arquivo le o video inteiro para a memoria a cada
execucao; o `static/` e servido direto pelo servidor, com Range (o player pula
para qualquer ponto sem baixar tudo). O preco e que o `static/` nao tem
autenticacao: por isso o nome e um token aleatorio preso a sessao, e a copia
expira por idade — o mesmo cuidado que o NencBoost toma com o audio
(`modules/prosodia/audio_timeline.py`).
"""

import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Iterable

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DEFAULT_TTL_SECONDS = 2 * 60 * 60


def new_name(prefix: str, suffix: str) -> str:
    """Nome imprevisivel para um arquivo publico."""

    return "{}{}{}".format(prefix, secrets.token_urlsafe(24), suffix)


def purge_stale(
    prefix: str,
    suffixes: Iterable[str],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    static_dir: Path = STATIC_DIR,
) -> int:
    """Apaga as copias do prefixo mais velhas que o prazo. Devolve quantas."""

    if not static_dir.is_dir():
        return 0
    endings = tuple(suffixes) + tuple(suffix + ".part" for suffix in suffixes)
    now = time.time()
    removed = 0
    for entry in os.listdir(static_dir):
        if not entry.startswith(prefix) or not entry.endswith(endings):
            continue
        path = static_dir / entry
        try:
            if now - path.stat().st_mtime > ttl_seconds:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed


def publish(source: Path, name: str, static_dir: Path = STATIC_DIR) -> str:
    """Garante a copia publica de `source` com o nome dado e devolve a URL relativa.

    Copia para um `.part` e renomeia, para o player nunca ler um arquivo pela
    metade. Se a copia ja existe, so renova a idade dela.
    """

    static_dir.mkdir(parents=True, exist_ok=True)
    destination = static_dir / name
    if destination.exists():
        try:
            os.utime(destination, None)
        except OSError:
            pass
    else:
        partial = static_dir / (name + ".part")
        shutil.copyfile(source, partial)
        os.replace(partial, destination)
    return "app/static/{}".format(name)
