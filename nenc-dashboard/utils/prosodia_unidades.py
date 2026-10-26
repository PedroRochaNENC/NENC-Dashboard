"""Unidades da organização: as regras que não dependem do banco nem da tela.

A unidade é da organização e o áudio a herda do QR code por onde entrou
(docs/SPEC_UNIDADES_E_TAGS.md, §3). Aqui ficam a validação dos campos, a
leitura da planilha de importação, o plano do que a importação cria ou
atualiza e a sugestão de unidades a partir dos QR codes.

Só biblioteca padrão: `prosodia_db` importa este módulo, e os scripts de
manutenção rodam no Python do servidor, que não tem pandas.
"""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils.prosodia_temas import chave_rotulo

CRIAR = "criar"
ATUALIZAR = "atualizar"

# Cabeçalhos aceitos na planilha, comparados sem caixa, acento nem espaço.
_COLUNAS = {
    "codigo": "codigo",
    "cod": "codigo",
    "codigo interno": "codigo",
    "nome": "nome",
    "unidade": "nome",
    "nome da unidade": "nome",
    "cidade": "cidade",
    "municipio": "cidade",
    "uf": "uf",
    "estado": "uf",
}


def _texto(valor) -> str:
    """Texto limpo de uma célula; NaN do pandas e None viram vazio."""
    if valor is None or valor != valor:  # NaN não é igual a si mesmo
        return ""
    texto = " ".join(str(valor).split())
    # Código numérico lido como número pela planilha: "12.0" volta a "12".
    if texto.endswith(".0") and texto[:-2].isdigit():
        texto = texto[:-2]
    return texto


def normalizar_unidade(nome, codigo="", cidade="", uf="") -> Dict[str, Optional[str]]:
    """Os quatro campos como o banco os grava, ou ValueError com o motivo.

    Campo opcional vazio vira None, para o índice único do código ignorá-lo.
    """
    nome = _texto(nome)
    if not nome:
        raise ValueError("Informe o nome da unidade.")
    uf = _texto(uf).upper()
    if uf and (len(uf) != 2 or not uf.isalpha()):
        raise ValueError("A UF tem duas letras, como SP ou RJ: {!r} não serve.".format(uf))
    return {
        "nome": nome,
        "codigo": _texto(codigo) or None,
        "cidade": _texto(cidade) or None,
        "uf": uf or None,
    }


def ler_planilha(linhas: Iterable[dict]) -> Tuple[List[Dict[str, Optional[str]]], List[str]]:
    """Unidades da planilha e os problemas encontrados, linha a linha.

    `linhas` são os registros da planilha (`DataFrame.to_dict("records")`).
    Linha em branco é ignorada. Linha com problema fica fora e vira uma
    mensagem com o número que ela tem na planilha (o cabeçalho é a 1). Código
    repetido dentro do arquivo fica só na primeira vez.
    """
    unidades: List[Dict[str, Optional[str]]] = []
    erros: List[str] = []
    codigos = set()
    for numero, linha in enumerate(linhas, start=2):
        campos: Dict[str, str] = {}
        for coluna, valor in (linha or {}).items():
            chave = _COLUNAS.get(chave_rotulo(coluna))
            if chave and chave not in campos:
                campos[chave] = _texto(valor)
        if not any(campos.values()):
            continue
        try:
            unidade = normalizar_unidade(
                campos.get("nome"), campos.get("codigo"), campos.get("cidade"), campos.get("uf")
            )
        except ValueError as erro:
            erros.append("Linha {}: {}".format(numero, erro))
            continue
        codigo = unidade["codigo"]
        if codigo and codigo in codigos:
            erros.append("Linha {}: o código {} já apareceu antes na planilha.".format(numero, codigo))
            continue
        if codigo:
            codigos.add(codigo)
        unidades.append(unidade)
    if not unidades and not erros:
        erros.append("Nenhuma unidade na planilha. As colunas esperadas são codigo, nome, cidade e uf.")
    return unidades, erros


