"""
Geração da Análise Geral v2 do NencBoost (fase 5 de docs/SPEC_ANALISE_GERAL.md).

As etapas do pipeline da spec (§4), na ordem em que a página as mostra:

1. a taxonomia: a do projeto ou, com o campo vazio, a provisória que a IA
   propõe e que fica gravada no projeto;
2. a classificação dos áudios do recorte que ainda não têm uma válida;
3. a agregação em código, com a comparação com a análise anterior;
4. o texto da IA, com a montagem do relatório e a verificação das referências;
5. a gravação da análise com o snapshot dos temas.

A página entra com o projeto, os áudios, os dataframes do projeto inteiro e
as duas chamadas à IA, montadas por responder_da_classificacao e
gerador_do_relatorio. Ela cuida só da tela: o progresso, os avisos e o envio
para a base de conhecimento.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Collection, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from utils import ai_provider, prosodia_db
from utils import prosodia_capitulos as capitulos
from utils import prosodia_escrita as escrita
from utils import prosodia_ficha as ficha
from utils import prosodia_mascara as mascara
from utils import prosodia_overview as overview
from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel
from utils import prosodia_subtemas as subtemas
from utils import prosodia_temas as temas
from utils import relatorio_markdown, relatorio_pdf
from utils.kb_attributes import build_kb_filter
from utils.prosodia_project_types import PESQUISA_OPINIAO, normalize_project_type

ETAPAS = ("Taxonomia", "Classificação", "Agregação", "Relatório", "Gravação")

# A taxonomia provisória e a classificação têm formato fechado: pouca variação
# ajuda a mesma fala a cair no mesmo subtema de uma geração para a outra.
TEMPERATURA_CLASSIFICACAO = 0.2

# avisar(etapa, texto): etapa de 1 a len(ETAPAS), para a tela acompanhar.
Aviso = Callable[[int, str], None]


class TaxonomiaSemSubtema(ValueError):
    """O campo da taxonomia tem texto, mas nenhuma linha no formato."""


class SemClassificacao(RuntimeError):
    """Nenhum áudio do recorte saiu classificado: não há o que contar."""


@dataclass
class Geracao:
    """A análise salva e o que a tela mostra junto dela."""

    analise_id: int
    relatorio: rel.Relatorio
    agregacao: rel.Agregacao
    taxonomia_da_ia: bool
    classificacao: temas.ResumoDaClassificacao
    # O relatório por tema em JSON, quando o projeto o tem ligado; None na v2.
    documento: Optional[dict] = None


def _plural(n: int, singular: str, plural: str) -> str:
    return "{} {}".format(n, singular if n == 1 else plural)


# ---------------------------------------------------------------------------
# As duas chamadas à IA
# ---------------------------------------------------------------------------

def responder_da_classificacao(provedor: str, modelo: str) -> Tuple[temas.Responder, str]:
    """A chamada da taxonomia provisória e da classificação, e o modelo dela.

    Na OpenAI vale o modelo barato, como na cobertura de perguntas; nos demais
    provedores, o escolhido na tela. O cliente sai daqui, na thread da página:
    a classificação chama o `responder` em outras threads, sem o contexto do
    Streamlit.
    """
    modelo_da_classificacao = ai_provider.classification_model(provedor, modelo)
    cliente = ai_provider.get_provider_client(provedor)
    if cliente is None:
        raise RuntimeError(
            "{} não configurado. Defina {} no .env e reinicie o app.".format(
                ai_provider.PROVIDER_LABELS.get(provedor, provedor),
                ai_provider.PROVIDER_ENV_KEYS.get(provedor, "a chave"),
            )
        )

    def responder(sistema: str, usuario: str, max_tokens: int) -> str:
        return ai_provider.chat_completion(
            provedor,
            modelo_da_classificacao,
            sistema,
            [{"role": "user", "content": usuario}],
            temperature=TEMPERATURA_CLASSIFICACAO,
            max_tokens=max_tokens,
            client=cliente,
        )

    return responder, modelo_da_classificacao


def gerador_do_relatorio(
    provedor: str, modelo: str, project_id: int, vector_store_id: Optional[str] = None
) -> rel.GerarTexto:
    """A chamada que escreve o relatório, com o modelo da tela.

    A base de conhecimento entra com a literatura e o material do projeto, mas
    sem as análises dele, individuais e gerais (§11): a comparação com a onda
    anterior vem do banco, e o Sumário Executivo de cada individual, do texto
    salvo.
    """
    filtro = build_kb_filter(project_id, incluir_analises=False)
    # O cliente sai daqui, na thread da página: no relatório por tema, os
    # capítulos chamam a IA em outras threads, sem o contexto do Streamlit.
    cliente = ai_provider.get_provider_client(provedor)

    def gerar(**kwargs) -> dict:
        return ai_provider.generate_analysis(
            provedor, modelo, vector_store_id=vector_store_id, kb_filter=filtro, client=cliente, **kwargs
        )

    return gerar


def responder_da_consolidacao(provedor: str, modelo: str) -> temas.Responder:
    """A chamada que agrupa os subtemas (§4), com o modelo da escrita: os nomes viram títulos."""
    cliente = ai_provider.get_provider_client(provedor)

    def responder(sistema: str, usuario: str, max_tokens: int) -> str:
        return ai_provider.chat_completion(
            provedor, modelo, sistema, [{"role": "user", "content": usuario}],
            temperature=TEMPERATURA_CLASSIFICACAO, max_tokens=max_tokens, client=cliente,
        )

    return responder


# ---------------------------------------------------------------------------
# O que a página entrega
# ---------------------------------------------------------------------------

def recorte_da_pagina(
    qrs: Sequence[str],
    qrs_do_projeto: Sequence[str],
    periodo: Optional[Tuple[date, date]],
    origens: Sequence[str],
    descricao: str,
    unidades: Sequence[str] = (),
    unidades_do_projeto: Sequence[str] = (),
) -> rel.Recorte:
    """O filtro da página como o snapshot grava.

    Marcar todos os QR codes, todas as origens ou todas as unidades é o mesmo
    que não filtrar, como na descrição do recorte. O período fica registrado
    sempre, mas o Δ não o compara: é ele que muda de uma onda para a outra.
    """
    qr = () if not qrs or set(qrs) == set(qrs_do_projeto) else tuple(sorted(qrs))
    origem = () if not origens or set(origens) == set(overview.ORIGENS) else tuple(sorted(origens))
    unidade = () if not unidades or set(unidades) == set(unidades_do_projeto) else tuple(sorted(unidades))
    datas = tuple(dia.isoformat() for dia in periodo) if periodo else ()
    return rel.Recorte(qr, origem, datas, descricao or "", unidade)


# ---------------------------------------------------------------------------
# Arquivos e geração por unidade
# ---------------------------------------------------------------------------

TODAS_AS_UNIDADES = "Todas as unidades"
VARIAS_UNIDADES = "Varias unidades"


def _trecho_do_nome(texto: str) -> str:
    """Texto para nome de arquivo: sem acento, palavras unidas por "_"."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(c)
    )
    return "_".join(re.findall(r"[A-Za-z0-9]+", sem_acento))


