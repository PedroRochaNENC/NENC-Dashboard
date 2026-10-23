"""
IA da Análise Geral do Teste Sensorial: gerar, conversar e enviar à base.

O núcleo de conversa e de envio à base é o mesmo da Jornada
(`utils/project_ai.py`); aqui ficam os prompts do módulo, o texto do recorte,
o relatório do estudo e a taxonomia de temas. A geração tem seus próprios
modos, porque o relatório para o marketing é mais longo e, no aprofundado, a
leitura estatística vira o Anexo C. `create` e `add` são injetáveis, para
testar sem a OpenAI, e a base só entra quando a tela passa o vector store do
Teste Sensorial da organização.
"""

import re
from typing import Callable, Dict, List, Optional, Tuple

from utils import project_ai, sensorial_design, sensorial_taxonomia
from utils.kb_attributes import build_kb_filter
from utils.project_ai import AI_MODELS, MODE_LABELS  # noqa: F401 (nomes que a página usa daqui)
from utils.jornada_taxonomy import fold
from utils.sensorial_prompts import (
    REPORT_CLAIMS_SYSTEM_PROMPT,
    SAMPLE_NUMBERS_SYSTEM_PROMPT,
    build_chat_system_prompt,
    build_report_claims_prompt,
    build_sample_numbers_prompt,
    build_sensorial_project_user_prompt,
    build_strategic_user_prompt,
    get_sensorial_project_system_prompt,
    normalize_areas,
    numbers_annex,
    parse_report_claims,
    parse_sample_numbers,
)

MODULE_KEY = "teste_sensorial"
MODULE_LABEL = "Teste Sensorial"
KB_PREFIX = "analise_geral_ts_"
# O relatório por temas, com três blocos em cada um, não cabe nos 4 mil tokens
# da Jornada.
REPORT_MAX_TOKENS = 7000
# Baixa: com o texto corrido, mais variação levava a IA a estender claims e
# camadas além do que o relatório do estudo diz.
REPORT_TEMPERATURE = 0.25
APPENDIX_MAX_TOKENS = 3000


def filters_text(filters: Optional[Dict]) -> str:
    """O recorte em uma linha, para a tela, o prompt e as exportações."""
    profile = (filters or {}).get("perfil") or {}
    parts = ["{} = {}".format(field, ", ".join(str(v) for v in values)) for field, values in profile.items()]
    if (filters or {}).get("comparar_por"):
        parts.append("comparando grupos de {}".format(filters["comparar_por"]))
    return "; ".join(parts) or "todo o projeto"


def load_study_report(project_id: int) -> Tuple[Optional[Tuple[str, str]], str]:
    """(arquivo, texto) do relatório do estudo marcado em Uploads, e o aviso quando ele não pôde ser lido."""
    from utils import sensorial_db
    from utils.briefing import extract_briefing_text

    item = sensorial_db.study_report(project_id)
    if item is None:
        return None, ""
    text, error = extract_briefing_text(item["filename"], sensorial_db.get_file_content(project_id, item["id"]) or b"")
    if error or not text.strip():
        return None, "O relatório do estudo ({}) ficou fora do prompt: {}".format(
            item["filename"], error or "sem texto")
    return (item["filename"], text), ""


def claims_by_sample(model: Dict, metrics: Dict) -> Dict[str, List[str]]:
    """Os claims testados em cada amostra, pelo rótulo da condição."""
    labels = sensorial_design.condition_labels(model["design"])
    table = (metrics.get("associacao") or {}).get("por_condicao")
    claims: Dict[str, List[str]] = {}
    if table is not None and not getattr(table, "empty", True):
        for row in table.itertuples():
            words = claims.setdefault(labels.get(row.condicao, row.condicao), [])
            if row.palavra not in words:
                words.append(row.palavra)
    if not claims and model.get("claims"):
        claims["todas as amostras"] = [item["palavra"] for item in model["claims"]]
    return claims


def study_stages(model: Dict) -> List[str]:
    """As etapas que a experiência organiza: tudo menos a referência e o que ficou fora."""
    return [stage["rotulo"] for stage in model["design"].get("etapas") or []
            if stage.get("papel") not in ("referencia", "ignorar")]


