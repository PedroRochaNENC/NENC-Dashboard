"""
Exportações da Jornada de Compra.

Os arquivos saem do modelo e das métricas que a página já calculou (as mesmas
tabelas da tela), sem ler o banco: a página escolhe o recorte e aqui só se
monta o arquivo.

Excel / Power BI: uma aba por tabela, em formato longo, com chaves simples
para os relacionamentos:

- `recording_key` (participante|tarefa|loja) liga Gravacoes a Olhar_AOI,
  Olhar_AOI_bruto, Marca_por_Gravacao, Resumo_Gravacao e Qualidade;
- `aoi_key` (loja|AOI) liga Catalogo_AOI a Olhar_AOI e Agregados_Grupo;
- `participant` liga Participantes a Gravacoes; `store` liga Lojas a Gravacoes.

As tabelas de dados (gravações, olhar, catálogo) vão completas, com a situação
de cada gravação; as de métricas seguem o recorte da página, descrito na aba
Projeto. A aba Dicionario explica cada coluna.
"""

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from utils.excel_export import (
    EXCEL_CELL_MAX_CHARS,
    EXCEL_SAFE_MAX_DATA_ROWS,
    json_text,
    safe_slug,
    write_workbook,
)
from utils.jornada_ingest import METRIC_COLUMNS, TASK_LABELS

# Folga para o aviso de corte caber na celula.
_TEXT_LIMIT = EXCEL_CELL_MAX_CHARS - 200
_CUT_NOTE = " […texto cortado no limite de uma célula do Excel]"

RECORDING_COLUMNS = [
    "recording_key", "participant", "task", "task_label", "store", "store_label", "channel",
    "profile", "has_frames", "n_frames", "duration_s", "hz", "max_gap_s", "loss_pct",
    "has_aoi_data", "coded", "export_length", "unit", "unit_evidence", "conversion", "status",
    "status_reason", "override_status", "frames_file_id",
]
GAZE_RAW_COLUMNS = ["recording_key", "participant", "task", "store", "aoi", "unit",
                    "source_file_id"] + list(METRIC_COLUMNS)
PACKAGING_COLUMNS = {
    "elements": ["group", "profile", "brand", "element", "element_label", "n_group", "lookers",
                 "reach", "dwell_sum_s", "dwell_per_participant_s", "dwell_per_looker_s",
                 "visits_per_looker", "ttff_mean_s", "element_share"],
    "brands": ["group", "profile", "brand", "n_group", "dwell_sum_s", "packaging_share",
               "dwell_per_participant_s", "logo_reach"],
    "coverage": ["group", "profile", "n_group", "aoi_coverage"],
}
COMPARISON_COLUMNS = ["task", "by", "metric", "group_a", "group_b", "n_a", "n_b", "mean_a",
                      "mean_b", "median_a", "median_b", "diff", "cliffs_delta", "p_value", "method"]
FINDING_COLUMNS = ["section", "text", "cell", "n", "value", "strength"]
ISSUE_COLUMNS = ["level", "code", "message", "ref"]
QUALITY_COLUMNS = ["recording_key", "id", "label", "status", "detail", "value"]
ANALYSIS_COLUMNS = ["id", "created_at", "model", "mode", "data_version", "is_current",
                    "analysis_text", "kb_file_id", "filters", "search"]
CITATION_COLUMNS = ["analysis_id", "citation_index", "file_id", "filename", "quote", "score",
                    "citation_json"]
PROJECT_COLUMNS = ["id", "name", "categoria", "especialidade", "marcas", "marca_foco",
                   "questions", "historico", "problemas", "briefing_filename", "briefing_text",
                   "settings_json", "quality_thresholds", "data_version", "created_at",
                   "recorte", "gerado_em"]

