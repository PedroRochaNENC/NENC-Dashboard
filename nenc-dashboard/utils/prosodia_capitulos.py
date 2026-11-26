"""
O relatório por tema em JSON, na parte que o código calcula
(docs/SPEC_RELATORIO_POR_TEMA.md, §3 e §5.1; Fase 1).

Entra a agregação da Análise Geral (prosodia_relatorio.agregar), os grupos de
subtemas de cada tag (prosodia_subtemas) e o que o cabeçalho pede; sai o
documento do esquema de relatorio_esquema, que relatorio_html desenha:

- um capítulo por tag aberta (e por categoria de subtemas fixos, na taxonomia
  mista), do mais citado ao menos, com "Outros" por último;
- os subcapítulos, um por grupo de subtemas, com a contagem por respondente
  (a união dos respondentes dos subtemas do grupo) e as polaridades unidas;
- as falas candidatas de cada capítulo, por id, com a máscara aplicada;
- as reclamações, em ordem determinística (faixa, respondentes, intensidade
  da voz, capítulo), com a etiqueta de voz só quando ela diz algo.

Os campos que a IA escreve (o que foi dito, crítica, hipótese, recomendação,
resumo, leituras) saem vazios: a Fase 2 os preenche. Até lá, cada
subcapítulo já leva até duas falas escolhidas aqui, para o documento ser
legível e conferível sem a IA. `markdown_do_relatorio` escreve o documento em
markdown, para a base de conhecimento e o .md.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from utils import prosodia_mascara as mascara
from utils import prosodia_relatorio as rel
from utils import relatorio_esquema as esquema
from utils.prosodia_subtemas import OUTROS_ASSUNTOS, Consolidacao
from utils.prosodia_temas import OUTROS, Taxonomia, chave_rotulo, juntar_polaridades

TITULO_OUTROS = "Avaliação geral e outros assuntos"
SELO_OUTROS = "Fora da taxonomia validada com a cliente"
OUTROS_ASSUNTOS_DE_OUTROS = "Outros assuntos"

# Falas candidatas (§5.3): até 12 por capítulo, de respondentes diferentes,
# com pelo menos 6 palavras e alguma informação além da cortesia.
MAX_CANDIDATAS = 12
MIN_PALAVRAS = 6
MIN_PALAVRAS_DE_CONTEUDO = 2
MAX_FALAS_POR_SUBCAPITULO = 2
MAX_FALAS_POR_RECLAMACAO = 2
_MAX_DEMAIS = 8

_ORDEM_DA_FAIXA = {rel.PADRAO: 0, rel.SINAL: 1, rel.MENCAO_ISOLADA: 2}
# A intensidade da voz na ordem das reclamações: irritação antes de resignação,
# antes dos demais (§3.6).
_IRRITACAO = "Irritação perceptível na voz"
_RESIGNACAO = "Crítica dita com resignação, sem irritação na voz"
_ORDEM_DA_INTENSIDADE = {_IRRITACAO: 0, _RESIGNACAO: 1}

_DIRECOES = {rel.ACIMA: "acima", rel.NO_HABITUAL: "habitual", rel.ABAIXO: "abaixo"}
_NIVEIS = {"alta": "alta", "média": "media", "baixa": "baixa"}

# Palavras que não contam como informação numa fala: cortesia, aprovação
# genérica e as de ligação. Fala só com elas é "gostei muito, tudo ótimo".
_SEM_INFORMACAO = frozenset("""
a o e é de da do das dos em na no nas nos um uma uns umas que se pra para com por pelo pela isso
essa esse esta este aqui ali lá eu ele ela nós gente a gente você vocês meu minha seu sua tá
está estou estava sou era foi ser ter tem tinha muito muita muitos muitas bem mais menos
só também sempre todo toda todos todas tudo nada né então assim aí ai bom boa bons boas
dia tarde noite obrigado obrigada olá ola oi gostei gosto gostando adoro adorei amo amei
excelente ótimo ótima otimo otima top legal parabéns parabens show perfeito perfeita
maravilhoso maravilhosa maravilha sensacional incrível incrivel demais nota dez mil sim
não nao pessoal academia smart fit smartfit unidade coisa coisas tipo assim acho achei
""".split())

_PALAVRA = re.compile(r"[^\W\d_]+", re.UNICODE)


def _inicial_maiuscula(texto: str) -> str:
    texto = " ".join(str(texto or "").split())
    return texto[:1].upper() + texto[1:]


def _palavras(texto: str) -> List[str]:
    return [p.casefold() for p in _PALAVRA.findall(texto or "")]


def palavras_de_conteudo(texto: str) -> int:
    return sum(1 for p in _palavras(texto) if len(p) >= 3 and p not in _SEM_INFORMACAO)


def fala_informativa(texto: str) -> bool:
    """A fala serve de candidata: 6 palavras ou mais, e não só cortesia ou aprovação genérica."""
    return len(_palavras(texto)) >= MIN_PALAVRAS and palavras_de_conteudo(texto) >= MIN_PALAVRAS_DE_CONTEUDO


# ---------------------------------------------------------------------------
# Contexto do documento
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cabecalho:
    """O que o documento precisa e a agregação não sabe."""

    projeto: str
    recorte: str
    gerado_em: str
    periodo: Tuple[str, ...] = ()
    modelo: str = ""
    contexto: str = ""
    taxonomia_status: Optional[str] = None
    taxonomia_da_ia: bool = False
    arquivo_do_anexo: str = ""
    # O que a máscara nunca troca (prosodia_mascara.preservar_do_projeto).
    preservar: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Peças internas
# ---------------------------------------------------------------------------

@dataclass
class _Ocorrencia:
    """Um trecho classificado num capítulo, com o grupo em que caiu."""

    respondente: str
    trecho: rel.Trecho
    leitura: rel.LeituraTrecho
    grupo: str
    detalhe: str
    critica: bool
    unidade: str

    @property
    def chave(self) -> Tuple[int, int]:
        return (self.trecho.audio_id, self.trecho.linha)

    @property
    def qualidade(self) -> Tuple[int, float]:
        nota = abs(self.trecho.nota_texto) if self.trecho.nota_texto is not None else 0.0
        return (min(palavras_de_conteudo(self.trecho.fala), 20), nota)


@dataclass
class _Grupo:
    """Um grupo de subtemas no recorte: quem tratou dele e como."""

    nome: str
    polaridade_por_respondente: Dict[str, str] = field(default_factory=dict)
    detalhes: Counter = field(default_factory=Counter)
    grafia: Dict[str, str] = field(default_factory=dict)
    quem_por_detalhe: Dict[str, Set[str]] = field(default_factory=dict)

    @property
    def respondentes(self) -> int:
        return len(self.polaridade_por_respondente)

    @property
    def polaridades(self) -> Dict[str, int]:
        return dict(Counter(self.polaridade_por_respondente.values()))


@dataclass
class _Fonte:
    """De onde sai um capítulo: uma tag, uma categoria de subtemas fixos, ou Outros."""

    tipo: str
    tema: str
    rotulos: Tuple[str, ...]
    linha: Optional[rel.LinhaTema] = None
    ordem: int = 0


class _Trechos:
    """Os trechos citados no documento, por id, cada um mascarado uma vez."""

    def __init__(self, preservar: Sequence[str]):
        self.preservar = list(preservar)
        self.por_chave: Dict[Tuple[int, int], str] = {}
        self.documento: Dict[str, dict] = {}
        # Falas já mostradas num capítulo: o seguinte prefere outras.
        self.citadas: Set[Tuple[int, int]] = set()

    def id_de(self, ocorrencia: _Ocorrencia, capitulo: str, subcapitulo: Optional[str]) -> str:
        if ocorrencia.chave in self.por_chave:
            return self.por_chave[ocorrencia.chave]
        identificador = "t{}".format(len(self.documento) + 1)
        texto = rel._fala(ocorrencia.trecho.fala)
        mascarado = mascara.mascarar_fala(texto, self.preservar, nomes=ocorrencia.critica)
        nota = ocorrencia.trecho.nota_texto
        self.documento[identificador] = {
            "alias": ocorrencia.trecho.alias,
            "tempo": rel._tempo(ocorrencia.trecho.inicio_s),
            "texto": mascarado,
            "nota": round(nota, 2) if nota is not None else None,
            "polaridade": ocorrencia.trecho.polaridade,
            "mascarado": mascarado != texto,
            "critica": ocorrencia.critica,
            "unidade": ocorrencia.unidade or None,
            "capitulo": capitulo,
            "subcapitulo": subcapitulo,
        }
        self.por_chave[ocorrencia.chave] = identificador
        return identificador


# ---------------------------------------------------------------------------
# Capítulos
# ---------------------------------------------------------------------------

def _fontes(agregacao: rel.Agregacao, taxonomia: Taxonomia) -> List[_Fonte]:
    """As fontes de capítulo com menção, do mais citado ao menos, com Outros no fim."""
    linhas = {tema.subtema: tema for tema in agregacao.temas}
    fontes: List[_Fonte] = []
    for ordem, (categoria, subtemas) in enumerate(taxonomia.categorias().items()):
        abertas = [s for s in subtemas if s.aberta and not s.alerta]
        fixos = [s for s in subtemas if not s.aberta and not s.alerta]
        for subtema in abertas:
            if subtema.rotulo in linhas:
                fontes.append(_Fonte("tag", subtema.rotulo, (subtema.rotulo,), linhas[subtema.rotulo], ordem))
        citados = tuple(s.rotulo for s in fixos if s.rotulo in linhas)
        if citados:
            fontes.append(_Fonte("categoria", categoria, citados, None, ordem))
    respondentes = {id(f): _respondentes_da_fonte(agregacao, f) for f in fontes}
    fontes.sort(key=lambda f: (-len(respondentes[id(f)]), f.ordem, chave_rotulo(f.tema)))
    if agregacao.outros is not None:
        fontes.append(_Fonte("outros", TITULO_OUTROS, (OUTROS,), agregacao.outros, len(fontes)))
    return fontes


def _respondentes_da_fonte(agregacao: rel.Agregacao, fonte: _Fonte) -> Set[str]:
    return {m.respondente for m in agregacao.mencoes if m.rotulo in fonte.rotulos}


def _polaridades_por_respondente(mencoes: Iterable[rel.MencaoDoAudio]) -> Dict[str, str]:
    por: Dict[str, str] = {}
    for mencao in mencoes:
        anterior = por.get(mencao.respondente)
        por[mencao.respondente] = juntar_polaridades(anterior, mencao.polaridade) if anterior else mencao.polaridade
    return por


def _agrupador(fonte: _Fonte, consolidacao: Consolidacao, agregacao: rel.Agregacao):
    """A função (rótulo, subtema) -> grupo de um capítulo.

    Na tag, o grupo da consolidação; na categoria, o subtema fixo; em Outros,
    o candidato a tag nova (3 ou mais respondentes no projeto), com a grafia
    do projeto, e o resto na sobra.
    """
    if fonte.tipo == "categoria":
        return lambda rotulo, detalhe: rotulo.split(">", 1)[-1].strip()
    if fonte.tipo == "outros":
        candidatos = {chave_rotulo(nome): nome for nome, _ in agregacao.candidatos}
        return lambda rotulo, detalhe: candidatos.get(chave_rotulo(detalhe), OUTROS_ASSUNTOS)
    tag = fonte.rotulos[0]
    return lambda rotulo, detalhe: consolidacao.nome_do_grupo(tag, detalhe)


def _voz(voz: rel.LeituraVoz, polaridade_predominante: str) -> dict:
    frase = rel.sinal_emocional(voz, polaridade_predominante)
    return {
        "avaliavel": voz.avaliavel,
        "respondentes_com_voz": voz.respondentes,
        "direcao": _DIRECOES.get(voz.direcao, "") if voz.avaliavel else "",
        "nivel": _NIVEIS.get(voz.nivel, "") if voz.avaliavel else "",
        "qualificador": voz.qualificador if voz.avaliavel else "",
        "frase": "" if frase in (rel.SEM_DESTAQUE, rel.SEM_VOZ) else frase,
    }


def _critica(trecho: rel.Trecho) -> bool:
    """A fala é crítica: menção negativa, ou trecho de nota até -0,2 numa menção mista (§3.6)."""
    if trecho.polaridade == "negativa":
        return True
    return trecho.polaridade == "mista" and trecho.nota_texto is not None and trecho.nota_texto <= -rel.LIMIAR_TEXTO


def _escolher(
    ocorrencias: Sequence[_Ocorrencia],
    quantas: int,
    excluir: Set[str] = frozenset(),
    evitar: Set[Tuple[int, int]] = frozenset(),
) -> List[_Ocorrencia]:
    """As melhores falas, uma por respondente; as de `evitar` (já citadas noutro capítulo) por último."""
    escolhidas: List[_Ocorrencia] = []
    vistos = set(excluir)
    for ocorrencia in sorted(
        ocorrencias,
        key=lambda o: (o.chave in evitar, -o.qualidade[0], -o.qualidade[1], o.trecho.alias, o.trecho.linha),
    ):
        if ocorrencia.respondente in vistos:
            continue
        vistos.add(ocorrencia.respondente)
        escolhidas.append(ocorrencia)
        if len(escolhidas) >= quantas:
            break
    return escolhidas


def _candidatas(ocorrencias: Sequence[_Ocorrencia], ordem_dos_grupos: Sequence[str]) -> List[_Ocorrencia]:
    """Até 12 falas do capítulo, de respondentes diferentes, revezando entre os grupos.

    O revezamento dá a cada subcapítulo pelo menos uma candidata antes de o
    primeiro ganhar a segunda. Se o capítulo tem crítica, pelo menos uma
    candidata é crítica.
    """
    validas = [o for o in ocorrencias if fala_informativa(o.trecho.fala)]
    por_grupo = {nome: [o for o in validas if o.grupo == nome] for nome in ordem_dos_grupos}
    for lista in por_grupo.values():
        lista.sort(key=lambda o: (-o.qualidade[0], -o.qualidade[1], o.trecho.alias, o.trecho.linha))
    escolhidas: List[_Ocorrencia] = []
    vistos: Set[str] = set()
    while len(escolhidas) < MAX_CANDIDATAS:
        avancou = False
        for nome in ordem_dos_grupos:
            lista = por_grupo[nome]
            while lista and lista[0].respondente in vistos:
                lista.pop(0)
            if lista and len(escolhidas) < MAX_CANDIDATAS:
                escolhida = lista.pop(0)
                escolhidas.append(escolhida)
                vistos.add(escolhida.respondente)
                avancou = True
        if not avancou:
            break
    criticas = [o for o in validas if o.critica]
    if criticas and not any(o.critica for o in escolhidas):
        melhor = _escolher(criticas, 1)[0]
        if len(escolhidas) >= MAX_CANDIDATAS:
            escolhidas.pop()
        escolhidas = [o for o in escolhidas if o.respondente != melhor.respondente] + [melhor]
    return escolhidas


def _demais(grupo: Optional[_Grupo], rotulo: str) -> Optional[str]:
    """A linha dos subtemas que não viraram subcapítulo, com os respondentes de cada um."""
    if grupo is None or not grupo.detalhes:
        return None
    itens = sorted(
        ((grupo.grafia[chave], len(grupo.quem_por_detalhe[chave])) for chave in grupo.detalhes),
        key=lambda item: (-item[1], chave_rotulo(item[0])),
    )
    partes = ["{} {}".format(nome, n) for nome, n in itens[:_MAX_DEMAIS]]
    if len(itens) > _MAX_DEMAIS:
        partes.append("e mais {}".format(len(itens) - _MAX_DEMAIS))
    return "{}: {}".format(rotulo, " · ".join(partes))


@dataclass
class _Capitulo:
    """O capítulo pronto e o que as reclamações precisam dele."""

    documento: dict
    fonte: _Fonte
    grupos: Dict[str, _Grupo]
    ordem_dos_grupos: List[str]
    ocorrencias: List[_Ocorrencia]
    mencoes: List[rel.MencaoDoAudio]
    subcapitulo_do_grupo: Dict[str, Tuple[str, str]]
    grupo_de: object  # (rótulo, subtema) -> nome do grupo


def _capitulo(
    numero: int,
    fonte: _Fonte,
    agregacao: rel.Agregacao,
    consolidacao: Consolidacao,
    trechos: _Trechos,
) -> _Capitulo:
    total = agregacao.n_respondentes
    cid = "c{}".format(numero)
    grupo_de = _agrupador(fonte, consolidacao, agregacao)
    mencoes = [m for m in agregacao.mencoes if m.rotulo in fonte.rotulos]
    por_respondente = _polaridades_por_respondente(mencoes)
    n = len(por_respondente)
    polaridades = dict(Counter(por_respondente.values()))
    predominante = rel.predominante(polaridades)

    # Grupos (subcapítulos e a sobra).
    grupos: Dict[str, _Grupo] = {}
    for mencao in mencoes:
        nome = grupo_de(mencao.rotulo, mencao.detalhe)
        grupo = grupos.setdefault(nome, _Grupo(nome))
        anterior = grupo.polaridade_por_respondente.get(mencao.respondente)
        grupo.polaridade_por_respondente[mencao.respondente] = (
            juntar_polaridades(anterior, mencao.polaridade) if anterior else mencao.polaridade
        )
        chave = chave_rotulo(mencao.detalhe) if fonte.tipo != "categoria" else chave_rotulo(nome)
        if chave:
            grupo.grafia.setdefault(chave, mencao.detalhe or nome)
            grupo.quem_por_detalhe.setdefault(chave, set()).add(mencao.respondente)
            grupo.detalhes[chave] = len(grupo.quem_por_detalhe[chave])
    sobra = OUTROS_ASSUNTOS
    nomeados = sorted(
        (g for nome, g in grupos.items() if nome != sobra),
        key=lambda g: (-g.respondentes, chave_rotulo(g.nome)),
    )
    ordem_dos_grupos = [g.nome for g in nomeados] + ([sobra] if sobra in grupos else [])

    # Faixa e voz do capítulo.
    if fonte.tipo == "tag":
        linha = fonte.linha
        faixa = linha.faixa
        voz = linha.voz
    else:
        faixa = rel.faixa(n, total) or "" if fonte.tipo == "categoria" else ""
        voz = rel.ler_voz(
            ((resp, leitura) for rotulo in fonte.rotulos for resp, _, leitura in agregacao.ocorrencias.get(rotulo, [])),
            predominante,
        )
    isolada = faixa == rel.MENCAO_ISOLADA

    # Ocorrências (trechos), sem as falas de alerta.
    unidade_do_audio = {a.audio_id: a.unidade for a in agregacao.recorte}
    ocorrencias: List[_Ocorrencia] = []
    vistas: Set[Tuple[int, int]] = set()
    for rotulo in fonte.rotulos:
        for respondente, trecho, leitura in agregacao.ocorrencias.get(rotulo, []):
            chave = (trecho.audio_id, trecho.linha)
            if chave in agregacao.falas_de_alerta or chave in vistas:
                continue
            vistas.add(chave)
            ocorrencias.append(_Ocorrencia(
                respondente, trecho, leitura,
                grupo_de(rotulo, trecho.detalhe),
                trecho.detalhe if fonte.tipo != "categoria" else rotulo.split(">", 1)[-1].strip(),
                _critica(trecho),
                unidade_do_audio.get(trecho.audio_id, ""),
            ))

    candidatas = _candidatas(ocorrencias, ordem_dos_grupos)

    documento = {
        "id": cid,
        "numero": numero,
        "tema": fonte.tema if fonte.tipo != "outros" else TITULO_OUTROS,
        "tipo": fonte.tipo,
        "faixa": faixa,
        "respondentes": n,
        "total": total,
        "polaridades": polaridades,
        "voz": _voz(voz, predominante),
        "delta": None,
        "selo": SELO_OUTROS if fonte.tipo == "outros" else None,
        "texto": None,
        "falas": [],
        "subcapitulos": [],
        "demais": None,
        "hipotese": None,
        "recomendacao": None,
        "acompanhar": None,
        "candidatas": [],
    }
    delta = agregacao.delta
    if fonte.tipo == "tag" and delta is not None and delta.exibir and fonte.linha.anteriores is not None:
        documento["delta"] = {
            "anteriores": fonte.linha.anteriores,
            "novos": fonte.linha.novos or 0,
            "total_anteriores": delta.anteriores,
            "total_novos": delta.novos,
            "tendencia": delta.sustenta_tendencia,
        }

    subcapitulo_do_grupo: Dict[str, Tuple[str, str]] = {}
    if isolada:
        documento["texto"] = ""
        escolhida = _escolher(candidatas, 1, evitar=trechos.citadas) or _escolher(ocorrencias, 1, evitar=trechos.citadas)
        documento["falas"] = [trechos.id_de(o, cid, None) for o in escolhida]
        trechos.citadas.update(o.chave for o in escolhida)
    else:
        if fonte.tipo == "outros":
            documento["texto"] = ""
        for indice, grupo in enumerate(nomeados, 1):
            sid = "{}.s{}".format(cid, indice)
            numero_sub = "{}.{}".format(numero, indice)
            subcapitulo_do_grupo[grupo.nome] = (sid, numero_sub)
            # Até a IA escolher (Fase 2): a melhor crítica, se houver, e a melhor das
            # demais, de preferência falas que nenhum capítulo anterior mostrou. Sem
            # candidata no grupo (falas curtas), vale a melhor fala dele.
            do_grupo = [o for o in candidatas if o.grupo == grupo.nome] or _escolher(
                [o for o in ocorrencias if o.grupo == grupo.nome], 1, evitar=trechos.citadas
            )
            criticas = [o for o in do_grupo if o.critica]
            primeira = _escolher(criticas, 1, evitar=trechos.citadas)
            falas = (primeira + _escolher(
                [o for o in do_grupo if not o.critica], MAX_FALAS_POR_SUBCAPITULO,
                excluir={o.respondente for o in primeira}, evitar=trechos.citadas,
            ))[:MAX_FALAS_POR_SUBCAPITULO]
            trechos.citadas.update(o.chave for o in falas)
            criticas_do_grupo = [o for o in ocorrencias if o.grupo == grupo.nome and o.critica]
            tem_critica = bool(criticas_do_grupo)
            # A crítica traz pelo menos uma fala (§15, item 5.3), mesmo curta.
            falas_criticas = _escolher(criticas, MAX_FALAS_POR_SUBCAPITULO) or _escolher(criticas_do_grupo, 1)
            documento["subcapitulos"].append({
                "id": sid,
                "numero": numero_sub,
                "nome": _inicial_maiuscula(grupo.nome),
                "respondentes": grupo.respondentes,
                "polaridades": grupo.polaridades,
                "subtemas": [grupo.grafia[c] for c, _ in sorted(grupo.detalhes.items(), key=lambda i: (-i[1], i[0]))],
                "o_que_foi_dito": "",
                "critica": {
                    "resumo": "",
                    "falas": [trechos.id_de(o, cid, sid) for o in falas_criticas],
                } if tem_critica else None,
                "falas": [trechos.id_de(o, cid, sid) for o in falas],
            })
        documento["demais"] = _demais(
            grupos.get(sobra), OUTROS_ASSUNTOS_DE_OUTROS if fonte.tipo == "outros" else OUTROS_ASSUNTOS
        )
    documento["candidatas"] = [
        trechos.id_de(o, cid, subcapitulo_do_grupo.get(o.grupo, (None,))[0]) for o in candidatas
    ]
    return _Capitulo(documento, fonte, grupos, ordem_dos_grupos, ocorrencias, mencoes, subcapitulo_do_grupo, grupo_de)


# ---------------------------------------------------------------------------
# Reclamações
# ---------------------------------------------------------------------------

@dataclass
class _Reclamacao:
    titulo: str
    capitulo: _Capitulo
    origem: str
    ordem_no_capitulo: int
    criticos: Set[str]
    ocorrencias_criticas: List[_Ocorrencia]
    unidades: Counter
    # O grupo de origem quando a reclamação é um subcapítulo; vazio na sobra e no capítulo isolado.
    grupo: str = ""


def _unidades_ordenadas(contagem: Counter) -> List[str]:
    return [u for u, _ in sorted(contagem.items(), key=lambda i: (-i[1], chave_rotulo(i[0]))) if u]


def _reclamacoes_do_capitulo(capitulo: _Capitulo, mistas_sem_trecho: List[int]) -> List[_Reclamacao]:
    """As unidades de reclamação de um capítulo: cada subcapítulo, cada subtema da sobra, ou o capítulo isolado."""
    doc = capitulo.documento
    numero = str(doc["numero"])
    isolada = doc["faixa"] == rel.MENCAO_ISOLADA

    def chave_da_unidade(grupo: str, detalhe: str) -> Tuple[str, str, int]:
        if isolada:
            return ("capitulo", numero, 0)
        if grupo in capitulo.subcapitulo_do_grupo:
            _, origem = capitulo.subcapitulo_do_grupo[grupo]
            return ("grupo:" + grupo, origem, capitulo.ordem_dos_grupos.index(grupo))
        return ("sobra:" + chave_rotulo(detalhe), numero, len(capitulo.ordem_dos_grupos) + 1)

    negativos: Dict[Tuple[str, str, int], Set[str]] = {}
    mistos: Dict[Tuple[str, str, int], Set[str]] = {}
    titulos: Dict[Tuple[str, str, int], Counter] = {}
    unidades: Dict[Tuple[str, str, int], Dict[str, Set[str]]] = {}
    for mencao in capitulo.mencoes:
        grupo = capitulo.grupo_de(mencao.rotulo, mencao.detalhe)
        chave = chave_da_unidade(grupo, mencao.detalhe)
        if mencao.polaridade == "negativa":
            negativos.setdefault(chave, set()).add(mencao.respondente)
        elif mencao.polaridade == "mista":
            mistos.setdefault(chave, set()).add(mencao.respondente)
        if mencao.polaridade in ("negativa", "mista"):
            titulos.setdefault(chave, Counter())[mencao.detalhe or ""] += 1
            unidades.setdefault(chave, {}).setdefault(mencao.respondente, set()).add(mencao.unidade)

    criticas_por_chave: Dict[Tuple[str, str, int], List[_Ocorrencia]] = {}
    for ocorrencia in capitulo.ocorrencias:
        if ocorrencia.critica:
            criticas_por_chave.setdefault(chave_da_unidade(ocorrencia.grupo, ocorrencia.detalhe), []).append(ocorrencia)

    reclamacoes = []
    for chave in sorted(set(negativos) | set(mistos), key=lambda c: (c[2], c[0])):
        criticas = criticas_por_chave.get(chave, [])
        com_trecho_critico = {o.respondente for o in criticas}
        criticos = set(negativos.get(chave, set()))
        mistos_aqui = mistos.get(chave, set()) - criticos
        criticos |= mistos_aqui & com_trecho_critico
        mistas_sem_trecho.append(len(mistos_aqui - com_trecho_critico))
        if not criticos:
            continue
        tipo, origem, ordem = chave
        if tipo == "capitulo":
            mais_citado = titulos[chave].most_common(1)[0][0] if titulos.get(chave) else ""
            titulo = mais_citado if mais_citado and len(titulos[chave]) == 1 else doc["tema"]
        elif tipo.startswith("grupo:"):
            titulo = tipo[len("grupo:"):]
        else:
            titulo = next((o.detalhe for o in criticas if o.detalhe), "") or (
                titulos[chave].most_common(1)[0][0] if titulos.get(chave) else ""
            ) or doc["tema"]
        contagem_unidades: Counter = Counter()
        for respondente in criticos:
            for unidade in unidades.get(chave, {}).get(respondente, set()):
                contagem_unidades[unidade] += 1
        reclamacoes.append(_Reclamacao(
            _inicial_maiuscula(titulo), capitulo, origem, ordem, criticos,
            [o for o in criticas if o.respondente in criticos], contagem_unidades,
            tipo[len("grupo:"):] if tipo.startswith("grupo:") else "",
        ))
    return reclamacoes


def _montar_reclamacoes(
    capitulos: Sequence[_Capitulo], agregacao: rel.Agregacao, trechos: _Trechos
) -> dict:
    total = agregacao.n_respondentes
    mistas_sem_trecho: List[int] = []
    todas = [r for c in capitulos for r in _reclamacoes_do_capitulo(c, mistas_sem_trecho)]

    def etiqueta(reclamacao: _Reclamacao) -> Optional[str]:
        voz = rel.ler_voz(((o.respondente, o.leitura) for o in reclamacao.ocorrencias_criticas), rel.NEGATIVO)
        if not voz.avaliavel:
            return None
        sinal = rel.sinal_emocional(voz, rel.NEGATIVO)
        return None if sinal in (rel.SEM_DESTAQUE, rel.SEM_VOZ) else sinal

    etiquetas = {id(r): etiqueta(r) for r in todas}

    def ordem(reclamacao: _Reclamacao):
        faixa = rel.faixa(len(reclamacao.criticos), total)
        return (
            _ORDEM_DA_FAIXA.get(faixa, 3),
            -len(reclamacao.criticos),
            _ORDEM_DA_INTENSIDADE.get(etiquetas[id(reclamacao)], 2),
            reclamacao.capitulo.documento["numero"],
            reclamacao.ordem_no_capitulo,
            chave_rotulo(reclamacao.titulo),
        )

    itens: List[dict] = []
    isoladas: List[dict] = []
    com_unidade = agregacao.por_unidade
    for reclamacao in sorted(todas, key=ordem):
        cid = reclamacao.capitulo.documento["id"]
        sid = reclamacao.capitulo.subcapitulo_do_grupo.get(reclamacao.grupo, (None,))[0]
        informativas = [o for o in reclamacao.ocorrencias_criticas if fala_informativa(o.trecho.fala)]
        escolhidas = _escolher(informativas or reclamacao.ocorrencias_criticas, MAX_FALAS_POR_RECLAMACAO)
        falas = [trechos.id_de(o, cid, sid) for o in escolhidas]
        unidades = _unidades_ordenadas(reclamacao.unidades)
        faixa = rel.faixa(len(reclamacao.criticos), total)
        if faixa == rel.MENCAO_ISOLADA:
            if not falas:
                continue
            isoladas.append({
                "tema": reclamacao.capitulo.documento["tema"],
                "assunto": reclamacao.titulo,
                # "; " entre unidades: o rótulo de uma unidade já pode ter " · ".
                "unidade": "; ".join(unidades) if com_unidade and unidades else None,
                "fala": falas[0],
                "origem": reclamacao.origem,
            })
            continue
        itens.append({
            "posicao": len(itens) + 1,
            "titulo": reclamacao.titulo,
            "tema": reclamacao.capitulo.documento["tema"],
            "origem": reclamacao.origem,
            "respondentes": len(reclamacao.criticos),
            "total": total,
            "faixa": faixa,
            "critica": "",
            "falas": falas,
            "etiqueta_voz": etiquetas[id(reclamacao)],
            "unidades": unidades if com_unidade else [],
        })
    return {
        "abertura": "",
        "itens": itens,
        "isoladas": isoladas,
        "mistas_sem_trecho_critico": sum(mistas_sem_trecho),
    }


# ---------------------------------------------------------------------------
# Demais partes
# ---------------------------------------------------------------------------

_DESCRICAO_VINCULO = "Os elogios mais fortes do projeto, somando o que foi dito e o tom de voz."
_DESCRICAO_RISCO = (
    "Relatos negativos ditos com menos energia na voz que a maioria do projeto: risco de abandono silencioso."
)


def _grupo_da_matriz(chave: str, titulo: str, descricao: str, grupo: Optional[rel.GrupoMatriz],
                     com_unidade: bool, preservar: Sequence[str]) -> dict:
    linhas = []
    total = grupo.total if grupo is not None else 0
    for linha in (grupo.linhas if grupo is not None else ()):
        linhas.append({
            "alias": linha.alias,
            "unidade": (linha.unidade or None) if com_unidade else None,
            "temas": "; ".join(linha.temas) or "—",
            # A frase é a paráfrase da classificação; no grupo de crítica, sem nomes.
            "frase": mascara.mascarar_fala(linha.frase, preservar, nomes=chave == "risco") or "—",
        })
    voz = None
    if grupo is not None and grupo.voz_media is not None and total:
        voz = (
            "Tom de voz no grupo: {} de {} com tom mais positivo que a média do projeto e {} com tom "
            "mais negativo.".format(grupo.voz_acima, total, grupo.voz_abaixo)
        )
    return {
        "chave": chave,
        "titulo": titulo,
        "descricao": descricao,
        "linhas": linhas,
        "fora_da_tabela": max(0, total - len(linhas)),
        "voz_no_grupo": voz,
        "perfil": "",
        "acao": "",
    }


def _matriz(agregacao: rel.Agregacao, preservar: Sequence[str]) -> dict:
    matriz = agregacao.matriz
    if not matriz.avaliavel:
        return {"avaliavel": False, "grupos": []}
    grupos = [_grupo_da_matriz("vinculo", rel.VINCULO, _DESCRICAO_VINCULO, matriz.vinculo,
                               agregacao.por_unidade, preservar)]
    if matriz.corte_ativacao is not None:
        grupos.append(_grupo_da_matriz("risco", rel.RISCO, _DESCRICAO_RISCO, matriz.risco,
                                       agregacao.por_unidade, preservar))
    return {"avaliavel": True, "grupos": grupos}


def notas_metodologicas(agregacao: rel.Agregacao) -> List[str]:
    respondente = (
        "Respondente é a unidade de contagem: áudios do mesmo número de WhatsApp contam como uma pessoa, que "
        "trata do tema se qualquer áudio dela tratar. Por isso as contagens dos temas não se somam."
        if agregacao.tipo_projeto == rel.PESQUISA_OPINIAO
        else "Respondente é a unidade de contagem: cada áudio é um respondente. Um respondente pode tratar de "
        "vários temas, por isso as contagens dos temas não se somam."
    )
    return [
        respondente,
        "Faixas: 1 a 2 respondentes, menção isolada; 3 a 4, sinal; 5 ou mais e pelo menos 10% do total, "
        "padrão. A comparação com a análise anterior só aparece com 5 ou mais respondentes novos e, com "
        "menos de 20, vale como variação observada, e não como tendência.",
        "Os subcapítulos reúnem os subtemas que a IA identificou dentro de cada tema; o respondente conta uma "
        "vez no subcapítulo, mesmo que tenha citado mais de um subtema dele.",
        "Nas falas críticas, nomes de pessoas e dados pessoais e financeiros foram trocados por marcas como "
        "[funcionário] e [valor].",
        "As tabelas de voz, o índice combinado, as divergências entre voz e texto e as médias por áudio "
        "estão no Anexo técnico do projeto.",
    ]


def _alertas(agregacao: rel.Agregacao, preservar: Sequence[str]) -> List[dict]:
    return [
        {
            "alias": alerta.alias,
            "tema": alerta.tema,
            "unidade": alerta.unidade or None,
            "chegada": alerta.chegada or None,
            "tempo": rel._tempo(alerta.inicio_s) if alerta.inicio_s is not None else None,
            "evidencia": mascara.mascarar_fala(alerta.evidencia, preservar) or "—",
        }
        for alerta in agregacao.alertas
    ]


# ---------------------------------------------------------------------------
# O documento
# ---------------------------------------------------------------------------

def montar_relatorio(
    agregacao: rel.Agregacao,
    taxonomia: Taxonomia,
    consolidacao: Consolidacao,
    cabecalho: Cabecalho,
) -> dict:
    """O relatório por tema em JSON, com os campos da IA vazios.

    Só para taxonomia com tag aberta: a sem tags segue no relatório v2
    (§2, item 25), e aqui levanta ValueError.
    """
    if not taxonomia.tem_tags_abertas:
        raise ValueError("O relatório por capítulos exige taxonomia com tag aberta; a sem tags segue na v2.")
    trechos = _Trechos(cabecalho.preservar)
    capitulos = [
        _capitulo(numero, fonte, agregacao, consolidacao, trechos)
        for numero, fonte in enumerate(_fontes(agregacao, taxonomia), 1)
    ]
    reclamacoes = _montar_reclamacoes(capitulos, agregacao, trechos)
    termometro = agregacao.termometro
    alertas = taxonomia.alertas
    documento = {
        "versao": esquema.VERSAO,
        "cabecalho": {
            "projeto": cabecalho.projeto,
            "recorte": cabecalho.recorte,
            "periodo": list(cabecalho.periodo),
            "respondentes": agregacao.n_respondentes,
            "audios": len(agregacao.recorte),
            "unidades": len(agregacao.unidades),
            "exploratoria": agregacao.exploratoria,
            "gerado_em": cabecalho.gerado_em,
            "modelo": cabecalho.modelo,
            "contexto": cabecalho.contexto,
        },
        "alertas": _alertas(agregacao, cabecalho.preservar),
        "resumo": "",
        "termometro": {
            "nota": termometro.nota,
            "media": round(termometro.media, 2) if termometro.media is not None else None,
            "positivos": termometro.positivos,
            "neutros": termometro.neutros,
            "negativos": termometro.negativos,
            "avaliaveis": termometro.respondentes,
            "leitura": "",
        },
        "como_ler": [
            {"observamos": observamos, "significa": significa}
            for observamos, significa in rel.como_ler_itens(
                agregacao, cabecalho.taxonomia_status, cabecalho.taxonomia_da_ia, por_capitulos=True
            )[:3]
        ],
        "visao_geral": [
            {
                "capitulo": c.documento["id"],
                "tema": c.documento["tema"],
                "respondentes": c.documento["respondentes"],
                "total": c.documento["total"],
                "polaridades": c.documento["polaridades"],
            }
            for c in capitulos
        ],
        "sem_mencao": [rotulo for rotulo in agregacao.sem_mencao if rotulo not in alertas],
        "capitulos": [c.documento for c in capitulos],
        "trechos": trechos.documento,
        "transversais": [],
        "matriz": _matriz(agregacao, cabecalho.preservar),
        "reclamacoes": reclamacoes,
        "notas": notas_metodologicas(agregacao),
        "anexo": {"arquivo": cabecalho.arquivo_do_anexo},
        "problemas": list(consolidacao.problemas),
    }
    return documento


def erros(documento: dict) -> List[str]:
    """Tudo o que o documento tem de fora do contrato: esquema e referências."""
    return esquema.erros_do_esquema(documento) + esquema.erros_de_referencia(documento)


# ---------------------------------------------------------------------------
# Markdown (base de conhecimento e .md)
# ---------------------------------------------------------------------------

def _polaridades_texto(polaridades: Dict[str, int]) -> str:
    return rel.texto_polaridades(polaridades) or "—"


def _fala_md(trechos: Dict[str, dict], identificador: str) -> str:
    trecho = trechos.get(identificador)
    if not trecho:
        return ""
    referencia = trecho["alias"] + (", " + trecho["tempo"] if trecho.get("tempo") else "")
    return "> “{}” ({})".format(trecho["texto"], referencia)


def _falas_md(trechos: Dict[str, dict], ids: Iterable[str]) -> List[str]:
    return [linha for linha in (_fala_md(trechos, i) for i in ids or []) if linha]


def markdown_do_relatorio(documento: dict) -> str:
    """O relatório em markdown, na ordem da §3.1, com os números e as falas do JSON.

    Campo de texto da IA vazio não entra: o markdown da Fase 1 é o esqueleto
    que o código calcula.
    """
    cab = documento["cabecalho"]
    trechos = documento.get("trechos", {})
    partes: List[str] = ["# {}".format(cab["projeto"])]
    linha = [cab["recorte"], "{} respondentes".format(cab["respondentes"])]
    if cab.get("periodo"):
        linha.insert(1, " a ".join(cab["periodo"]))
    linha.append("gerado em {}".format(cab["gerado_em"]))
    partes.append(" · ".join(linha))
    if cab.get("exploratoria"):
        partes.append(
            "**Leitura exploratória:** {} respondentes, abaixo de {}. Os números descrevem esta amostra e não "
            "se estendem ao público.".format(cab["respondentes"], rel.MIN_RESPONDENTES)
        )

    if documento.get("alertas"):
        partes.append("## Alertas críticos")
        partes.append(rel._tabela(
            ["Áudio", "Tema", "Unidade", "Chegada", "Trecho", "O que foi relatado"],
            ([a["alias"], a["tema"], a.get("unidade") or "—", a.get("chegada") or "—", a.get("tempo") or "—",
              a["evidencia"]] for a in documento["alertas"]),
        ))

    partes.append("## Resumo Executivo")
    if documento.get("resumo"):
        partes.append(documento["resumo"])
    termometro = documento["termometro"]
    if termometro.get("nota") is not None:
        partes.append("**Termômetro de Experiência:** {} (de -5 a +5) · {} positivos, {} neutros e {} negativos.".format(
            "{:+d}".format(termometro["nota"]) if termometro["nota"] else "0",
            termometro.get("positivos", 0), termometro.get("neutros", 0), termometro.get("negativos", 0),
        ))
    if termometro.get("leitura"):
        partes.append(termometro["leitura"])
    if documento.get("como_ler"):
        partes.append("**Como ler esta análise**")
        partes.append("\n".join(
            "- **O que observamos:** {} **O que isso significa:** {}".format(i["observamos"], i["significa"])
            for i in documento["como_ler"]
        ))

    partes.append("## Visão geral dos temas")
    partes.append(rel._tabela(
        ["Capítulo", "Tema", "Respondentes", "Polaridades"],
        (
            [str(c["numero"]), c["tema"], "{}/{}".format(c["respondentes"], c["total"]),
             _polaridades_texto(c["polaridades"])]
            for c in documento["capitulos"]
        ),
    ))
    if documento.get("sem_mencao"):
        partes.append(
            "Sem menção nesta amostra: {}. Numa coleta aberta, a ausência de menções não indica que o tema "
            "seja irrelevante.".format(", ".join(documento["sem_mencao"]))
        )

    for cap in documento["capitulos"]:
        partes.append("## {}. {}".format(cap["numero"], cap["tema"]))
        ficha = ["{}/{} ({}%)".format(cap["respondentes"], cap["total"], rel._percentual(cap["respondentes"], cap["total"]))]
        if cap.get("faixa"):
            ficha.append(cap["faixa"])
        ficha.append(_polaridades_texto(cap["polaridades"]))
        if cap.get("selo"):
            ficha.append(cap["selo"])
        partes.append(" · ".join(ficha))
        if cap["voz"].get("frase"):
            partes.append("Voz: " + cap["voz"]["frase"] + ".")
        elif not cap["voz"].get("avaliavel"):
            partes.append("Voz: não avaliável ({} com voz).".format(cap["voz"].get("respondentes_com_voz", 0)))
        if cap.get("delta"):
            d = cap["delta"]
            partes.append("Anteriores → novos: {}/{} → {}/{}{}.".format(
                d["anteriores"], d["total_anteriores"], d["novos"], d["total_novos"],
                "" if d["tendencia"] else " (variação observada, não tendência)",
            ))
        if cap.get("texto"):
            partes.append(cap["texto"])
        partes.extend(_falas_md(trechos, cap.get("falas")))
        for sub in cap.get("subcapitulos", []):
            partes.append("### {} {}".format(sub["numero"], sub["nome"]))
            partes.append("{} · {}".format(
                rel._plural(sub["respondentes"], "respondente", "respondentes"), _polaridades_texto(sub["polaridades"])
            ))
            if sub.get("o_que_foi_dito"):
                partes.append(sub["o_que_foi_dito"])
            partes.extend(_falas_md(trechos, sub.get("falas")))
            critica = sub.get("critica") or {}
            if critica.get("resumo"):
                partes.append("**Crítica:** " + critica["resumo"])
            extras = [i for i in critica.get("falas", []) if i not in (sub.get("falas") or [])]
            partes.extend(_falas_md(trechos, extras))
        if cap.get("demais"):
            partes.append(cap["demais"])
        if cap.get("hipotese"):
            h = cap["hipotese"]
            partes.append("**Hipótese:** {} *Como validar:* {}".format(h["texto"], h["como_validar"]))
        if cap.get("recomendacao"):
            r = cap["recomendacao"]
            partes.append("**Recomendação** [{} · {} · {}]: {} *Como medir:* {} *Responde a:* {}".format(
                r["tipo"], r["driver"], r["area"], r["texto"], r["como_medir"],
                ", ".join(_numero_do_sub(documento, s) for s in r["responde_a"]),
            ))
        if cap.get("acompanhar"):
            a = cap["acompanhar"]
            partes.append("**O que acompanhar:** {} *Como investigar:* {}".format(a["texto"], a["como_investigar"]))

    if documento.get("transversais"):
        partes.append("## Leituras transversais")
        partes.append("\n".join(
            "- {} *Como validar:* {}".format(t["texto"], t["como_validar"]) for t in documento["transversais"]
        ))

    matriz = documento.get("matriz") or {}
    if matriz.get("avaliavel"):
        partes.append("## Matriz de Destaques")
        for grupo in matriz.get("grupos", []):
            partes.append("**{}** — {}".format(grupo["titulo"], grupo["descricao"]))
            if grupo["linhas"]:
                partes.append(rel._tabela(
                    ["Áudio", "Unidade", "Temas", "Em uma frase"],
                    ([l["alias"], l.get("unidade") or "—", l["temas"], l["frase"]] for l in grupo["linhas"]),
                ))
            else:
                partes.append("Nenhum áudio do recorte neste grupo.")
            if grupo.get("fora_da_tabela"):
                partes.append("Mais {} no grupo, fora da tabela.".format(
                    rel._plural(grupo["fora_da_tabela"], "áudio", "áudios")
                ))
            for chave, rotulo in (("voz_no_grupo", ""), ("perfil", "*Perfil:* "), ("acao", "*Ação:* ")):
                if grupo.get(chave):
                    partes.append(rotulo + grupo[chave])

    reclamacoes = documento.get("reclamacoes") or {}
    if reclamacoes.get("itens") or reclamacoes.get("isoladas"):
        partes.append("## Reclamações e feedbacks negativos")
        if reclamacoes.get("abertura"):
            partes.append(reclamacoes["abertura"])
        for item in reclamacoes.get("itens", []):
            cabeca = "### {}. {}".format(item["posicao"], item["titulo"])
            partes.append(cabeca)
            ficha = [item["tema"], "{}/{} respondentes com crítica".format(item["respondentes"], item["total"]),
                     item["faixa"], "origem: " + item["origem"]]
            if item.get("etiqueta_voz"):
                ficha.append(item["etiqueta_voz"])
            if item.get("unidades"):
                ficha.append("unidades: " + ", ".join(item["unidades"]))
            partes.append(" · ".join(ficha))
            if item.get("critica"):
                partes.append(item["critica"])
            partes.extend(_falas_md(trechos, item.get("falas")))
        if reclamacoes.get("isoladas"):
            partes.append("**Menções isoladas**")
            partes.append(rel._tabela(
                ["Tema", "Assunto", "Unidade", "Fala", "Origem"],
                (
                    [i["tema"], i["assunto"], i.get("unidade") or "—",
                     "“{}” ({})".format(trechos[i["fala"]]["texto"], trechos[i["fala"]]["alias"]) if i["fala"] in trechos else "—",
                     i["origem"]]
                    for i in reclamacoes["isoladas"]
                ),
            ))

    if documento.get("notas"):
        partes.append("## Notas metodológicas")
        partes.append("\n".join("- " + nota for nota in documento["notas"]))
    return "\n\n".join(p for p in partes if p) + "\n"


def _numero_do_sub(documento: dict, sid: str) -> str:
    for cap in documento["capitulos"]:
        for sub in cap.get("subcapitulos", []):
            if sub["id"] == sid:
                return sub["numero"]
    return sid