def planejar_importacao(
    unidades: Sequence[Dict[str, Optional[str]]], existentes: Sequence[dict]
) -> List[dict]:
    """O que a importação faz com cada unidade: criar ou atualizar qual.

    Com código, vale a unidade cadastrada com o mesmo código; sem ela, a de
    mesmo nome (sem caixa nem acento) que ainda não tem código. É assim que a
    planilha completa as unidades criadas pela sugestão dos QR codes, que
    nascem sem código. Linha sem código casa pelo nome com qualquer unidade e
    deixa o código dela como está. O resto é criado. Cada unidade cadastrada
    é atualizada uma vez só.
    """
    por_codigo = {u["codigo"]: u for u in existentes if u.get("codigo")}
    por_nome: Dict[str, List[dict]] = {}
    for unidade in existentes:
        por_nome.setdefault(chave_rotulo(unidade.get("nome")), []).append(unidade)

    plano = []
    usadas = set()
    for unidade in unidades:
        alvo = por_codigo.get(unidade["codigo"]) if unidade["codigo"] else None
        if alvo is None:
            alvo = next(
                (
                    candidata
                    for candidata in por_nome.get(chave_rotulo(unidade["nome"]), [])
                    if candidata["id"] not in usadas
                    and (not unidade["codigo"] or not candidata.get("codigo"))
                ),
                None,
            )
        if alvo is not None and alvo["id"] in usadas:
            alvo = None
        item = dict(unidade, acao=CRIAR if alvo is None else ATUALIZAR, unidade_id=None)
        if alvo is not None:
            usadas.add(alvo["id"])
            item["unidade_id"] = alvo["id"]
            # Sem código na planilha, a unidade fica com o que já tinha.
            item["codigo"] = unidade["codigo"] or alvo.get("codigo")
        plano.append(item)
    return plano


def propor_pelos_qrs(
    qrs: Iterable[dict], vinculados: Iterable[Tuple[int, str]], existentes: Sequence[dict]
) -> List[dict]:
    """Uma proposta de unidade para cada QR code ainda sem unidade.

    `qrs` traz `project_id`, `project_name`, `code` e `name` de cada QR da
    API. O nome proposto é o do QR, porque é ele que diz onde o cartaz está; a
    equipe edita antes de confirmar. Quando já existe uma unidade com esse
    nome, a proposta aponta para ela em vez de criar outra.
    """
    ligados = {(int(projeto), str(codigo)) for projeto, codigo in vinculados}
    por_nome = {chave_rotulo(u.get("nome")): u for u in existentes}
    propostas = []
    vistos = set()
    for qr in qrs:
        codigo = _texto(qr.get("code"))
        chave = (int(qr["project_id"]), codigo)
        if not codigo or chave in ligados or chave in vistos:
            continue
        vistos.add(chave)
        nome_qr = _texto(qr.get("name"))
        existente = por_nome.get(chave_rotulo(nome_qr)) if nome_qr else None
        propostas.append({
            "project_id": chave[0],
            "project_name": _texto(qr.get("project_name")),
            "qr_code": codigo,
            "qr_name": nome_qr,
            "nome": existente["nome"] if existente else (nome_qr or codigo),
            "unidade_id": existente["id"] if existente else None,
        })
    return propostas


def agrupar_sugestoes(confirmadas: Iterable[dict], existentes: Sequence[dict]) -> List[dict]:
    """As propostas confirmadas, agrupadas pela unidade que vão formar.

    Duas propostas com o mesmo nome (sem caixa nem acento) formam uma unidade
    só, com os dois QR codes: é como a equipe junta o cartaz da recepção e o do
    vestiário da mesma academia. Nome de unidade já cadastrada liga a ela.
    Devolve, por unidade, o nome, o id (None quando é para criar) e os pares
    (projeto, código do QR).
    """
    por_nome = {chave_rotulo(u.get("nome")): u for u in existentes}
    grupos: Dict[str, dict] = {}
    for proposta in confirmadas:
        nome = _texto(proposta.get("nome"))
        if not nome:
            continue
        chave = chave_rotulo(nome)
        if chave not in grupos:
            existente = por_nome.get(chave)
            grupos[chave] = {
                "nome": existente["nome"] if existente else nome,
                "unidade_id": existente["id"] if existente else None,
                "qrs": [],
            }
        par = (int(proposta["project_id"]), _texto(proposta.get("qr_code")))
        if par not in grupos[chave]["qrs"]:
            grupos[chave]["qrs"].append(par)
    return list(grupos.values())


def rotulo_unidade(unidade: Optional[dict]) -> str:
    """Nome da unidade com o código, para listas de escolha."""
    if not unidade:
        return "Sem unidade"
    nome = unidade.get("nome") or unidade.get("unidade_nome") or ""
    codigo = unidade.get("codigo") or unidade.get("unidade_codigo")
    return "{} · {}".format(nome, codigo) if codigo else nome