SHEET_DESCRIPTIONS = {
    "Projeto": "Dados do projeto, recorte aplicado às métricas e momento da exportação.",
    "Lojas": "Lojas do estudo, com canal e tarefas feitas em cada uma.",
    "Participantes": "Um participante por linha: perfil e tempo até a decisão.",
    "Gravacoes": "Uma gravação (participante × tarefa × loja) por linha: taxa, unidade, "
                 "situação na análise e qualidade.",
    "Catalogo_AOI": "Cada AOI de cada loja classificada: tipo, marca, linha, produto, "
                    "atributos e se entra na análise.",
    "Olhar_AOI": "Olhar por gravação × AOI, em segundos (convertido por gravação). "
                 "Traz todas as gravações; filtre por status = incluida.",
    "Olhar_AOI_bruto": "Os números do export do Blickshift sem conversão, na unidade de origem.",
    "Agregados_Grupo": "Exports agregados por grupo (perfil ou TODOS), convertidos para segundos.",
    "Metricas_Marca": "Métricas por célula (tarefa × loja) e marca, no recorte da página.",
    "Marca_por_Gravacao": "Uma linha por gravação × marca, com zeros para marca não olhada.",
    "Metricas_SKU": "Métricas por produto (SKU) em cada loja.",
    "Precos": "Atenção às etiquetas de preço, onde estão mapeadas.",
    "Atributos": "Fração da atenção por valor de atributo (ex.: Diurno × Noturno).",
    "Resumo_Gravacao": "Por gravação: atenção à categoria, marcas vistas, marca foco e tempo "
                       "até a decisão.",
    "Tempo_Decisao": "Tempo até a decisão por loja, canal e perfil (descritivo).",
    "Comparacoes": "Comparações entre grupos: médias, δ de Cliff e p quando a amostra permite.",
    "Elementos_Embalagem": "Elementos de cada embalagem por perfil (dos agregados).",
    "Marcas_Embalagem": "Atenção a cada embalagem por perfil e alcance do logo.",
    "Cobertura_Embalagem": "Fração do tempo gravado que caiu em algum elemento de embalagem.",
    "Qualidade": "Checagens de qualidade de cada gravação.",
    "Avisos": "Avisos da leitura dos arquivos (unidades, repetições, agregados que não conferem).",
    "Achados": "Frases determinísticas geradas das métricas, com n e célula.",
    "Limitacoes": "Limitações da análise que acompanham qualquer leitura dos números.",
    "Analises_IA": "Análises de IA salvas no projeto.",
    "Citacoes": "Trechos da base de conhecimento citados por cada análise de IA.",
    "Dicionario": "Esta aba: o que é cada coluna.",
}