def nome_do_arquivo(projeto: str, unidade: str, dia: date, extensao: str = "pdf") -> str:
    """Analise_<Projeto>_<Unidade>_<data>.<extensão>, sem acento nem espaço.

    A data vai como AAAA-MM-DD, para os arquivos ficarem em ordem. Unidade com
    código sai com ele, que diferencia unidades de mesmo nome.
    """
    partes = [p for p in ("Analise", _trecho_do_nome(projeto), _trecho_do_nome(unidade)) if p]
    return "{}_{}.{}".format("_".join(partes), dia.isoformat(), extensao)


def nome_do_pacote(projeto: str, dia: date) -> str:
    """Analises_<Projeto>_<data>.zip, o pacote da geração por unidade."""
    return "_".join(p for p in ("Analises", _trecho_do_nome(projeto), dia.isoformat()) if p) + ".zip"


def unidade_do_recorte(recorte: rel.Recorte) -> str:
    """A unidade que vai no nome do arquivo de uma análise salva."""
    if len(recorte.unidade) == 1:
        return recorte.unidade[0]
    return TODAS_AS_UNIDADES if not recorte.unidade else VARIAS_UNIDADES


def nome_da_analise(projeto: str, analise: dict, extensao: str = "pdf") -> str:
    """O nome do arquivo de uma análise salva: a unidade do recorte e o dia dela.

    Análise sem snapshot (anterior à v2) ou sem filtro de unidade sai como
    "Todas as unidades".
    """
    recorte = rel.Recorte.de_dict((analise.get("temas") or {}).get("recorte"))
    criada = rel.ler_data(analise.get("created_at"))
    return nome_do_arquivo(
        projeto, unidade_do_recorte(recorte), criada.date() if criada else date.today(), extensao
    )


def nome_do_anexo(projeto: str, unidade: str, dia: date) -> str:
    """Anexo_Tecnico_<Projeto>_<data>.pdf, com a unidade quando a análise é de uma só.

    A regra de nome_do_arquivo. O anexo do lote é o do projeto: vai sem unidade
    (spec do relatório por tema, §9).
    """
    trecho_da_unidade = "" if unidade in ("", TODAS_AS_UNIDADES) else _trecho_do_nome(unidade)
    partes = [p for p in ("Anexo_Tecnico", _trecho_do_nome(projeto), trecho_da_unidade) if p]
    return "{}_{}.pdf".format("_".join(partes), dia.isoformat())


