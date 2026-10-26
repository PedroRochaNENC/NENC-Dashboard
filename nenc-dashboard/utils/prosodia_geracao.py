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

from dataclasses import dataclass
from datetime import date
from typing import Callable, Collection, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from utils import ai_provider, prosodia_db
from utils import prosodia_overview as overview
from utils import prosodia_relatorio as rel
from utils import prosodia_temas as temas
from utils.kb_attributes import build_kb_filter
from utils.prosodia_project_types import normalize_project_type

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

    def gerar(**kwargs) -> dict:
        return ai_provider.generate_analysis(
            provedor, modelo, vector_store_id=vector_store_id, kb_filter=filtro, **kwargs
        )

    return gerar


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
    )


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
            "A taxonomia do projeto não tem nenhuma linha no formato \"Categoria > Subtema\". "
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
        analises_individuais={
            audio_id: (prosodia_db.get_latest_analysis(audio_id) or {}).get("analysis_text")
            for audio_id in agregacao.contados
        },
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