COLUMN_DESCRIPTIONS = {
    # chaves e identificacao
    "recording_key": "Chave da gravação: participante|tarefa|loja. Liga Gravacoes, Olhar_AOI, "
                     "Marca_por_Gravacao, Resumo_Gravacao e Qualidade.",
    "aoi_key": "Chave da AOI: loja|nome da AOI. Liga Catalogo_AOI a Olhar_AOI e Agregados_Grupo.",
    "participant": "Código do participante (ex.: Pt01).",
    "task": "Tarefa: livre, estimulada, embalagens ou outra.",
    "task_label": "Nome da tarefa.",
    "store": "Loja (código numérico ou nome). Em métricas, * = todas as lojas da tarefa.",
    "store_label": "Nome da loja exibido nas telas.",
    "label": "Nome exibido.",
    "channel": "Canal da loja (ex.: FARMA, C&C).",
    "profile": "Perfil de shopper.",
    "tasks": "Tarefas feitas na loja.",
    "stores": "Lojas em que o participante gravou.",
    "cell": "Célula de análise: tarefa × loja.",
    "brand": "Marca.",
    "aoi": "Nome da AOI como veio do export.",
    "id": "Identificador.",
    "name": "Nome do projeto.",
    # participantes
    "tempo_informado": "Tempo até a decisão como anotado na planilha (texto).",
    "tempo_decisao_s": "Tempo até a decisão, em segundos.",
    "notes": "Observações da equipe.",
    # gravacoes
    "has_frames": "Há o arquivo de quadros (frame,timestamp) da gravação.",
    "n_frames": "Número de quadros do rastreador.",
    "duration_s": "Duração da gravação, em segundos.",
    "hz": "Taxa de amostragem medida, em Hz.",
    "max_gap_s": "Maior intervalo sem olhar registrado, em segundos.",
    "loss_pct": "Percentual do tempo em falhas de rastreio.",
    "has_aoi_data": "A gravação aparece no export de AOIs.",
    "coded": "Alguma AOI tem olhar registrado (a gravação foi codificada).",
    "export_length": "Comprimento da gravação segundo o export (TotalGazeDuration ÷ Normalized).",
    "unit": "Unidade de tempo do export: segundos ou amostras (quadros).",
    "unit_evidence": "Por que a unidade foi decidida assim.",
    "conversion": "Como os tempos viraram segundos.",
    "status": "Situação: incluida, excluida, nao_codificada, agregado ou sem_aoi. Só "
              "incluida entra nas métricas. Em Qualidade: pass, warn ou fail.",
    "status_reason": "Motivo da situação.",
    "override_status": "Decisão manual da equipe (auto = sem decisão).",
    "frames_file_id": "Arquivo de quadros usado.",
    "quality": "Qualidade geral da gravação: pass, warn ou fail.",
    "quality_label": "Qualidade geral por extenso.",
    "alerts": "Checagens que não passaram.",
    "has_video": "Há vídeo da gravação no projeto.",
    # catalogo
    "kind": "Tipo da AOI: produto, preco, embalagem, fora ou outro.",
    "line": "Linha do produto.",
    "product": "Produto (SKU), sem a parte.",
    "part": "Parte da AOI quando o produto foi dividido (p1, p2…).",
    "element": "Elemento da embalagem (MARCA, FIG, BENEFICIO…).",
    "element_label": "Elemento da embalagem por extenso.",
    "shelf_weight": "Peso manual da AOI na gôndola (vazio = 1 por AOI).",
    "include": "A AOI entra na análise.",
    "is_focus": "É a marca foco do projeto.",
    "source": "Origem da classificação: auto ou manual.",
    "is_outside": "Linha de tempo fora de qualquer AOI.",
    # olhar
    "dwell_s": "Tempo total olhando, em segundos.",
    "visits": "Número de visitas (entradas do olhar na AOI).",
    "ttff_s": "Tempo até o primeiro olhar, em segundos desde o início da gravação.",
    "looked": "Olhou ao menos uma vez.",
    "avg_visit_s": "Duração média de uma visita, em segundos.",
    "max_visit_s": "Visita mais longa, em segundos.",
    "share_of_recording": "Fração da gravação olhando a AOI (NormalizedGazeDuration; sem unidade).",
    "dwell_raw": "TotalGazeDuration na unidade do export.",
    "ttff_raw": "TimeToFirstFixation na unidade do export.",
    "source_file_id": "Arquivo de origem da linha.",
    # agregados
    "group": "Grupo do export agregado (PERFIL n, TODOS) ou Todos = soma dos perfis.",
    "n_group": "Participantes no grupo.",
    "n_group_source": "De onde veio o tamanho do grupo.",
    "dwell_sum_s": "Soma do tempo olhando no grupo, em segundos.",
    "visits_sum": "Soma das visitas no grupo.",
    "lookers": "Participantes do grupo que olharam (GazedAtBy).",
    "ttff_mean_lookers_s": "TTFF médio entre quem olhou, em segundos.",
    "share_of_pool_time": "Fração do tempo do grupo na AOI.",
    "used": "O agregado entra na análise (só onde não há dado individual).",
    # metricas de marca
    "n": "Participantes (gravações incluídas) na célula ou grupo.",
    "n_pos": "Gravações que olharam algum produto (base da share).",
    "share_mean": "Share visual: média, por participante, da fração da atenção às marcas.",
    "share_weighted": "Share ponderada pelo tempo: Σ tempo na marca ÷ Σ tempo nas marcas.",
    "reach": "Alcance: fração dos participantes que olharam.",
    "examined": "Fração que olhou pelo menos o limiar de exame (1 s por padrão).",
    "examined_n": "Gravações com tempo em segundos (base do examinou).",
    "examined_given_noticed": "Entre quem notou, fração que examinou.",
    "revisit": "Fração que voltou a uma AOI da marca (2+ visitas na mesma AOI).",
    "ttff_median": "Mediana do tempo até o primeiro olhar entre quem olhou, em segundos.",
    "ttff_q1": "1º quartil do TTFF, em segundos.",
    "ttff_q3": "3º quartil do TTFF, em segundos.",
    "ttff_n": "Participantes com TTFF (olharam).",
    "rel_ttff_median": "Mediana do TTFF relativo à primeira marca vista, em segundos.",
    "first_noticed": "Fração dos participantes cuja primeira olhada foi na marca (empates dividem).",
    "first_noticed_n": "Participantes que olharam alguma marca.",
    "dwell_mean_s": "Tempo médio por participante, em segundos.",
    "visits_mean": "Visitas médias por participante.",
    "presence": "Fração da gôndola ocupada pela marca (peso manual ou nº de AOIs).",
    "presence_index": "Share ÷ presença: acima de 1, a marca rende mais atenção que o espaço.",
    "presence_source": "Como a presença foi medida.",
    # por gravacao
    "share": "Fração da atenção às marcas que a marca levou nesta gravação.",
    "max_visits": "Maior número de visitas numa AOI da marca.",
    "rel_ttff_s": "TTFF relativo à primeira marca vista nesta gravação, em segundos.",
    "first_credit": "Crédito de primeira marca notada (1, ou dividido em empate).",
    # preco e atributos
    "price_fraction": "Tempo no preço ÷ (preço + produto), média entre participantes.",
    "dimension": "Atributo (ex.: tipo, cobertura).",
    "value": "Valor do atributo, ou valor numérico do achado/checagem.",
    "n_defined": "Participantes com olhar em produtos com o atributo definido.",
    # resumo
    "category_share": "Fração da gravação olhando produtos e preços da categoria.",
    "category_dwell_s": "Tempo olhando a categoria, em segundos.",
    "brands_looked": "Marcas olhadas.",
    "visits_total": "Visitas somadas na categoria.",
    "focus_share": "Share da marca foco nesta gravação.",
    "focus_ttff_s": "TTFF da marca foco, em segundos.",
    # decisao
    "group_type": "Agrupamento: loja, canal ou perfil.",
    "median_s": "Mediana, em segundos.",
    "q1_s": "1º quartil, em segundos.",
    "q3_s": "3º quartil, em segundos.",
    "min_s": "Mínimo, em segundos.",
    "max_s": "Máximo, em segundos.",
    # comparacoes
    "by": "Variável que separa os grupos.",
    "metric": "Métrica comparada.",
    "group_a": "Primeiro grupo.",
    "group_b": "Segundo grupo.",
    "n_a": "n do primeiro grupo.",
    "n_b": "n do segundo grupo.",
    "mean_a": "Média do primeiro grupo.",
    "mean_b": "Média do segundo grupo.",
    "median_a": "Mediana do primeiro grupo.",
    "median_b": "Mediana do segundo grupo.",
    "diff": "Diferença das médias (A − B).",
    "cliffs_delta": "δ de Cliff, de −1 a 1 (0 = grupos iguais).",
    "p_value": "p do teste de permutação (vazio quando descritivo).",
    "method": "Teste usado, ou descritivo com menos de 5 por grupo.",
    # embalagem
    "dwell_per_participant_s": "Tempo por participante do grupo, em segundos.",
    "dwell_per_looker_s": "Tempo por participante que olhou, em segundos.",
    "visits_per_looker": "Visitas por participante que olhou.",
    "ttff_mean_s": "TTFF médio entre quem olhou, em segundos.",
    "element_share": "Fração do olhar da embalagem que o elemento levou.",
    "packaging_share": "Fração do olhar às embalagens que a marca levou.",
    "logo_reach": "Fração do grupo que viu o logo (elemento MARCA).",
    "aoi_coverage": "Fração do tempo gravado que caiu em algum elemento de embalagem.",
    # qualidade e avisos
    "detail": "Detalhe da checagem.",
    "level": "Gravidade: info, warn ou error.",
    "code": "Tipo do aviso.",
    "message": "Mensagem.",
    "ref": "A que o aviso se refere (gravação, arquivo ou célula).",
    # achados e limitacoes
    "section": "Área: gondola, preco, navegacao, embalagem ou decisao.",
    "text": "Texto.",
    "strength": "descritivo (n < 5) ou amostra ≥ 5.",
    "limitation": "Limitação da análise.",
    # projeto
    "categoria": "Categoria estudada.",
    "especialidade": "Especialidade ou contexto do estudo.",
    "marcas": "Marcas conhecidas do projeto, uma por linha.",
    "marca_foco": "Marca foco do projeto.",
    "questions": "Perguntas do estudo.",
    "historico": "Histórico e contexto.",
    "problemas": "Problemas de negócio.",
    "briefing_filename": "Arquivo do briefing.",
    "briefing_text": "Texto do briefing.",
    "settings_json": "Configuração: lojas, canais, perfis, atributos e parâmetros (JSON).",
    "quality_thresholds": "Limiares de qualidade próprios do projeto (JSON; vazio = padrão).",
    "data_version": "Versão dos dados; muda a cada envio ou ajuste.",
    "created_at": "Criado em.",
    "recorte": "Recorte aplicado às métricas desta exportação.",
    "gerado_em": "Momento da exportação.",
    # ia
    "model": "Modelo de IA.",
    "mode": "Modo da análise: rápida ou aprofundada.",
    "is_current": "A análise foi feita com a versão atual dos dados.",
    "analysis_text": "Texto da análise.",
    "kb_file_id": "Cópia da análise na base de conhecimento.",
    "filters": "Recorte usado na análise (JSON).",
    "search": "O que a busca na base fez (JSON).",
    "analysis_id": "Análise de IA da citação.",
    "citation_index": "Ordem da citação.",
    "file_id": "Arquivo citado na base de conhecimento.",
    "filename": "Nome do arquivo citado.",
    "quote": "Trecho citado.",
    "score": "Relevância do trecho na busca.",
    "citation_json": "Citação completa (JSON).",
}
_RAW_COLUMN_NOTE = "Coluna do export do Blickshift, na unidade de origem."