def _context_text(project: Dict) -> str:
    return "\n".join("{}: {}".format(label, str(project.get(key) or "").strip())
                     for label, key in (("Projeto", "name"), ("Categoria", "categoria"), ("Objetivo", "objetivo"))
                     if str(project.get(key) or "").strip())


def resolve_taxonomy(project: Dict, model: Dict, metrics: Dict, *, ai_model: str,
                     report: Optional[Tuple[str, str]] = None, create: Callable = project_ai.default_create,
                     save: Optional[Callable[[str, str], None]] = None) -> Dict:
    """A taxonomia que a análise usa: a do projeto ou, sem ela, a proposta da IA, gravada como provisória.

    Devolve {"texto", "status", "proposta", "aviso"}. Falha ao gravar não
    impede a análise: a proposta segue para o prompt e o aviso para a tela.
    """
    texto = str(project.get("taxonomia") or "").strip()
    status = project.get("taxonomia_status") or sensorial_taxonomia.PROVISORIA
    if not sensorial_taxonomia.parse(texto).vazia:
        return {"texto": texto, "status": status, "proposta": False, "aviso": ""}
    proposta = sensorial_taxonomia.propor(create, ai_model, claims_by_sample(model, metrics), study_stages(model),
                                          (report or ("", ""))[1], _context_text(project))
    if proposta.vazia:
        return {"texto": "", "status": "", "proposta": False,
                "aviso": "A IA não devolveu temas no formato; o relatório agrupou os claims por conta própria."}
    texto, status, aviso = sensorial_taxonomia.formatar(proposta), sensorial_taxonomia.PROVISORIA, ""
    if save is not None:
        try:
            save(texto, status)
        except Exception as error:  # quem gera nem sempre edita o projeto
            aviso = "A taxonomia proposta não foi salva no projeto ({}).".format(error)
    return {"texto": texto, "status": status, "proposta": True, "aviso": aviso}


def sample_codes(model: Dict) -> List[str]:
    """Os códigos das condições que são amostras no desenho (sem basal nem controle)."""
    return [c["codigo"] for c in model["design"].get("condicoes") or [] if c.get("papel") == "amostra"]


def confirmed_sample_names(project: Dict, model: Dict) -> Dict[str, str]:
    """Rótulo técnico -> nome comercial, só com os nomes que a equipe confirmou em Dados do Projeto."""
    from utils import sensorial_db

    saved = sensorial_db.sample_names(project)
    labels = sensorial_design.condition_labels(model["design"])
    return {labels.get(code, code): saved[code] for code in sample_codes(model) if saved.get(code)}


def _app_values(model: Dict, metrics: Dict) -> List[Tuple[str, float]]:
    """(código da amostra, valor) de tudo o que o relatório do estudo pode citar: médias e Scores."""
    samples = set(sample_codes(model))
    values: List[Tuple[str, float]] = []
    tables = [((metrics.get(layer) or {}).get("resumo"), ("media", "mediana")) for layer in ("eeg", "perifericos")]
    tables.append(((metrics.get("associacao") or {}).get("por_condicao"), ("score", "pct_sim", "cr_sim")))
    for table, columns in tables:
        if table is None or getattr(table, "empty", True):
            continue
        for row in table.to_dict("records"):
            if row.get("condicao") not in samples:
                continue
            for column in columns:
                value = row.get(column)
                if isinstance(value, (int, float)) and value == value:
                    values.append((row["condicao"], float(value)))
    return values


def _number(text: str) -> Optional[Tuple[float, int]]:
    """(valor, casas decimais) de um número escrito com vírgula ou ponto decimal."""
    cleaned = str(text).strip().replace(" ", "").rstrip("%").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    decimals = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0
    return value, decimals