def nome_do_anexo_da_analise(projeto: str, analise: dict) -> str:
    """O nome do Anexo técnico de uma análise salva, pela unidade do recorte e pelo dia dela."""
    recorte = rel.Recorte.de_dict((analise.get("temas") or {}).get("recorte"))
    criada = rel.ler_data(analise.get("created_at"))
    return nome_do_anexo(projeto, unidade_do_recorte(recorte), criada.date() if criada else date.today())


@dataclass(frozen=True)
class AnaliseDoLote:
    """Uma análise da geração por unidade: a unidade, os áudios e o recorte gravado."""

    unidade: str
    ids: Tuple[int, ...]
    recorte: rel.Recorte


def planejar_por_unidade(
    audios_projeto: Sequence[dict],
    periodo: Optional[Tuple[date, date]],
    origens: Sequence[str],
    limites: Tuple[date, date],
) -> List[AnaliseDoLote]:
    """Uma análise para cada unidade com áudio no recorte, e a de todas por último.

    O recorte é o período e a origem da página; QR code e unidade, não, porque
    é a unidade que muda de uma análise para a outra. A de todas leva também
    os áudios sem unidade, e fica por último para ser a análise mais recente
    do projeto, a que a página mostra.
    """
    base = overview.filter_audios(audios_projeto, None, periodo, origens)
    opcoes = overview.unidade_options(audios_projeto)
    lote = []
    for unidade in overview.unidade_options(base):
        if unidade == overview.SEM_UNIDADE:
            continue
        ids = tuple(int(a["id"]) for a in base if overview.unidade_label(a) == unidade)
        descricao = overview.filter_description([], periodo, origens, limites, [unidade])
        lote.append(AnaliseDoLote(
            unidade, ids, recorte_da_pagina([], [], periodo, origens, descricao, [unidade], opcoes)
        ))
    descricao = overview.filter_description([], periodo, origens, limites)
    lote.append(AnaliseDoLote(
        TODAS_AS_UNIDADES,
        tuple(int(a["id"]) for a in base),
        recorte_da_pagina([], [], periodo, origens, descricao),
    ))
    return lote


def contexto_do_projeto(projeto: dict) -> dict:
    """O cadastro do projeto que a IA recebe (§9) e que o PDF mostra."""
    return {
        "nome": projeto.get("name") or "",
        "especialidade": projeto.get("especialidade") or "",
        "historico": projeto.get("historico") or "",
        "problemas": projeto.get("problemas") or "",
        "briefing": projeto.get("briefing_text") or "",
    }


def linhas_por_audio(
    audios_projeto: Sequence[dict], transcricao_projeto: Optional[pd.DataFrame]
) -> Dict[int, Tuple[temas.Linha, ...]]:
    """A transcrição numerada de cada áudio do projeto, por audio_id.

    A transcrição do projeto junta a de todos os áudios, cada linha com o
    session_id do seu. Áudio sem transcrição fica com a tupla vazia, e a
    agregação o conta como sem fala.
    """
    por_sessao: Dict[str, Tuple[temas.Linha, ...]] = {}
    if (
        transcricao_projeto is not None
        and not transcricao_projeto.empty
        and "session_id" in transcricao_projeto.columns
    ):
        sessoes = transcricao_projeto["session_id"].astype(str)
        for sessao, bloco in transcricao_projeto.groupby(sessoes, sort=False):
            por_sessao[str(sessao)] = tuple(temas.linhas_da_transcricao(bloco))
    return {int(a["id"]): por_sessao.get(str(a.get("session_id") or ""), ()) for a in audios_projeto}


def legenda(audios_projeto: Sequence[dict], ids: Collection[int]) -> List[Tuple[str, str, str, str]]:
    """Áudio, sessão, QR code e chegada dos áudios `ids`, na ordem dos aliases.

    A sessão traz o telefone de quem mandou o áudio. Com a legenda fora do
    relatório, é na tela que a equipe acha quem é o "Áudio 07". O alias é o
    do projeto inteiro, o mesmo que o relatório usou.
    """
    nomes = rel.aliases(rel.AudioDoProjeto(int(a["id"]), str(a.get("session_id") or "")) for a in audios_projeto)
    escolhidos = {int(i) for i in ids}
    return [
        (nomes[int(a["id"])], str(a.get("session_id") or ""), overview.qr_label(a), overview.entry_text(a))
        for a in sorted(audios_projeto, key=lambda a: int(a["id"]))
        if int(a["id"]) in escolhidos
    ]


# ---------------------------------------------------------------------------
# PDF e Anexo técnico (docs/SPEC_RELATORIO_POR_TEMA.md, §9)
# ---------------------------------------------------------------------------