# ---------------------------------------------------------------------------
# Recorte
# ---------------------------------------------------------------------------

def filters_text(filters: Optional[Dict], model: Optional[Dict] = None) -> str:
    """O recorte em uma linha, para a tela, os arquivos e o prompt."""

    filters = filters or {}
    labels = {}
    if model is not None and not model.get("stores", pd.DataFrame()).empty:
        labels = dict(zip(model["stores"]["store"], model["stores"]["label"]))
    tasks = [TASK_LABELS.get(t, t) for t in filters.get("tasks") or []]
    stores = [labels.get(s, s) for s in filters.get("stores") or []]
    profiles = list(filters.get("profiles") or [])
    kinds = filters.get("kinds") or ["produto"]
    parts = [
        "Tarefas: {}".format(", ".join(tasks) if tasks else "todas"),
        "Lojas: {}".format(", ".join(stores) if stores else "todas"),
        "Perfis: {}".format(", ".join(profiles) if profiles else "todos"),
        "Preço: {} da atenção da marca".format("parte" if "preco" in kinds else "fora"),
    ]
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Excel / Power BI
# ---------------------------------------------------------------------------

def _cap_text(value):
    if isinstance(value, str) and len(value) > _TEXT_LIMIT:
        return value[:_TEXT_LIMIT] + _CUT_NOTE
    return value


