"""
Excel / Power BI do Teste Sensorial.

Uma aba por tabela da análise, no recorte da página: projeto e desenho,
sessões (com a situação em cada camada e a qualidade), participantes, janelas
que ficaram de fora, médias por participante, resumo por condição e etapa,
comparações pareadas (p bruto e de Holm, efeito), curvas dos índices,
teste de associação, síntese, achados, limitações, análises de IA e um
dicionário de todas as colunas. As janelas de EEG incluídas vão com os índices,
para o Power BI relacionar pela sessão e pela etapa.

Só o código do participante entra: o nome nunca chegou ao modelo.
"""

from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from utils import sensorial_cleaning, sensorial_design, sensorial_metrics
from utils.excel_export import (
    EXCEL_SAFE_MAX_DATA_ROWS,
    analysis_rows,
    clean_frame,
    dictionary_frame,
    safe_slug,
    write_workbook,
)

SHEET_DESCRIPTIONS = {
    "Projeto": "O projeto, o recorte, a versão dos dados e a configuração da análise.",
    "Condicoes": "Condições do desenho (basal, controle, amostras).",
    "Etapas": "Etapas da análise e o papel de cada uma.",
    "Sessoes": "Uma linha por sessão: código, condição, janelas, situação em cada camada e qualidade.",
    "Participantes": "Uma linha por participante: sessões, perfil e registro de campo.",
    "Janelas_Fora": "Janelas de EEG das etapas da análise que ficaram de fora, com o motivo.",
    "Janelas_EEG": "Janelas de EEG incluídas, com os índices do relatório (para o Power BI).",
    "Medias_Participante": "Média das janelas de cada participante por condição, etapa e medida.",
    "Resumo": "Por medida, condição e etapa: n de participantes, média, desvio, erro padrão e mediana.",
    "Comparacoes": "Comparações pareadas por participante (Wilcoxon), com o p de Holm por medida × etapa.",
    "Curvas": "Curva média de cada índice por condição e etapa (início da etapa), com o erro padrão.",
    "Associacao": "Teste de associação por condição e claim: % de Sim, CR do Sim, Score, faixa e quadrante.",
    "Sintese": "Por amostra e claim: o que cérebro, corpo e teste explícito confirmam.",
    "Achados": "Diferenças (Holm) e tendências, em frases.",
    "Limitacoes": "Limitações da análise.",
    "Analises_IA": "Análises de IA salvas no projeto.",
    "Citacoes_IA": "Trechos da base de conhecimento citados nas análises.",
    "Dicionario": "Aba, coluna e o que ela contém.",
}

