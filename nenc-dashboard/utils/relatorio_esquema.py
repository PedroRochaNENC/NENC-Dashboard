"""
O relatório por tema como documento JSON (docs/SPEC_RELATORIO_POR_TEMA.md, §5.1).

É o contrato entre quem monta o relatório (a agregação e a IA, Fases 1 e 2) e
quem o desenha (relatorio_html e relatorio_pdf, Fase 3). Este módulo só
descreve e valida o documento: não lê banco nem chama IA.

Os campos de texto escritos pela IA são marcados "[IA]" na spec; aqui são
strings comuns. Toda fala citada vai por id, em `trechos`: o texto literal, o
alias e o tempo vêm do código, e a máscara de nomes já foi aplicada nele.
"""

from typing import Any, Dict, List

from jsonschema import Draft202012Validator

VERSAO = 2

POLARIDADES = ("positiva", "negativa", "mista", "neutra")
FAIXAS = ("Padrão", "Sinal", "Menção isolada")
TIPOS_DE_CAPITULO = ("tag", "categoria", "outros")
DIRECOES_DE_VOZ = ("acima", "habitual", "abaixo")
NIVEIS_DE_VOZ = ("alta", "media", "baixa")

_TEXTO = {"type": "string"}
_TEXTO_OU_NULO = {"type": ["string", "null"]}
_INTEIRO = {"type": "integer", "minimum": 0}
_IDS = {"type": "array", "items": {"type": "string"}}

_POLARIDADES = {
    "type": "object",
    "properties": {nome: _INTEIRO for nome in POLARIDADES},
    "additionalProperties": False,
}

_VOZ = {
    "type": "object",
    "required": ["avaliavel"],
    "properties": {
        "avaliavel": {"type": "boolean"},
        "respondentes_com_voz": _INTEIRO,
        "direcao": {"enum": list(DIRECOES_DE_VOZ) + [""]},
        "nivel": {"enum": list(NIVEIS_DE_VOZ) + [""]},
        "qualificador": {"enum": ["reforça", "atenua", "contradiz", ""]},
        # Só quando a leitura diz algo; no habitual com ativação média fica vazia.
        "frase": _TEXTO,
    },
}

_DELTA = {
    "type": ["object", "null"],
    "required": ["anteriores", "novos", "total_anteriores", "total_novos", "tendencia"],
    "properties": {
        "anteriores": _INTEIRO,
        "novos": _INTEIRO,
        "total_anteriores": _INTEIRO,
        "total_novos": _INTEIRO,
        "tendencia": {"type": "boolean"},
    },
}

_SUBCAPITULO = {
    "type": "object",
    "required": ["id", "numero", "nome", "respondentes", "polaridades", "o_que_foi_dito", "falas"],
    "properties": {
        "id": _TEXTO,
        "numero": _TEXTO,
        "nome": _TEXTO,
        "respondentes": _INTEIRO,
        "polaridades": _POLARIDADES,
        "subtemas": {"type": "array", "items": {"type": "string"}},
        "o_que_foi_dito": _TEXTO,
        "critica": {
            "type": ["object", "null"],
            "required": ["resumo", "falas"],
            "properties": {"resumo": _TEXTO, "falas": _IDS},
        },
        "falas": _IDS,
    },
}

_CAPITULO = {
    "type": "object",
    "required": ["id", "numero", "tema", "tipo", "faixa", "respondentes", "total", "polaridades", "voz"],
    "properties": {
        "id": _TEXTO,
        "numero": {"type": "integer", "minimum": 1},
        "tema": _TEXTO,
        "tipo": {"enum": list(TIPOS_DE_CAPITULO)},
        "faixa": {"enum": list(FAIXAS) + [""]},
        "respondentes": _INTEIRO,
        "total": _INTEIRO,
        "polaridades": _POLARIDADES,
        "voz": _VOZ,
        "delta": _DELTA,
        # Selo do capítulo "Outros": "Fora da taxonomia validada com a cliente".
        "selo": _TEXTO_OU_NULO,
        # Menção isolada: um parágrafo e uma fala. No capítulo Outros, a abertura.
        "texto": _TEXTO_OU_NULO,
        "falas": _IDS,
        "subcapitulos": {"type": "array", "items": _SUBCAPITULO},
        # Os subtemas que não ganharam subcapítulo, numa linha.
        "demais": _TEXTO_OU_NULO,
        "hipotese": {
            "type": ["object", "null"],
            "required": ["texto", "como_validar", "apoia_em"],
            "properties": {"texto": _TEXTO, "como_validar": _TEXTO, "apoia_em": _IDS},
        },
        "recomendacao": {
            "type": ["object", "null"],
            "required": ["tipo", "driver", "area", "texto", "como_medir", "responde_a"],
            "properties": {
                "tipo": {"enum": ["Higiene", "Encantamento"]},
                "driver": _TEXTO,
                "area": _TEXTO,
                "texto": _TEXTO,
                "como_medir": _TEXTO,
                "responde_a": _IDS,
            },
        },
        "acompanhar": {
            "type": ["object", "null"],
            "required": ["texto", "como_investigar", "porque"],
            "properties": {"texto": _TEXTO, "como_investigar": _TEXTO, "porque": _IDS},
        },
    },
}

