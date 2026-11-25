"""
Uma Análise Geral v2 em markdown, montada pelo próprio código da v2 (spec do relatório por tema, §9).

O corpo é o exemplo das lavanderias de test_prosodia_montagem (o exemplo de
resposta do prompt, separado e montado por `montar_relatorio`), e o apêndice
sai de `apendice_markdown` sobre oito áudios com o Sincronizado. É o texto que
o template legado e o Anexo técnico recebem; nenhum dado de cliente entra.
"""

from typing import Dict, List

import pandas as pd

from tests.test_prosodia_montagem import _exemplo
from tests.test_prosodia_relatorio import MAQUINA, _agregar, _audio, _tema
from utils import prosodia_prompts as prompts
from utils import prosodia_relatorio as rel

PROJETO = "Rede de lavanderias self-service"
_FALAS = (
    "A máquina quatro parou no meio do ciclo e o app já tinha cobrado, ninguém resolveu nada.",
    "Gostei muito da limpeza da loja.",
    "O preço do ciclo subiu e eu não fui avisado de nada pelo aplicativo.",
    "Atendimento nota dez, todo mundo muito educado.",
)
_UNIDADES = ((1, "Centro", "LJ-CEN-01"), (2, "Moema", "LJ-MOE-01"))


def _apendice(incluir_legenda: bool = True) -> str:
    audios = [_audio(i, _tema(MAQUINA)) for i in range(1, 9)]
    linhas = []
    for audio in audios:
        for s in range(10):
            linhas.append({
                "session_id": audio.session_id, "SpeakerName": "Respondente", "Text": _FALAS[(s + audio.audio_id) % 4],
                "start_s": s * 4.0, "end_s": s * 4.0 + 3.5, "segmento_idx": s,
                "dim_valence": 0.1 * s - 0.05 * audio.audio_id, "dim_arousal": 0.2 * s - 0.3, "dim_dominance": 0.1,
                "f0_media": 180.5 + s, "sentimento_texto": 0.6 if s % 3 == 0 else -0.4,
                "emocao_happy": 0.7, "emocao_neutral": 0.1, "emocao_sad": 0.1, "emocao_angry": 0.1,
            })
    sinc = pd.DataFrame(linhas)
    return rel.apendice_markdown(_agregar(audios, sinc_projeto=sinc), sinc, None, incluir_legenda=incluir_legenda)


def texto_v2(incluir_legenda: bool = True) -> str:
    """O texto salvo de uma Análise Geral v2: cabeçalho, seções numeradas e o "Apêndice técnico"."""
    agregacao = _exemplo()
    cabecalho = rel.cabecalho_markdown(agregacao, PROJETO + " — Voz do Cliente por QR Code", rel.Recorte(), 64, "validada")
    return rel.montar_relatorio(
        agregacao, cabecalho, rel.separar_secoes(prompts.EXEMPLO_ANALISE_GERAL), _apendice(incluir_legenda),
        como_ler=rel.como_ler_markdown(agregacao, "validada"),
    )


def audios_do_projeto() -> List[Dict]:
    """As linhas de get_audios_for_interviews dos oito áudios do apêndice, em duas unidades e uma sem."""
    audios = []
    for audio_id in range(1, 9):
        unidade = _UNIDADES[audio_id % 2] if audio_id != 8 else None
        audios.append({
            "id": audio_id,
            "session_id": _audio(audio_id).session_id,
            "qr_code_name": "Máquinas" if audio_id % 3 else "Balcão",
            "created_at": "2026-10-{:02d} 10:00:00".format(audio_id),
            "unidade_id": unidade[0] if unidade else None,
            "unidade_nome": unidade[1] if unidade else None,
            "unidade_codigo": unidade[2] if unidade else None,
        })
    return audios


def projeto() -> Dict:
    return {
        "id": 1,
        "name": PROJETO,
        "especialidade": "Lavanderias self-service",
        "historico": "Rede de 6 unidades, coleta por QR code nas máquinas.",
        "problemas": "O que faz o cliente voltar? Onde o atendimento falha?",
        "briefing_text": "Briefing longo " * 200,
    }