COLUMN_DESCRIPTIONS = {
    "project_id": "Id do projeto no app.", "projeto": "Nome do projeto.", "categoria": "Categoria do estudo.",
    "objetivo": "Contexto e objetivo do estudo.", "data_version": "Versão dos dados quando o arquivo foi gerado.",
    "recorte": "Recorte por perfil aplicado.", "gerado_em": "Data e hora da geração.",
    "limpeza": "Limpeza em uso (BASE LIMPA e regra de outliers).", "ppi_modo": "Modo do PPI (z ou spss).",
    "participantes": "Participantes no recorte.", "codigo": "Código da condição ou da etapa.",
    "rotulo": "Nome nos gráficos.", "papel": "Papel no desenho.",
    "sessao_id": "Identificador da sessão no pipeline (chave entre as tabelas).",
    "participant_code": "Código do participante (o nome nunca entra no app).",
    "experimento": "Experimento da sessão no pipeline.", "amostra": "Amostra da sessão no pipeline.",
    "condicao": "Condição do desenho.", "data": "Data da sessão.", "hora": "Hora da sessão.",
    "janelas_eeg": "Janelas de EEG da sessão (todas as etapas).",
    "janelas_eeg_analise": "Janelas de EEG nas etapas da análise.",
    "janelas_perifericos": "Janelas dos periféricos da sessão.",
    "janelas_perifericos_analise": "Janelas dos periféricos nas etapas da análise.",
    "tentativas": "Tentativas do teste de associação.", "repetida": "Outra sessão do participante na mesma condição.",
    "tentativas_analise": "Tentativas do teste de associação com claim (as que entram na conta).",
    "sessoes": "Sessões do participante no pipeline.", "condicoes": "Condições em que o participante tem sessão.",
    "notas": "Anotações da equipe sobre o participante.",
    "etapa": "Etapa.", "Etapa": "Etapa.", "Bloco": "Bloco da etapa.", "Tempo": "Segundos desde o início da etapa.",
    "motivo": "Por que a janela ficou de fora.", "medida": "Código da medida.", "medida_nome": "Nome de negócio.",
    "valor": "Média das janelas do participante.", "n": "Número de participantes (ou de pares).",
    "media": "Média.", "dp": "Desvio padrão.", "ep": "Erro padrão.", "mediana": "Mediana.",
    "tipo": "Tipo da comparação: vs_basal, vs_controle ou entre_amostras.",
    "condicao_a": "Condição comparada.", "condicao_b": "Condição de comparação.",
    "etapa_b": "Etapa da condição de comparação (a de referência do basal).",
    "media_a": "Média da condição comparada.", "media_b": "Média da condição de comparação.",
    "mediana_a": "Mediana da condição comparada.", "mediana_b": "Mediana da condição de comparação.",
    "diferenca_mediana": "Mediana das diferenças pareadas (a − b).", "r": "r de postos pareado (−1 a 1).",
    "p": "p bruto do Wilcoxon.", "metodo": "wilcoxon ou descritivo (abaixo do mínimo de pares).",
    "p_holm": "p corrigido por Holm na família medida × etapa.",
    "resultado": "diferença (Holm), tendência (p bruto), sem diferença ou descritivo.",
    "camada": "eeg ou perifericos.", "t": "Segundos desde o início da etapa (bins de 0,25 s).",
    "palavra": "Claim do teste de associação.", "pct_sim": "Fração de respostas Sim.",
    "cr_sim": "CR médio das respostas Sim.", "score": "% de Sim × CR médio do Sim.",
    "faixa": "Faixa do Score.", "quadrante": "Quadrante (adesão × convicção).",
    "cerebro": "O que o EEG diz do claim.", "corpo": "O que os periféricos dizem do claim.",
    "explicito": "O que o teste explícito diz do claim.", "conclusao": "validado, parcial ou não validado.",
    "texto": "Achado em frase.", "comparacao": "Condição de comparação.", "limitacao": "Limitação.",
    "is_current": "A análise foi gerada com a versão atual dos dados.", "PPI": "Índice preditivo geral.",
    "incluida": "A janela entra na análise.", "qualidade": "Qualidade geral da sessão.",
    "grupo": "Grupo somado no teste de associação (vazio = por condição).",
    # ia
    "id": "Id da análise de IA.", "created_at": "Criada em.", "model": "Modelo de IA.",
    "mode": "Modo da análise: rápida ou aprofundada.", "analysis_text": "Texto da análise.",
    "kb_file_id": "Cópia da análise na base de conhecimento.", "filters": "Recorte usado na análise (JSON).",
    "search": "O que a busca na base fez (JSON).", "analysis_id": "Análise de IA da citação.",
    "citation_index": "Ordem da citação.", "file_id": "Arquivo citado na base de conhecimento.",
    "documento": "Nome do documento citado.", "quote": "Trecho citado.",
    "relevancia": "Relevância do trecho na busca.", "citation_json": "Citação completa (JSON).",
}
ANALYSIS_COLUMNS = ["id", "created_at", "model", "mode", "data_version", "is_current", "analysis_text",
                    "kb_file_id", "filters", "search"]
# "filename" e "score" já têm outro sentido aqui (nome de pessoa; Score do claim): a citação leva nomes próprios.
CITATION_COLUMNS = {"analysis_id": "analysis_id", "citation_index": "citation_index", "file_id": "file_id",
                    "filename": "documento", "quote": "quote", "score": "relevancia", "citation_json": "citation_json"}


def _describe(column: str) -> str:
    for prefix, text in (("incluida_", "Entra na camada {}."), ("motivo_", "Por que fica de fora da camada {}."),
                         ("qualidade_", "Qualidade da camada {}."), ("detalhe_", "Detalhe da qualidade da camada {}."),
                         ("repetida_", "Repetição na camada {}."), ("perfil_", "Perfil: {}."),
                         ("campo_", "Registro de campo: {}.")):
        if column.startswith(prefix):
            return text.format(column[len(prefix):])
    for item in sensorial_design.INDEX_CATALOG:
        if column == item["codigo"]:
            return "{}: {}".format(item["nome"], item["descricao"])
    return ""


def export_filename(project: Dict, extension: str, prefix: str = "teste_sensorial") -> str:
    return "{}_{}_{}.{}".format(prefix, safe_slug(project.get("name", "projeto")), project.get("id", 0), extension)


def _with_names(frame: pd.DataFrame, names: Dict[str, str]) -> pd.DataFrame:
    if frame.empty or "medida" not in frame:
        return frame
    frame = frame.copy()
    frame.insert(frame.columns.get_loc("medida") + 1, "medida_nome", frame["medida"].map(lambda m: names.get(m, m)))
    return frame