TODOS_OS_AUDIOS = "Todos os áudios do projeto"
_TITULO_DAS_LIMITACOES = "#### Limitações e cortes aplicados"


def subtitulo_do_recorte(recorte: rel.Recorte) -> str:
    """O recorte na capa do PDF: a descrição gravada, ou "Todos os áudios do projeto"."""
    descricao = (recorte.descricao or "").strip()
    return descricao[:1].upper() + descricao[1:] if descricao else TODOS_OS_AUDIOS


def _data(quando: Optional[datetime]) -> str:
    return (quando or datetime.now()).strftime("%d/%m/%Y")


def _celula(texto) -> str:
    return " ".join(str(texto or "").replace("|", "/").split()) or "—"


def audios_e_unidades_markdown(audios_projeto: Sequence[dict], ids: Collection[int]) -> str:
    """Áudio NN, unidade, QR code e chegada dos áudios `ids`, sem a sessão, que traz o telefone.

    É a coluna de unidade do Anexo (§9): o alias é o do projeto inteiro, e esta
    tabela diz de que unidade é cada "Áudio NN" que as outras tabelas citam.
    """
    nomes = rel.aliases(rel.AudioDoProjeto(int(a["id"]), str(a.get("session_id") or "")) for a in audios_projeto)
    escolhidos = {int(i) for i in ids}
    linhas = [
        "| {} | {} | {} | {} |".format(
            nomes[int(a["id"])],
            _celula(overview.unidade_label(a) if a.get("unidade_id") else ""),
            _celula(overview.qr_label(a)),
            _celula(overview.entry_text(a)),
        )
        for a in sorted(audios_projeto, key=lambda a: int(a["id"]))
        if int(a["id"]) in escolhidos
    ]
    if not linhas:
        return "Nenhum áudio no recorte."
    return "\n".join(["| Áudio | Unidade | QR code | Chegada |", "|---|---|---|---|"] + linhas)


def anexo_do_texto(texto: Optional[str], audios_projeto: Sequence[dict], ids: Collection[int]) -> str:
    """O Anexo técnico de uma análise: o apêndice do texto mais a tabela de áudios e unidades.

    Vazio quando o texto não tem "Apêndice técnico" (análise anterior à v2). A
    tabela entra antes das limitações, que fecham o anexo.
    """
    _, apendice = relatorio_markdown.separar_anexo(texto)
    if not apendice:
        return ""
    tabela = "#### Áudios e unidades\n\n" + audios_e_unidades_markdown(audios_projeto, ids)
    if _TITULO_DAS_LIMITACOES in apendice:
        return apendice.replace(_TITULO_DAS_LIMITACOES, tabela + "\n\n" + _TITULO_DAS_LIMITACOES, 1)
    return apendice + "\n\n" + tabela


def pdf_da_analise(
    projeto: dict,
    texto: Optional[str],
    citacoes: Sequence[dict],
    modelo: str,
    criada_em: Optional[datetime],
    recorte: rel.Recorte,
) -> bytes:
    """O PDF de uma análise em markdown (v2 ou antiga), sem o apêndice, que vai no anexo."""
    return relatorio_pdf.legado_pdf(
        texto,
        projeto=str(projeto.get("name") or ""),
        subtitulo=subtitulo_do_recorte(recorte),
        gerado_em=_data(criada_em),
        modelo=modelo or "",
        contexto=contexto_do_projeto(projeto),
        citacoes=list(citacoes or []),
        com_anexo=bool(relatorio_markdown.separar_anexo(texto)[1]),
    )


def anexo_pdf_da_analise(
    projeto: dict,
    texto: Optional[str],
    audios_projeto: Sequence[dict],
    ids: Collection[int],
    modelo: str,
    criada_em: Optional[datetime],
    recorte: rel.Recorte,
) -> Optional[bytes]:
    """O PDF do Anexo técnico de uma análise, ou None quando ela não tem apêndice."""
    anexo = anexo_do_texto(texto, audios_projeto, ids)
    if not anexo:
        return None
    return relatorio_pdf.anexo_pdf(
        anexo,
        projeto=str(projeto.get("name") or ""),
        subtitulo=subtitulo_do_recorte(recorte),
        gerado_em=_data(criada_em),
        modelo=modelo or "",
    )


def arquivos_da_analise(
    projeto: dict, analise: dict, audios_projeto: Sequence[dict]
) -> Tuple[bytes, Optional[bytes]]:
    """(PDF, PDF do anexo ou None) de uma análise salva, com o recorte e a data dela."""
    snapshot = analise.get("temas") or {}
    recorte = rel.Recorte.de_dict(snapshot.get("recorte"))
    criada = rel.ler_data(analise.get("created_at"))
    texto = analise.get("analysis_text") or ""
    modelo = analise.get("model") or ""
    if analise.get("relatorio"):
        # Relatório por tema: o PDF sai do JSON; o anexo, do apêndice no fim do texto.
        pdf = relatorio_pdf.relatorio_pdf(analise["relatorio"])
    else:
        pdf = pdf_da_analise(projeto, texto, analise.get("citations") or [], modelo, criada, recorte)
    anexo = anexo_pdf_da_analise(projeto, texto, audios_projeto, snapshot.get("audios") or [], modelo, criada, recorte)
    return pdf, anexo