_TRECHO = {
    "type": "object",
    "required": ["alias", "tempo", "texto", "polaridade"],
    "properties": {
        "alias": _TEXTO,
        "tempo": _TEXTO,
        "texto": _TEXTO,
        "nota": {"type": ["number", "null"]},
        "polaridade": {"enum": list(POLARIDADES)},
        "mascarado": {"type": "boolean"},
    },
}

_GRUPO_DA_MATRIZ = {
    "type": "object",
    "required": ["chave", "titulo", "descricao", "linhas"],
    "properties": {
        "chave": {"enum": ["vinculo", "risco"]},
        "titulo": _TEXTO,
        "descricao": _TEXTO,
        "linhas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["alias", "temas", "frase"],
                "properties": {
                    "alias": _TEXTO,
                    "unidade": _TEXTO_OU_NULO,
                    "temas": _TEXTO,
                    "frase": _TEXTO,
                },
            },
        },
        "fora_da_tabela": _INTEIRO,
        "voz_no_grupo": _TEXTO_OU_NULO,
        "perfil": _TEXTO_OU_NULO,
        "acao": _TEXTO_OU_NULO,
    },
}

_ITEM_DE_RECLAMACAO = {
    "type": "object",
    "required": ["posicao", "titulo", "tema", "origem", "respondentes", "total", "faixa", "critica", "falas"],
    "properties": {
        "posicao": {"type": "integer", "minimum": 1},
        "titulo": _TEXTO,
        "tema": _TEXTO,
        "origem": _TEXTO,
        "respondentes": _INTEIRO,
        "total": _INTEIRO,
        "faixa": {"enum": list(FAIXAS)},
        "critica": _TEXTO,
        "falas": _IDS,
        # Só com 5 ou mais respondentes com voz e leitura que diz algo.
        "etiqueta_voz": _TEXTO_OU_NULO,
        # Só em relatório de várias unidades.
        "unidades": {"type": "array", "items": {"type": "string"}},
    },
}

ESQUEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "versao", "cabecalho", "alertas", "resumo", "termometro", "como_ler", "visao_geral",
        "sem_mencao", "capitulos", "trechos", "transversais", "matriz", "reclamacoes", "notas",
    ],
    "properties": {
        "versao": {"const": VERSAO},
        "cabecalho": {
            "type": "object",
            "required": ["projeto", "recorte", "respondentes", "audios", "exploratoria", "gerado_em"],
            "properties": {
                "projeto": _TEXTO,
                "recorte": _TEXTO,
                "periodo": {"type": "array", "items": {"type": "string"}},
                "respondentes": _INTEIRO,
                "audios": _INTEIRO,
                # Unidades no recorte; mais de uma liga o rótulo de unidade nas reclamações.
                "unidades": _INTEIRO,
                "exploratoria": {"type": "boolean"},
                "gerado_em": _TEXTO,
                "modelo": _TEXTO,
                "contexto": _TEXTO,
            },
        },
        "alertas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["alias", "tema", "evidencia"],
                "properties": {
                    "alias": _TEXTO,
                    "tema": _TEXTO,
                    "unidade": _TEXTO_OU_NULO,
                    "chegada": _TEXTO_OU_NULO,
                    "tempo": _TEXTO_OU_NULO,
                    "evidencia": _TEXTO,
                },
            },
        },
        "resumo": _TEXTO,
        "termometro": {
            "type": "object",
            "required": ["nota", "avaliaveis", "leitura"],
            "properties": {
                "nota": {"type": ["integer", "null"], "minimum": -5, "maximum": 5},
                "media": {"type": ["number", "null"]},
                "positivos": _INTEIRO,
                "neutros": _INTEIRO,
                "negativos": _INTEIRO,
                "avaliaveis": _INTEIRO,
                "leitura": _TEXTO,
            },
        },
        "como_ler": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["observamos", "significa"],
                "properties": {"observamos": _TEXTO, "significa": _TEXTO},
            },
        },
        "visao_geral": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["capitulo", "tema", "respondentes", "total", "polaridades"],
                "properties": {
                    "capitulo": _TEXTO,
                    "tema": _TEXTO,
                    "respondentes": _INTEIRO,
                    "total": _INTEIRO,
                    "polaridades": _POLARIDADES,
                },
            },
        },
        "sem_mencao": {"type": "array", "items": {"type": "string"}},
        "capitulos": {"type": "array", "items": _CAPITULO},
        "trechos": {"type": "object", "additionalProperties": _TRECHO},
        "transversais": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["texto", "como_validar", "capitulos"],
                "properties": {"texto": _TEXTO, "como_validar": _TEXTO, "capitulos": _IDS},
            },
        },
        "matriz": {
            "type": "object",
            "required": ["avaliavel", "grupos"],
            "properties": {
                "avaliavel": {"type": "boolean"},
                "grupos": {"type": "array", "items": _GRUPO_DA_MATRIZ},
            },
        },
        "reclamacoes": {
            "type": "object",
            "required": ["abertura", "itens", "isoladas"],
            "properties": {
                "abertura": _TEXTO,
                "itens": {"type": "array", "items": _ITEM_DE_RECLAMACAO},
                "isoladas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["tema", "assunto", "fala", "origem"],
                        "properties": {
                            "tema": _TEXTO,
                            "assunto": _TEXTO,
                            "unidade": _TEXTO_OU_NULO,
                            "fala": _TEXTO,
                            "origem": _TEXTO,
                        },
                    },
                },
            },
        },
        "notas": {"type": "array", "items": {"type": "string"}},
        "anexo": {"type": "object", "properties": {"arquivo": _TEXTO}},
        "problemas": {"type": "array", "items": {"type": "string"}},
    },
}

_VALIDADOR = Draft202012Validator(ESQUEMA)


def erros_do_esquema(relatorio: Dict[str, Any]) -> List[str]:
    """O que o documento tem de fora do esquema, um texto por problema."""
    erros = sorted(_VALIDADOR.iter_errors(relatorio), key=lambda erro: list(erro.absolute_path))
    return ["{}: {}".format("/".join(str(parte) for parte in erro.absolute_path) or "(raiz)", erro.message) for erro in erros]


def erros_de_referencia(relatorio: Dict[str, Any]) -> List[str]:
    """Referências que não existem: fala fora de `trechos` e capítulo ou subcapítulo inexistente.

    O esquema JSON não enxerga isto, e o desenho depende: um id solto vira
    uma fala em branco no PDF.
    """
    trechos = relatorio.get("trechos", {})
    capitulos = {c["id"]: c for c in relatorio.get("capitulos", [])}
    subcapitulos = {s["id"] for c in capitulos.values() for s in c.get("subcapitulos", [])}
    problemas: List[str] = []

    def conferir_falas(onde: str, ids) -> None:
        for fala in ids or []:
            if fala not in trechos:
                problemas.append("{}: fala {} não existe em trechos".format(onde, fala))

    for capitulo in capitulos.values():
        conferir_falas(capitulo["id"], capitulo.get("falas"))
        for sub in capitulo.get("subcapitulos", []):
            conferir_falas(sub["id"], sub.get("falas"))
            conferir_falas(sub["id"] + "/critica", (sub.get("critica") or {}).get("falas"))
        for chave in ("hipotese", "recomendacao", "acompanhar"):
            bloco = capitulo.get(chave) or {}
            for campo in ("apoia_em", "responde_a", "porque"):
                for destino in bloco.get(campo, []):
                    if destino not in subcapitulos:
                        problemas.append("{}/{}: {} não é um subcapítulo".format(capitulo["id"], chave, destino))
    for item in relatorio.get("reclamacoes", {}).get("itens", []):
        conferir_falas("reclamacoes/" + str(item.get("posicao")), item.get("falas"))
    for isolada in relatorio.get("reclamacoes", {}).get("isoladas", []):
        conferir_falas("reclamacoes/isoladas", [isolada.get("fala")])
    for transversal in relatorio.get("transversais", []):
        for destino in transversal.get("capitulos", []):
            if destino not in capitulos:
                problemas.append("transversais: capítulo {} não existe".format(destino))
    for linha in relatorio.get("visao_geral", []):
        if linha["capitulo"] not in capitulos:
            problemas.append("visao_geral: capítulo {} não existe".format(linha["capitulo"]))
    return problemas