def match_sample_names(numbers: Dict[str, List[str]], model: Dict, metrics: Dict) -> Dict[str, str]:
    """Código da amostra -> nome comercial, pelos números do relatório que só uma amostra do app tem.

    Um número vale como voto quando, arredondado às casas com que o relatório
    o escreveu (ou com até 1% de diferença), aparece em uma única amostra do
    app; o sinal é ignorado, porque o Score do relatório e o do app podem usar
    convenções opostas. Fica com o nome a amostra com pelo menos dois votos e
    à frente das outras; número inteiro não vota (é n, contagem ou código).
    Os números do relatório costumam vir de outra escala (SPSS) e batem por
    acaso: o resultado é só uma sugestão, que a equipe confirma.
    """
    values = _app_values(model, metrics)
    targets = {}
    for name, written in numbers.items():
        parsed = [_number(text) for text in written]
        targets[name] = [(abs(value), max(0.5 * 10 ** -decimals, 0.01 * abs(value)))
                         for value, decimals in (p for p in parsed if p is not None) if decimals and value]
    names: Dict[str, str] = {}
    # Em rodadas: a amostra já nomeada sai da busca, e um número que batia
    # nela e em outra passa a votar só na outra.
    while True:
        votes: Dict[str, Dict[str, int]] = {}
        for name, wanted in targets.items():
            if name in names.values():
                continue
            for target, tolerance in wanted:
                hits = {code for code, value in values
                        if code not in names and abs(abs(value) - target) <= tolerance}
                if len(hits) == 1:
                    tally = votes.setdefault(name, {})
                    code = hits.pop()
                    tally[code] = tally.get(code, 0) + 1
        chosen: Dict[str, List[str]] = {}
        for name, tally in votes.items():
            ranked = sorted(tally.items(), key=lambda item: -item[1])
            runner_up = ranked[1][1] if len(ranked) > 1 else 0
            if ranked[0][1] >= 2 and ranked[0][1] > runner_up:
                chosen.setdefault(ranked[0][0], []).append(name)
        # Dois nomes na mesma amostra: nenhum dos dois é confiável.
        new = {code: found[0] for code, found in chosen.items() if len(found) == 1}
        if not new:
            return names
        names.update(new)


def suggest_sample_names(model: Dict, metrics: Dict, *, ai_model: str, report: Tuple[str, str],
                         create: Callable = project_ai.default_create) -> Dict:
    """Sugestão de nome comercial por amostra, para a equipe confirmar em Dados do Projeto.

    A IA lista os números que o relatório do estudo atribui a cada amostra e o
    código os procura nas métricas do app. Devolve {"nomes": {código: nome},
    "lidos": [nomes comerciais que a IA achou no relatório]}: um nome lido e
    não casado com nenhuma amostra fica para a equipe escolher.
    """
    result = create(system_prompt=SAMPLE_NUMBERS_SYSTEM_PROMPT, user_prompt=build_sample_numbers_prompt(report),
                    model=ai_model, vector_store_id=None, temperature=0.0, max_tokens=1500)
    numbers = parse_sample_numbers(result.get("text") or "")
    return {"nomes": match_sample_names(numbers, model, metrics), "lidos": list(numbers)}


def claims_by_sample_code(model: Dict, metrics: Dict) -> Dict[str, List[str]]:
    """Os claims testados em cada amostra, pelo código da condição (todos os claims, sem teste de associação)."""
    codes = sample_codes(model)
    table = (metrics.get("associacao") or {}).get("por_condicao")
    claims: Dict[str, List[str]] = {code: [] for code in codes}
    if table is not None and not getattr(table, "empty", True):
        for row in table.itertuples():
            if row.condicao in claims and row.palavra not in claims[row.condicao]:
                claims[row.condicao].append(row.palavra)
    every = [item["palavra"] for item in model.get("claims") or []]
    return {code: words or list(every) for code, words in claims.items()}


def _tokens(text: str) -> set:
    return set(re.findall(r"[0-9a-z%,]+", fold(text))) - {",", "%"}


def _match_sample(name: str, candidates: Dict[str, List[str]]) -> Optional[str]:
    """O código cuja descrição (nome comercial, rótulo, código) melhor cobre o nome lido no relatório.

    Vale quando uma das duas descrições está toda contida na outra; entre as
    que valem, ganha a que mais se parece ("Zinger 0,001%" não casa com a
    Clash, que também tem "Zinger 0,001%", porque a Clash tem mais palavras).
    """
    read = _tokens(name)
    scores = []
    for code, texts in candidates.items():
        for text in texts:
            known = _tokens(text)
            common = read & known
            if read and known and common:
                ratios = (len(common) / len(known), len(common) / len(read))
                scores.append((max(ratios), min(ratios), len(common), code))
    scores = sorted((s for s in scores if s[0] == 1), reverse=True)
    if not scores or (len(scores) > 1 and scores[0][:3] == scores[1][:3] and scores[0][3] != scores[1][3]):
        return None
    return scores[0][3]