# ---------------------------------------------------------------------------
# A geração
# ---------------------------------------------------------------------------

def _audio_do_projeto(
    audio: dict,
    linhas: Dict[int, Tuple[temas.Linha, ...]],
    validas: Dict[int, dict],
    conteudos: Optional[Dict[int, Optional[str]]] = None,
) -> rel.AudioDoProjeto:
    audio_id = int(audio["id"])
    return rel.AudioDoProjeto(
        audio_id=audio_id,
        session_id=str(audio.get("session_id") or ""),
        linhas=linhas.get(audio_id, ()),
        classificacao=validas.get(audio_id),
        criado_em=rel.ler_data(audio.get("created_at")),
        qr=overview.qr_label(audio),
        chegada=overview.entry_text(audio),
        conteudo=(conteudos or {}).get(audio_id),
        unidade=overview.unidade_label(audio) if audio.get("unidade_id") else "",
        duracao_s=float(audio["duration_seconds"]) if audio.get("duration_seconds") else None,
    )


def temas_gravados_do_audio(projeto: dict, audio_id: int, transcricao: Optional[pd.DataFrame]) -> str:
    """"Temas tocados" da ficha montados da classificação gravada do áudio (spec do relatório por tema, §10).

    Só na Pesquisa de Opinião, e só quando a classificação ainda vale para a
    taxonomia e a transcrição de agora: a ficha repete a contagem da Geral,
    sem pedir à IA. Vazio nos demais casos, e a IA escreve a seção.
    """
    tipo = normalize_project_type(projeto.get("tipo_projeto"))
    taxonomia = temas.parse_taxonomia(projeto.get("taxonomia"))
    if tipo != PESQUISA_OPINIAO or not taxonomia.subtemas:
        return ""
    salva = prosodia_db.get_audio_temas_for_project(int(projeto["id"])).get(int(audio_id))
    linhas = temas.linhas_da_transcricao(transcricao) if transcricao is not None and not transcricao.empty else []
    if not salva or not linhas or salva.get("chave") != temas.chave_classificacao(taxonomia, tipo, linhas):
        return ""
    return ficha.temas_do_codigo(salva.get("temas"), taxonomia.alertas)


def analise_para_a_geral(audio_id: int) -> Optional[str]:
    """O texto individual que a Geral lê de um áudio (spec do relatório por tema, §10).

    A ficha de contexto mais recente; sem ficha, a análise mais recente, como
    antes da Fase 4. A análise aprofundada gerada depois de uma ficha não a
    tira da Geral: ela não traz Sumário Executivo nem ficha.
    """
    analises = prosodia_db.get_analyses(audio_id)
    for analise in analises:
        if ficha.eh_ficha(analise.get("analysis_text")):
            return analise["analysis_text"]
    return analises[0].get("analysis_text") if analises else None


def conteudos_do_projeto(project_id: int) -> Dict[int, Optional[str]]:
    """A assinatura do arquivo de cada áudio do projeto, para juntar as cópias.

    get_audios_for_interviews traz só metadado; o prosodia_json vem do banco
    aqui, e só o hash dele segue para a agregação.
    """
    return {
        audio_id: rel.assinatura_do_conteudo(blobs.get("prosodia_json"))
        for audio_id, blobs in prosodia_db.get_audio_blobs_for_project(project_id).items()
    }