def _clean(frame: Optional[pd.DataFrame], columns: Sequence[str] = ()) -> pd.DataFrame:
    """Tabela pronta para a planilha: cabeçalho estável, sem ±inf e texto que cabe na célula."""

    if frame is None or (frame.empty and len(frame.columns) == 0):
        return pd.DataFrame(columns=list(columns))
    out = frame.reset_index(drop=True).copy()
    numeric = out.select_dtypes(include="number").columns
    if len(numeric):
        out[numeric] = out[numeric].replace([np.inf, -np.inf], np.nan)
    for column in out.columns:
        if out[column].dtype == object:
            out[column] = out[column].map(_cap_text)
    return out


def _first(item: Dict, *keys: str):
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def _analysis_rows(analyses: Iterable[Dict], data_version) -> Tuple[List[Dict], List[Dict]]:
    analysis_rows, citation_rows = [], []
    for analysis in analyses or ():
        analysis_rows.append({
            "id": analysis.get("id"),
            "created_at": analysis.get("created_at"),
            "model": analysis.get("model"),
            "mode": analysis.get("mode"),
            "data_version": analysis.get("data_version"),
            "is_current": (analysis.get("data_version") is not None
                           and analysis.get("data_version") == data_version),
            "analysis_text": analysis.get("analysis_text"),
            "kb_file_id": analysis.get("kb_file_id"),
            "filters": json_text(analysis.get("filters") or {}),
            "search": json_text(analysis.get("search") or {}),
        })
        for index, citation in enumerate(analysis.get("citations") or [], start=1):
            data = citation if isinstance(citation, dict) else {"quote": str(citation)}
            citation_rows.append({
                "analysis_id": analysis.get("id"),
                "citation_index": index,
                "file_id": _first(data, "file_id"),
                "filename": _first(data, "filename", "file_name", "source"),
                "quote": _first(data, "quote", "text", "content"),
                "score": _first(data, "score"),
                "citation_json": json_text(data),
            })
    return analysis_rows, citation_rows