def suggest_report_claims(project: Dict, model: Dict, metrics: Dict, *, ai_model: str, report: Tuple[str, str],
                          create: Callable = project_ai.default_create) -> Dict:
    """Sugestão da conclusão do relatório por amostra e claim, para a equipe confirmar em Dados do Projeto.

    A IA lê só o relatório do estudo; o código casa o nome da amostra que ela
    leu com os nomes confirmados no Desenho e o claim com os testados na
    amostra. Devolve {"status": {código: {claim: status}}, "conflitos":
    [(código, claim)], "amostras_sem_par": [nomes lidos]}: quando a IA dá dois
    status ao mesmo claim (o relatório se contradiz entre slides), não há
    sugestão e a equipe decide.
    """
    from utils import sensorial_db

    result = create(system_prompt=REPORT_CLAIMS_SYSTEM_PROMPT, user_prompt=build_report_claims_prompt(report),
                    model=ai_model, vector_store_id=None, temperature=0.0, max_tokens=3000)
    names = sensorial_db.sample_names(project)
    labels = sensorial_design.condition_labels(model["design"])
    candidates = {code: [t for t in (names.get(code), labels.get(code), code) if t] for code in sample_codes(model)}
    tested = {code: {fold(claim): claim for claim in claims}
              for code, claims in claims_by_sample_code(model, metrics).items()}
    found: Dict[str, Dict[str, set]] = {}
    unmatched: List[str] = []
    for sample, claim, status in parse_report_claims(result.get("text") or ""):
        code = _match_sample(sample, candidates)
        if code is None:
            if sample not in unmatched:
                unmatched.append(sample)
            continue
        known = tested.get(code, {}).get(fold(claim))
        if known:
            found.setdefault(code, {}).setdefault(known, set()).add(status)
    statuses = {code: {claim: next(iter(seen)) for claim, seen in claims.items() if len(seen) == 1}
                for code, claims in found.items()}
    conflicts = [(code, claim) for code, claims in found.items() for claim, seen in claims.items() if len(seen) > 1]
    return {"status": {code: claims for code, claims in statuses.items() if claims}, "conflitos": conflicts,
            "amostras_sem_par": unmatched}


def confirmed_report_claims(project: Dict, model: Dict) -> Dict[str, Dict[str, List[str]]]:
    """{amostra: {status: [claims]}} confirmados pela equipe, com o nome comercial confirmado da amostra."""
    from utils import sensorial_db

    saved = sensorial_db.report_claims(project)
    names = sensorial_db.sample_names(project)
    labels = sensorial_design.condition_labels(model["design"])
    grouped: Dict[str, Dict[str, List[str]]] = {}
    for code in sample_codes(model):
        for claim, status in (saved.get(code) or {}).items():
            sample = names.get(code) or labels.get(code, code)
            grouped.setdefault(sample, {}).setdefault(status, []).append(claim)
    return grouped