def gerar_analise_geral(
    projeto: dict,
    audios_projeto: Sequence[dict],
    ids_recorte: Collection[int],
    recorte: rel.Recorte,
    transcricao_projeto: Optional[pd.DataFrame],
    sinc_projeto: Optional[pd.DataFrame],
    responder: temas.Responder,
    modelo_da_classificacao: str,
    gerar: rel.GerarTexto,
    modelo: str,
    *,
    perguntas_ativacao: Optional[Sequence[dict]] = None,
    incluir_legenda: bool = False,
    avisar: Optional[Aviso] = None,
    ao_classificar: Optional[Callable[[int, int], None]] = None,
    responder_consolidacao: Optional[temas.Responder] = None,
) -> Geracao:
    """Gera e salva a Análise Geral do recorte, nas etapas da spec (§4).

    `audios_projeto` são as linhas de get_audios_for_interviews do projeto
    inteiro, e `ids_recorte`, os ids dos áudios do filtro. Os dataframes são a
    transcrição e o Sincronizado normalizado do projeto inteiro: os aliases, a
    régua da voz e os terços da Matriz usam todos os áudios, e a contagem, só
    o recorte. `modelo` é o que fica gravado com a análise.

    Áudio cuja classificação falha fica fora da contagem e volta na geração
    seguinte; se nenhum do recorte sair classificado, a geração para em
    SemClassificacao, sem chamar a IA do relatório. Só o campo vazio leva à
    taxonomia proposta pela IA: com texto fora do formato, a geração para em
    TaxonomiaSemSubtema, para a proposta não apagar o que a equipe escreveu.
    """
    avisar = avisar or (lambda etapa, texto: None)
    project_id = int(projeto["id"])
    tipo = normalize_project_type(projeto.get("tipo_projeto"))
    contexto = contexto_do_projeto(projeto)
    ids = {int(i) for i in ids_recorte}
    linhas = linhas_por_audio(audios_projeto, transcricao_projeto)

    # 1. Taxonomia
    texto_da_taxonomia = str(projeto.get("taxonomia") or "").strip()
    taxonomia = temas.parse_taxonomia(texto_da_taxonomia)
    if texto_da_taxonomia and not taxonomia.subtemas:
        raise TaxonomiaSemSubtema(
            "A taxonomia do projeto não tem nenhuma linha válida (\"Categoria > Subtema\" ou \"Tag\"). "
            "Corrija na Preparação, ou apague o campo para a IA propor uma provisória."
        )
    taxonomia_status = projeto.get("taxonomia_status")
    taxonomia_da_ia = not taxonomia.subtemas
    if taxonomia_da_ia:
        avisar(1, "O projeto não tem taxonomia: a IA propõe uma provisória a partir das falas.")
        taxonomia = temas.propor_taxonomia(
            [linhas[audio_id] for audio_id in sorted(linhas)], tipo, contexto, responder
        )
        prosodia_db.set_project_taxonomy(
            project_id, temas.formatar_taxonomia(taxonomia), temas.TAXONOMIA_PROVISORIA
        )
        taxonomia_status = temas.TAXONOMIA_PROVISORIA
        avisar(1, "{} propostos e salvos no projeto como taxonomia provisória, para revisar na Preparação.".format(
            _plural(len(taxonomia.subtemas), "subtema", "subtemas")
        ))
    else:
        status = temas.TAXONOMIA_STATUS_LABELS.get(
            taxonomia_status or "", temas.TAXONOMIA_STATUS_LABELS[temas.TAXONOMIA_PROVISORIA]
        )
        avisar(1, "Taxonomia do projeto, {}: {}.".format(
            status[0].lower() + status[1:], _plural(len(taxonomia.subtemas), "subtema", "subtemas")
        ))

    # 2. Classificação dos áudios do recorte, com o cache de audio_temas
    salvas = prosodia_db.get_audio_temas_for_project(project_id)
    versao = temas.versao_taxonomia(taxonomia)
    emergentes_conhecidos = [
        (rotulo, categoria)
        for rotulo, categoria, _ in temas.contar_emergentes(item.get("temas") for item in salvas.values())
    ]
    # Na taxonomia com tag aberta, a IA recebe os subtemas já usados em cada
    # tag, para repetir o nome em vez de criar outro para o mesmo assunto.
    subtemas_conhecidos = {
        rotulo: [detalhe for detalhe, _ in lista]
        for rotulo, lista in temas.contar_detalhes(item.get("temas") for item in salvas.values()).items()
    }
    avisar(2, "Áudios do recorte sem classificação válida vão à IA ({}).".format(modelo_da_classificacao))
    resumo = temas.classificar_audios(
        [temas.AudioParaClassificar(audio_id, linhas[audio_id]) for audio_id in sorted(ids) if linhas.get(audio_id)],
        taxonomia,
        tipo,
        {audio_id: item.get("chave") for audio_id, item in salvas.items()},
        emergentes_conhecidos,
        responder,
        lambda audio_id, chave, classificacao: prosodia_db.save_audio_temas(
            audio_id, chave, versao, modelo_da_classificacao, classificacao
        ),
        ao_classificar,
        subtemas_conhecidos=subtemas_conhecidos,
    )
    partes = [
        "{} agora".format(_plural(len(resumo.classificados), "classificado", "classificados")),
        "{} do cache".format(_plural(len(resumo.reaproveitados), "reaproveitado", "reaproveitados")),
    ]
    if resumo.falhas:
        partes.append("{} com falha, fora da contagem".format(_plural(len(resumo.falhas), "áudio", "áudios")))
    avisar(2, "; ".join(partes) + ".")
    validas = rel.classificacoes_validas(
        linhas, prosodia_db.get_audio_temas_for_project(project_id), taxonomia, tipo
    )

    # 3. Agregação
    conteudos = conteudos_do_projeto(project_id)
    audios = [_audio_do_projeto(audio, linhas, validas, conteudos) for audio in audios_projeto]
    anterior = rel.escolher_analise_anterior(prosodia_db.get_project_analyses(project_id), recorte, audios, ids)
    agregacao = rel.agregar(audios, ids, taxonomia, tipo, sinc_projeto, transcricao_projeto, anterior)
    if not agregacao.n_respondentes and resumo.falhas:
        audio_id, erro = sorted(resumo.falhas.items())[0]
        raise SemClassificacao(
            "Nenhum áudio do recorte foi classificado ({}). Primeiro erro, no áudio {}: {}".format(
                _plural(len(resumo.falhas), "falha", "falhas"), audio_id, erro
            )
        )
    leitura = "{} em {} do recorte".format(
        _plural(agregacao.n_respondentes, "respondente", "respondentes"),
        _plural(len(agregacao.contados), "áudio contado", "áudios contados"),
    )
    if agregacao.repetidos:
        leitura += " ({} de outro áudio, fora da contagem)".format(
            _plural(len(agregacao.repetidos), "cópia", "cópias")
        )
    if agregacao.exploratoria:
        leitura += ", leitura exploratória"
    if anterior is not None:
        leitura += "; comparação com a análise de {}".format(anterior.data)
    avisar(3, leitura + ".")

    analises_individuais = {audio_id: analise_para_a_geral(audio_id) for audio_id in agregacao.contados}
    if relatorio_por_tema_ligado(projeto, taxonomia):
        return _gerar_por_tema(
            projeto, audios, audios_projeto, recorte, agregacao, taxonomia, taxonomia_status, taxonomia_da_ia,
            resumo, sinc_projeto, transcricao_projeto, gerar, modelo, analises_individuais,
            responder_consolidacao or responder, perguntas_ativacao, incluir_legenda, avisar,
        )

    # 4. Texto da IA, montagem e verificação
    avisar(4, "A IA escreve o texto; o código monta o relatório e confere as referências.")
    relatorio = rel.escrever_relatorio(
        agregacao,
        contexto,
        recorte,
        len(audios_projeto),
        taxonomia_status,
        gerar,
        taxonomia_da_ia=taxonomia_da_ia,
        sinc_projeto=sinc_projeto,
        transcricao_projeto=transcricao_projeto,
        perguntas_ativacao=perguntas_ativacao,
        analises_individuais=analises_individuais,
        incluir_legenda=incluir_legenda,
    )
    avisar(4, "Verificação das referências: {}.".format(
        _plural(len(relatorio.problemas), "ponto para revisar", "pontos para revisar")
        if relatorio.problemas
        else "nenhum problema"
    ))

    # 5. Gravação
    analise_id = prosodia_db.save_project_analysis(
        project_id, modelo, relatorio.texto, relatorio.citacoes, temas=rel.snapshot(agregacao, recorte)
    )
    avisar(5, "Análise salva com o snapshot dos temas, que a próxima usa para comparar.")
    return Geracao(analise_id, relatorio, agregacao, taxonomia_da_ia, resumo)