def excel_tables(project: Dict, model: Dict, metrics: Dict, *, quality: Optional[pd.DataFrame] = None,
                 analyses: Sequence[Dict] = (), recorte: str = "") -> Dict[str, pd.DataFrame]:
    names = model["index_names"]
    settings = model["settings"]
    design = model["design"]
    tables: Dict[str, pd.DataFrame] = {}
    tables["Projeto"] = pd.DataFrame([{
        "project_id": project.get("id"), "projeto": project.get("name"), "categoria": project.get("categoria"),
        "objetivo": project.get("objetivo"), "data_version": project.get("data_version"),
        "recorte": recorte or "todo o projeto", "participantes": metrics.get("participantes"),
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "limpeza": sensorial_cleaning.settings_text(settings), "ppi_modo": settings["indices"]["ppi"].get("modo"),
    }])
    tables["Condicoes"] = clean_frame(pd.DataFrame(design.get("condicoes") or []), ["codigo", "rotulo", "papel"])
    tables["Etapas"] = clean_frame(pd.DataFrame(design.get("etapas") or []), ["codigo", "rotulo", "papel"])
    sessions = model["sessions"].drop(columns=["codigo_corrigido", "condicao_corrigida", "avisos_sessao"],
                                      errors="ignore")
    if quality is not None and not quality.empty:
        sessions = sessions.merge(quality, on="sessao_id", how="left")
    tables["Sessoes"] = clean_frame(sessions)
    tables["Participantes"] = clean_frame(model["participants"])
    eeg = model["eeg"]
    keys = ["sessao_id", "participant_code", "condicao", "Etapa", "Bloco", "Tempo"]
    if not eeg.empty:
        stages = sensorial_design.analysis_stages(design)
        out = eeg[eeg["Etapa"].isin(stages) & ~eeg["incluida"]]
        tables["Janelas_Fora"] = clean_frame(out[keys + ["motivo"]], keys + ["motivo"])
        included = eeg[eeg["incluida"]]
        tables["Janelas_EEG"] = clean_frame(included[keys + list(model["indices"])])
    else:
        tables["Janelas_Fora"] = clean_frame(None, keys + ["motivo"])
        tables["Janelas_EEG"] = clean_frame(None, keys)
    means = pd.concat([metrics["eeg"]["medias"].assign(camada="eeg"),
                       metrics["perifericos"]["medias"].assign(camada="perifericos")], ignore_index=True)
    tables["Medias_Participante"] = clean_frame(_with_names(means, names))
    summary = pd.concat([metrics["eeg"]["resumo"].assign(camada="eeg"),
                         metrics["perifericos"]["resumo"].assign(camada="perifericos")], ignore_index=True)
    tables["Resumo"] = clean_frame(_with_names(summary, names))
    comparisons = pd.concat([metrics["eeg"]["comparacoes"].assign(camada="eeg"),
                             metrics["perifericos"]["comparacoes"].assign(camada="perifericos")], ignore_index=True)
    tables["Comparacoes"] = clean_frame(_with_names(comparisons, names))
    curves = []
    for measure in model["indices"]:
        curve = sensorial_metrics.curves(model, measure, "etapa", metrics.get("filtros"))["curva"]
        if not curve.empty:
            curves.append(curve.assign(medida=measure))
    tables["Curvas"] = clean_frame(_with_names(pd.concat(curves, ignore_index=True), names) if curves else None,
                                   ["medida", "medida_nome", "condicao", "etapa", "t", "media", "ep", "n"])
    association = metrics["associacao"]["por_condicao"].assign(grupo="")
    pooled = metrics["associacao"].get("agrupado")
    if pooled is not None and not pooled.empty:
        association = pd.concat([association, pooled.assign(grupo=pooled["condicao"])], ignore_index=True)
    tables["Associacao"] = clean_frame(association)
    tables["Sintese"] = clean_frame(metrics["sintese"])
    tables["Achados"] = clean_frame(pd.DataFrame(metrics["achados"]),
                                    ["camada", "tipo", "medida", "condicao", "comparacao", "etapa", "p_holm", "texto"])
    tables["Limitacoes"] = pd.DataFrame({"limitacao": metrics["limitacoes"]})
    analysis_list, citation_list = analysis_rows(analyses, project.get("data_version"))
    tables["Analises_IA"] = clean_frame(pd.DataFrame(analysis_list, columns=ANALYSIS_COLUMNS))
    tables["Citacoes_IA"] = clean_frame(pd.DataFrame(citation_list, columns=list(CITATION_COLUMNS))
                                        .rename(columns=CITATION_COLUMNS))
    tables["Dicionario"] = dictionary_frame(tables, SHEET_DESCRIPTIONS, COLUMN_DESCRIPTIONS, _describe)
    return tables