def generate_analysis(
    project: Dict,
    model: Dict,
    metrics: Dict,
    *,
    mode: str,
    ai_model: str,
    recorte: str,
    vector_store_id: Optional[str] = None,
    report: Optional[Tuple[str, str]] = None,
    save_taxonomy: Optional[Callable[[str, str], None]] = None,
    create: Callable = project_ai.default_create,
) -> Dict:
    """Roda o modo pedido; devolve {"text", "citations", "search", "taxonomia", "amostras", "claims"}.

    Antes do relatório, a taxonomia (só sem uma no projeto). Os nomes
    comerciais das amostras e a conclusão do relatório por claim são os
    confirmados em Dados do Projeto: sem nomes, o texto usa os rótulos
    técnicos; sem a conclusão, a IA a busca no relatório. Rápida: uma chamada. Aprofundada:
    a leitura estatística sem a base, que vira o Anexo C, e o relatório com a
    base, que recebe a leitura e o mesmo prompt-base.
    """
    if mode not in ("rapida", "aprofundada"):
        raise ValueError("Modo de análise desconhecido: {!r}".format(mode))
    taxonomia = resolve_taxonomy(project, model, metrics, ai_model=ai_model, report=report, create=create,
                                 save=save_taxonomy)
    sample_names = confirmed_sample_names(project, model)
    report_claims = confirmed_report_claims(project, model)
    base = build_sensorial_project_user_prompt(project, model, metrics, recorte=recorte, report=report,
                                               taxonomia=taxonomia["texto"], taxonomia_status=taxonomia["status"],
                                               sample_names=sample_names, report_claims=report_claims)
    names = model.get("index_names") or {}
    # Sem as análises anteriores do projeto, como na Análise Geral do NencBoost: no 1055 a busca devolvia a
    # análise 5, enviada à base, e a IA copiava dela os erros que a confirmação dos claims devia corrigir.
    kb_filter = (build_kb_filter(project.get("id"), modulo=MODULE_KEY, incluir_analises=False)
                 if vector_store_id else None)
    appendix = ""
    if mode == "rapida":
        result = create(system_prompt=get_sensorial_project_system_prompt("rapida", names), user_prompt=base,
                        model=ai_model, vector_store_id=vector_store_id, kb_filter=kb_filter,
                        temperature=REPORT_TEMPERATURE, max_tokens=REPORT_MAX_TOKENS)
    else:
        statistical = create(system_prompt=get_sensorial_project_system_prompt("estatistica", names),
                             user_prompt=base, model=ai_model, vector_store_id=None, temperature=0.2,
                             max_tokens=APPENDIX_MAX_TOKENS)
        appendix = (statistical.get("text") or "").strip()
        result = create(system_prompt=get_sensorial_project_system_prompt("estrategica", names),
                        user_prompt=build_strategic_user_prompt(base, appendix), model=ai_model,
                        vector_store_id=vector_store_id, kb_filter=kb_filter, temperature=REPORT_TEMPERATURE,
                        max_tokens=REPORT_MAX_TOKENS)
    text = normalize_areas((result.get("text") or "").strip())
    # A IA escreve o texto e o Anexo A; os números (B) e a leitura estatística (C) o app acrescenta.
    from utils import sensorial_db

    numbers = numbers_annex(model, metrics, sensorial_db.sample_names(project), sensorial_db.report_claims(project))
    if text and (numbers or appendix):
        if not re.search(r"^##\s+Anexos\s*$", text, re.MULTILINE):
            text += "\n\n---\n\n## Anexos"
        if numbers:
            text += "\n\n### Anexo B — Números do estudo (calculados pelo app)\n\n" + numbers
        if appendix:
            text += "\n\n### Anexo C — Leitura estatística\n\n" + appendix
    return {"text": text, "citations": result.get("citations") or [], "search": result.get("search") or {},
            "taxonomia": taxonomia, "amostras": sample_names, "claims": report_claims}


def send_analysis_to_kb(project: Dict, analysis: Dict, recorte: str, vector_store_id: str, *,
                        add: Callable = project_ai.default_add) -> str:
    """Envia a análise à base marcada como análise deste projeto; devolve o id do arquivo."""
    return project_ai.send_analysis_to_kb(project, analysis, recorte, vector_store_id, module_key=MODULE_KEY,
                                          module_label=MODULE_LABEL, kb_prefix=KB_PREFIX, add=add)


def chat_answer(report_text: str, metrics: Dict, history: List[Dict], *, ai_model: str,
                project_id: Optional[int] = None, vector_store_id: Optional[str] = None,
                create: Callable = project_ai.default_create) -> str:
    """Resposta do chat sobre a análise; `history` termina na pergunta do usuário."""
    return project_ai.chat_answer(build_chat_system_prompt(report_text, metrics), history, ai_model=ai_model,
                                  module_key=MODULE_KEY, project_id=project_id, vector_store_id=vector_store_id,
                                  create=create)
