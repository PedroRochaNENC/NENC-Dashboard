"""Isola a suite do banco padrao `nenc-dashboard/prosodia.db`.

Sem NENC_DB_PATH nem `database_path`, utils/auth.py, prosodia_db, jornada_db
e jornada_media caem no prosodia.db ao lado do app. Um teste que esquece o
caminho escrevia ali - e o arquivo, com esquema e sem usuarios, mudava o
resultado de testes seguintes (foi assim que a guarda de autoria de
can_manage_user passou a liberar a conta por engano).

Duas camadas:

1. NENC_DB_PATH aponta para uma pasta temporaria desde a coleta, e cada teste
   recebe um banco proprio e vazio. Um teste que esquece o caminho cai num
   banco descartavel que nao vaza para o proximo.
2. Uma checagem por teste falha se o prosodia.db padrao aparecer, nomeando o
   teste culpado, e o remove para nao contaminar o resto da suite. Um
   prosodia.db que ja existia antes (o banco local de desenvolvimento) nao e
   vigiado: o Streamlit aberto em outra janela pode altera-lo durante a suite.

Vale para `pytest`; `python -m unittest` nao le este arquivo.
"""

import itertools
import os
import shutil
import tempfile
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = APP_ROOT / "prosodia.db"
_SQLITE_SIDE_FILES = ("", "-journal", "-wal", "-shm")

# No import do conftest, antes de qualquer modulo de teste: um import com
# efeito colateral no banco tambem cai na pasta temporaria. Sobrescreve um
# NENC_DB_PATH do ambiente de quem roda, que pode ser um banco de verdade.
_SUITE_DIRECTORY = Path(tempfile.mkdtemp(prefix="nenc-tests-"))
os.environ["NENC_DB_PATH"] = str(_SUITE_DIRECTORY / "suite.db")
_test_counter = itertools.count(1)


def _default_database_files():
    return [Path(str(DEFAULT_DATABASE) + suffix) for suffix in _SQLITE_SIDE_FILES]


def _existing_default_files():
    return {path for path in _default_database_files() if path.exists()}


def _remove(paths):
    for path in paths:
        try:
            path.unlink()
        except (FileNotFoundError, PermissionError):
            # No Windows, uma conexao esquecida aberta segura o arquivo; a
            # limpeza do fim da sessao tenta de novo para a pasta temporaria.
            pass


@pytest.fixture(autouse=True)
def _isolated_database(monkeypatch):
    database = _SUITE_DIRECTORY / "test-{}.db".format(next(_test_counter))
    monkeypatch.setenv("NENC_DB_PATH", str(database))
    before = _existing_default_files()
    yield
    _remove(Path(str(database) + suffix) for suffix in _SQLITE_SIDE_FILES)

    created = sorted(_existing_default_files() - before)
    if not created:
        return
    _remove(created)
    pytest.fail(
        "O teste criou o banco padrao ({}). Passe database_path ou fixe "
        "NENC_DB_PATH num banco descartavel.".format(
            ", ".join(path.name for path in created)
        ),
        pytrace=False,
    )


def pytest_unconfigure(config):
    shutil.rmtree(_SUITE_DIRECTORY, ignore_errors=True)