def build_excel(project: Dict, model: Dict, metrics: Dict, *, quality: Optional[pd.DataFrame] = None,
                analyses: Sequence[Dict] = (), recorte: str = "",
                max_rows_per_sheet: int = EXCEL_SAFE_MAX_DATA_ROWS) -> Tuple[bytes, str]:
    """Workbook para Excel e Power BI; devolve os bytes e o nome do arquivo."""
    tables = excel_tables(project, model, metrics, quality=quality, analyses=analyses, recorte=recorte)
    return write_workbook(tables, max_rows_per_sheet), export_filename(project, "xlsx")


# ---------------------------------------------------------------------------
# Tabelas compactas, comuns ao PDF e ao PPTX
# ---------------------------------------------------------------------------

MAX_TOPOMAPS = 12


def report_topomaps(model: Dict, limit: int = MAX_TOPOMAPS) -> List[Dict]:
    """Topomapas das etapas analisadas, por experimento e na ordem do desenho, com a legenda do relatório."""
    stages = {s["codigo"]: (index, s["rotulo"]) for index, s in enumerate(model["design"].get("etapas") or [])
              if s.get("papel") != "ignorar"}
    chosen = [t for t in model.get("topomaps") or [] if (t.get("etapa_arquivo") or "") in stages]
    chosen.sort(key=lambda t: (t.get("experimento") or "", stages[t["etapa_arquivo"]][0]))
    return [{"file_id": t["file_id"],
             "title": " · ".join(p for p in (t.get("experimento") or "", stages[t["etapa_arquivo"]][1]) if p)}
            for t in chosen[:limit]]

def _fmt(value, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return "—" if number != number else "{:.{}g}".format(number, digits).replace(".", ",")


def measure_rows(summary: pd.DataFrame, measure: str, design: Dict) -> Tuple[list, list]:
    """Uma linha por condição, uma coluna por etapa: "média ± ep (n)"."""
    stages = [s for s in design.get("etapas") or []]
    headers = ["Condição"] + [s["rotulo"] for s in stages]
    rows = []
    data = summary[summary["medida"] == measure] if not summary.empty else summary
    for condition in design.get("condicoes") or []:
        cells = []
        for stage in stages:
            hit = data[(data["condicao"] == condition["codigo"]) & (data["etapa"] == stage["codigo"])]
            if hit.empty:
                cells.append("—")
            else:
                row = hit.iloc[0]
                cells.append("{} ± {} ({})".format(_fmt(row["media"]), _fmt(row["ep"], 2), int(row["n"])))
        if any(cell != "—" for cell in cells):
            rows.append([condition["rotulo"]] + cells)
    return headers, rows


def comparison_rows(table: pd.DataFrame, model: Dict, measures, only_relevant: bool = True) -> Tuple[list, list]:
    """Comparações com diferença ou tendência, em linhas legíveis."""
    labels = sensorial_design.condition_labels(model["design"])
    stages = {s["codigo"]: s["rotulo"] for s in model["design"].get("etapas") or []}
    names = model["index_names"]
    headers = ["Medida", "Comparação", "Etapa", "n", "Dif. mediana", "r", "p Holm", "Resultado"]
    if table.empty:
        return headers, []
    chosen = table[table["medida"].isin(list(measures))]
    if only_relevant:
        chosen = chosen[chosen["resultado"].isin(["diferença", "tendência"])]
    chosen = chosen.sort_values(["resultado", "p_holm"])
    rows = [[names.get(r.medida, r.medida), "{} × {}".format(labels.get(r.condicao_a, r.condicao_a),
                                                             labels.get(r.condicao_b, r.condicao_b)),
             stages.get(r.etapa, r.etapa), str(int(r.n)), _fmt(r.diferenca_mediana), _fmt(r.r, 2),
             _fmt(r.p_holm, 2), r.resultado] for r in chosen.itertuples()]
    return headers, rows


def association_rows(table: pd.DataFrame, model: Dict) -> Tuple[list, list]:
    labels = sensorial_design.condition_labels(model["design"])
    headers = ["Condição", "Claim", "% Sim", "CR do Sim", "Score", "Faixa", "Quadrante"]
    rows = [[labels.get(r.condicao, r.condicao), r.palavra, "{:.0f}%".format(100 * r.pct_sim), _fmt(r.cr_sim),
             _fmt(r.score), r.faixa, r.quadrante] for r in table.itertuples()] if not table.empty else []
    return headers, rows


def synthesis_rows(table: pd.DataFrame, model: Dict) -> Tuple[list, list]:
    """Claim por amostra: a conclusão de cada cruzamento."""
    labels = sensorial_design.condition_labels(model["design"])
    if table.empty:
        return ["Claim"], []
    samples = list(dict.fromkeys(table["condicao"]))
    headers = ["Claim"] + [labels.get(s, s) for s in samples]
    rows = []
    for word, group in table.groupby("palavra", sort=False):
        by_sample = dict(zip(group["condicao"], group["conclusao"]))
        rows.append([word] + [by_sample.get(s, "—") for s in samples])
    return headers, rows