# ---------------------------------------------------------------------------
# Relatório por tema (docs/SPEC_RELATORIO_POR_TEMA.md, §5.2)
# ---------------------------------------------------------------------------

def relatorio_por_tema_ligado(projeto: dict, taxonomia: temas.Taxonomia) -> bool:
    """O projeto gera por capítulos: o interruptor ligado e a taxonomia com tag aberta (§2, itens 25 e 27)."""
    return bool(projeto.get("relatorio_por_tema")) and taxonomia.tem_tags_abertas


def detalhes_para_consolidacao(
    audios: Sequence[rel.AudioDoProjeto], tipo: str, taxonomia: temas.Taxonomia
) -> Dict[str, List[subtemas.Detalhe]]:
    """Os subtemas de cada tag no projeto inteiro, sem as cópias do mesmo arquivo (§4)."""
    classificados = [a for a in audios if a.linhas and a.classificacao is not None]
    copias = rel._repetidos(classificados)
    mencoes = [
        (rel.chave_respondente(a.session_id, tipo), a.classificacao)
        for a in classificados
        if a.audio_id not in copias
    ]
    return subtemas.detalhes_do_projeto(mencoes, [s.rotulo for s in taxonomia.subtemas if s.aberta])


def _periodo(recorte: rel.Recorte) -> Tuple[str, ...]:
    datas = []
    for valor in recorte.periodo:
        try:
            datas.append(date.fromisoformat(valor).strftime("%d/%m/%Y"))
        except ValueError:
            continue
    return tuple(datas)


