"""
Remocao do que um projeto ou audio deixou na base de conhecimento.

Apagar um projeto no banco nunca tirou nada da OpenAI: a transcricao da
entrevista continuava indexada, e continuava sendo citada pela analise de
outros projetos. Estas funcoes sao o outro lado da ingestao e sao chamadas pela
camada de dados logo depois do DELETE.

Sao best-effort de proposito. A verdade e o banco: se a OpenAI estiver fora do
ar, apagar um projeto nao pode falhar por causa disso. O que escapar aqui e
recolhido depois por `scripts/cleanup_orphan_kb_files.py`.

A excecao e `remove_document`, que vem do botao da tela de Base de
Conhecimento: ali o erro tem que chegar a quem clicou.
"""

from typing import Iterable, Optional

from openai import NotFoundError

from utils.ai_provider import (
    get_openai_client,
    get_prosodia_vector_store_id,
    get_vector_store_id,
    list_vector_store_documents,
)
from utils.kb_attributes import belongs_to_project


def _store(module_key: str = "prosodia"):
    """Cliente e base do modulo na organizacao ativa, ou (None, None).

    Cada modulo tem o proprio vector store; limpar o projeto da Jornada na base
    do NencBoost nao acharia nada e deixaria o material do projeto indexado.

    O cliente vem primeiro porque nao depende de sessao: sem chave configurada
    nao ha o que limpar, e a funcao volta sem tocar em autorizacao — e assim que
    testes e scripts sem OpenAI atravessam uma exclusao sem efeito nenhum.
    """

    client = get_openai_client()
    if client is None:
        return None, None
    try:
        if module_key == "jornada_compra":
            vector_store_id = get_vector_store_id()
        else:
            vector_store_id = get_prosodia_vector_store_id()
    except Exception:
        return None, None
    if not vector_store_id:
        return None, None
    return client, vector_store_id


def _delete_files(client, vector_store_id: str, file_ids: Iterable[str]) -> int:
    """Tira os arquivos da base e da conta. Devolve quantos sairam."""

    removed = 0
    for file_id in dict.fromkeys(file_id for file_id in file_ids if file_id):
        try:
            client.vector_stores.files.delete(
                vector_store_id=vector_store_id, file_id=file_id
            )
        except Exception:
            # Pode ja ter saido da base; ainda vale apagar o arquivo em si, que
            # e o que continua sendo cobrado.
            pass
        try:
            client.files.delete(file_id)
            removed += 1
        except Exception:
            pass
    if removed:
        try:
            list_vector_store_documents.clear()
        except Exception:
            pass
    return removed


def _project_file_ids(
    client,
    vector_store_id: str,
    project_id: int,
    session_id: Optional[str] = None,
) -> list:
    file_ids = []
    for vector_store_file in client.vector_stores.files.list(
        vector_store_id=vector_store_id
    ):
        attributes = getattr(vector_store_file, "attributes", None) or {}
        if not belongs_to_project(attributes, project_id):
            continue
        if session_id and str(attributes.get("session_id") or "") != str(session_id):
            continue
        file_ids.append(vector_store_file.id)
    return file_ids


def remove_documents_for_project(
    project_id: Optional[int],
    extra_file_ids: Iterable[str] = (),
    module_key: str = "prosodia",
) -> int:
    """Remove da base do modulo tudo que pertence a um projeto."""

    client, vector_store_id = _store(module_key)
    if client is None:
        return 0
    try:
        file_ids = list(extra_file_ids)
        if project_id:
            file_ids.extend(_project_file_ids(client, vector_store_id, project_id))
        return _delete_files(client, vector_store_id, file_ids)
    except Exception:
        return 0


def remove_documents_for_audio(
    project_id: Optional[int],
    session_id: Optional[str],
    extra_file_ids: Iterable[str] = (),
) -> int:
    """Remove a transcricao, a prosodia e as analises de uma entrevista.

    Os ids guardados no banco cobrem so a transcricao e o JSON; o resto do
    material da sessao (qualidade, analise de IA) so e alcancavel pelo atributo.
    """

    client, vector_store_id = _store("prosodia")
    if client is None:
        return 0
    try:
        file_ids = list(extra_file_ids)
        if project_id and session_id:
            file_ids.extend(
                _project_file_ids(client, vector_store_id, project_id, session_id)
            )
        return _delete_files(client, vector_store_id, file_ids)
    except Exception:
        return 0


def remove_files(file_ids: Iterable[str], module_key: str = "prosodia") -> int:
    """Remove arquivos avulsos, pelos ids que o banco guardava."""

    file_ids = [file_id for file_id in file_ids if file_id]
    if not file_ids:
        return 0
    client, vector_store_id = _store(module_key)
    if client is None:
        return 0
    try:
        return _delete_files(client, vector_store_id, file_ids)
    except Exception:
        return 0


def remove_document(vector_store_id: str, file_id: str) -> None:
    """Tira um documento da base a pedido da tela, e levanta o que der errado.

    O 404 nao e erro: arquivo que a OpenAI nao encontra mais ja esta removido.
    Era ele que travava o botao "Remover" — a linha saia do vector store, o
    `files.delete` seguinte estourava com "No such File object", e a tela
    mostrava o erro sem limpar a listagem, ainda exibindo o documento que
    acabara de sair.

    O estado que leva a isso: o arquivo sumiu da conta e a linha continuou no
    vector store (uma limpeza anterior em que o desanexo falhou e o
    `files.delete` passou, ou `scripts/cleanup_orphan_kb_files.py`). Na
    listagem esse documento aparece com o id no lugar do nome, porque o nome
    vinha do arquivo que nao existe mais.
    """

    client = get_openai_client()
    if client is None:
        raise RuntimeError("OpenAI API key not configured. Set OPENAI_API_KEY in .env")

    try:
        try:
            client.vector_stores.files.delete(
                vector_store_id=vector_store_id, file_id=file_id
            )
        except NotFoundError:
            pass
        # O arquivo em si e o que continua sendo cobrado: vale apagar mesmo
        # quando a linha no vector store ja tinha saido.
        try:
            client.files.delete(file_id)
        except NotFoundError:
            pass
    finally:
        # Qualquer tentativa deixa a listagem em cache suspeita, inclusive a
        # que falhou no meio do caminho.
        try:
            list_vector_store_documents.clear()
        except Exception:
            pass


def delete_vector_store(vector_store_id: Optional[str]) -> bool:
    """Apaga um vector store inteiro — o caso do Teste Sensorial.

    La a base e do projeto, entao apagar o projeto e apagar a base. Sem isto o
    store fica na conta da OpenAI sem nenhuma referencia no banco: ninguem ve,
    e continua sendo cobrado.
    """

    if not vector_store_id:
        return False
    client = get_openai_client()
    if client is None:
        return False
    try:
        client.vector_stores.delete(vector_store_id)
    except Exception:
        return False
    try:
        list_vector_store_documents.clear()
    except Exception:
        pass
    return True