def _recordings_sheet(model: Dict, quality: Optional[Dict], media: Sequence[Dict]) -> pd.DataFrame:
    recordings = _clean(model.get("recordings"), RECORDING_COLUMNS)
    summary = (quality or {}).get("summary")
    if summary is not None and not summary.empty and not recordings.empty:
        recordings = recordings.merge(
            summary[["recording_key", "quality", "quality_label", "alerts"]],
            on="recording_key", how="left",
        )
    else:
        for column in ("quality", "quality_label", "alerts"):
            recordings[column] = None
    with_video = {
        "{}|{}|{}".format(m.get("participant_code") or "", m.get("task") or "", m.get("store") or "")
        for m in media or ()
    }
    recordings["has_video"] = recordings["recording_key"].isin(with_video) if len(recordings) else []
    return recordings


def _dictionary(tables: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for sheet, frame in tables.items():
        for column in frame.columns:
            if column in COLUMN_DESCRIPTIONS:
                description = COLUMN_DESCRIPTIONS[column]
            elif str(column).startswith("attr_"):
                description = "Valor do atributo {} no nome da AOI.".format(str(column)[5:])
            elif column in METRIC_COLUMNS:
                description = _RAW_COLUMN_NOTE
            else:
                description = ""
            rows.append({"aba": sheet, "descricao_aba": SHEET_DESCRIPTIONS.get(sheet, ""),
                         "coluna": column, "descricao": description})
    rows.append({"aba": "Dicionario", "descricao_aba": SHEET_DESCRIPTIONS["Dicionario"],
                 "coluna": "aba / coluna / descricao", "descricao": "Aba, coluna e o que ela contém."})
    return pd.DataFrame(rows, columns=["aba", "descricao_aba", "coluna", "descricao"])


def excel_tables(
    project: Dict,
    model: Dict,
    metrics: Dict,
    *,
    quality: Optional[Dict] = None,
    analyses: Sequence[Dict] = (),
    media: Sequence[Dict] = (),
    generated_at: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    """As abas do Excel, em ordem, antes de virar arquivo."""

    filters = metrics.get("filters") or {}
    data_version = project.get("data_version")
    project_row = {column: project.get(column) for column in PROJECT_COLUMNS}
    project_row["recorte"] = filters_text(filters, model)
    project_row["gerado_em"] = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    packaging = metrics.get("packaging") or {}
    analysis_rows, citation_rows = _analysis_rows(analyses, data_version)
    limitations = pd.DataFrame({"limitation": list(metrics.get("limitations") or [])})

    tables = {
        "Projeto": _clean(pd.DataFrame([project_row], columns=PROJECT_COLUMNS)),
        "Lojas": _clean(model.get("stores"), ["store", "label", "channel", "tasks"]),
        "Participantes": _clean(model.get("participants"),
                                ["participant", "profile", "tempo_informado", "tempo_decisao_s",
                                 "notes", "stores", "channel"]),
        "Gravacoes": _recordings_sheet(model, quality, media),
        "Catalogo_AOI": _clean(model.get("catalog")),
        "Olhar_AOI": _clean(model.get("gaze")),
        "Olhar_AOI_bruto": _clean(model.get("gaze_raw"), GAZE_RAW_COLUMNS),
        "Agregados_Grupo": _clean(model.get("pooled")),
        "Metricas_Marca": _clean(metrics.get("brand")),
        "Marca_por_Gravacao": _clean(metrics.get("per_recording_brand")),
        "Metricas_SKU": _clean(metrics.get("sku")),
        "Precos": _clean(metrics.get("price")),
        "Atributos": _clean(metrics.get("attributes")),
        "Resumo_Gravacao": _clean(metrics.get("recording_summary")),
        "Tempo_Decisao": _clean(metrics.get("decision")),
        "Comparacoes": _clean(metrics.get("comparisons"), COMPARISON_COLUMNS),
        "Elementos_Embalagem": _clean(packaging.get("elements"), PACKAGING_COLUMNS["elements"]),
        "Marcas_Embalagem": _clean(packaging.get("brands"), PACKAGING_COLUMNS["brands"]),
        "Cobertura_Embalagem": _clean(packaging.get("coverage"), PACKAGING_COLUMNS["coverage"]),
        "Qualidade": _clean((quality or {}).get("checks"), QUALITY_COLUMNS),
        "Avisos": _clean(pd.DataFrame(model.get("issues") or [], columns=ISSUE_COLUMNS)),
        "Achados": _clean(pd.DataFrame(metrics.get("findings") or [], columns=FINDING_COLUMNS)),
        "Limitacoes": _clean(limitations, ["limitation"]),
        "Analises_IA": _clean(pd.DataFrame(analysis_rows, columns=ANALYSIS_COLUMNS)),
        "Citacoes": _clean(pd.DataFrame(citation_rows, columns=CITATION_COLUMNS)),
    }
    tables["Dicionario"] = _dictionary(tables)
    return tables


def export_filename(project: Dict, extension: str, prefix: str = "jornada") -> str:
    return "{}_{}_{}.{}".format(prefix, safe_slug(project.get("name", "projeto")),
                                project.get("id", 0), extension)


def build_excel(
    project: Dict,
    model: Dict,
    metrics: Dict,
    *,
    quality: Optional[Dict] = None,
    analyses: Sequence[Dict] = (),
    media: Sequence[Dict] = (),
    max_rows_per_sheet: int = EXCEL_SAFE_MAX_DATA_ROWS,
) -> Tuple[bytes, str]:
    """Workbook para Excel e Power BI; devolve os bytes e o nome do arquivo."""

    tables = excel_tables(project, model, metrics, quality=quality, analyses=analyses, media=media)
    return write_workbook(tables, max_rows_per_sheet), export_filename(project, "xlsx", "jornada_powerbi")