def _gerar_por_tema(
    projeto: dict,
    audios: Sequence[rel.AudioDoProjeto],
    audios_projeto: Sequence[dict],
    recorte: rel.Recorte,
    agregacao: rel.Agregacao,
    taxonomia: temas.Taxonomia,
    taxonomia_status: Optional[str],
    taxonomia_da_ia: bool,
    resumo: temas.ResumoDaClassificacao,
    sinc_projeto: Optional[pd.DataFrame],
    transcricao_projeto: Optional[pd.DataFrame],
    gerar: rel.GerarTexto,
    modelo: str,
    analises_individuais: Dict[int, Optional[str]],
    responder_consolidacao: temas.Responder,
    perguntas_ativacao: Optional[Sequence[dict]],
    incluir_legenda: bool,
    avisar: Aviso,
) -> Geracao:
    """Etapas 3 a 8 da §5.2: consolidação, capítulos, síntese, verificação e gravação com o JSON."""
    if not agregacao.n_respondentes:
        raise ValueError("Nenhum respondente com fala classificada no recorte: não há o que analisar.")
    project_id = int(projeto["id"])
    tipo = normalize_project_type(projeto.get("tipo_projeto"))
    contexto = contexto_do_projeto(projeto)

    avisar(4, "Relatório por tema: a IA agrupa os subtemas de cada tag (só os que mudaram desde a última vez).")
    consolidacao = subtemas.consolidar_subtemas(
        taxonomia,
        detalhes_para_consolidacao(audios, tipo, taxonomia),
        prosodia_db.get_subtema_grupos(project_id),
        responder_consolidacao,
        lambda tag, chave, grupos: prosodia_db.save_subtema_grupos(
            project_id, tag, chave, subtemas.grupos_como_json(grupos), modelo
        ),
    )
    unidades = sorted({overview.unidade_label(a) for a in audios_projeto if a.get("unidade_id")})
    preservar = tuple(mascara.preservar_do_projeto(
        str(projeto.get("name") or ""), unidades, [s.rotulo for s in taxonomia.subtemas]
    ))
    agora = datetime.now()
    cabecalho = capitulos.Cabecalho(
        projeto=str(projeto.get("name") or ""),
        recorte=subtitulo_do_recorte(recorte),
        gerado_em=agora.strftime("%d/%m/%Y %H:%M"),
        periodo=_periodo(recorte),
        modelo=modelo,
        contexto=" ".join(str(contexto.get("especialidade") or "").split())[:400],
        taxonomia_status=taxonomia_status,
        taxonomia_da_ia=taxonomia_da_ia,
        arquivo_do_anexo=nome_do_anexo(str(projeto.get("name") or ""), unidade_do_recorte(recorte), agora.date()),
        preservar=preservar,
    )
    documento = capitulos.montar_relatorio(agregacao, taxonomia, consolidacao, cabecalho)
    avisar(4, "{} e a síntese: a IA escreve cada um, e o código confere e refaz o que falhar.".format(
        _plural(len(documento["capitulos"]), "capítulo", "capítulos")
    ))
    escrito = escrita.escrever_relatorio_por_tema(
        documento,
        gerar,
        tipo_projeto=tipo,
        contexto=contexto,
        fichas=rel.sumarios_por_alias(agregacao, analises_individuais),
        preservar=preservar,
    )
    problemas = list(documento.get("problemas") or [])
    problemas += capitulos.erros(documento)
    avisar(4, "Verificação: {}.".format(
        _plural(len(problemas), "ponto para revisar", "pontos para revisar") if problemas else "nenhum problema"
    ))

    # O apêndice técnico segue o da v2, no fim do texto: é de onde sai o Anexo
    # técnico (anexo_do_texto) e o que a base de conhecimento recebe.
    sumarios = rel.sumarios_individuais(agregacao, analises_individuais)
    apendice = rel.apendice_markdown(
        agregacao, sinc_projeto, transcricao_projeto, perguntas_ativacao,
        (sumarios.com, sumarios.sem), incluir_legenda, sumarios.fora_do_limite,
    )
    texto = capitulos.markdown_do_relatorio(documento).rstrip() + "\n\n## Apêndice técnico\n\n" + apendice.strip() + "\n"
    relatorio = rel.Relatorio(
        texto=texto,
        problemas=problemas,
        citacoes=escrito.citacoes,
        busca=escrito.busca,
        texto_da_ia=json.dumps(documento, ensure_ascii=False),
        sistema=prompts.sistema_capitulo(tipo),
        mensagem="{} chamadas à IA (capítulos e síntese).".format(escrito.chamadas),
    )
    analise_id = prosodia_db.save_project_analysis(
        project_id, modelo, texto, escrito.citacoes, temas=rel.snapshot(agregacao, recorte), relatorio=documento
    )
    avisar(5, "Análise salva com o relatório por tema e o snapshot dos temas.")
    return Geracao(analise_id, relatorio, agregacao, taxonomia_da_ia, resumo, documento)
