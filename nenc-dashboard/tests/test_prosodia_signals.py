"""Sinais do NencBoost que os prompts pedem e o pipeline precisa entregar.

Dominância e as quatro categorias de emoção sempre existiram no CSV
Sincronizado e nunca chegavam à IA: ficavam só nos gráficos. O prompt
estatístico, por sua vez, pedia "distribuição de emoções" — e o modelo
respondia, com razão, que ela não existia.

Estes testes seguram as duas pontas: as funções formatam os sinais, e o
prompt não pede nada que elas não saibam produzir.
"""

import unittest

import pandas as pd

from utils import prosodia_prompts
from utils.prosodia_prompts import (
    PROSODIA_PROJECT_SYSTEM_PROMPT_STATISTICAL,
    PROSODIA_SYSTEM_PROMPT_STATISTICAL,
    build_project_user_prompt,
    build_prosodia_user_prompt,
)
from utils.prosodia_signals import (
    ACUSTICAS,
    DIMENSOES,
    EMOCOES,
    MIN_LINHAS_POR_LOCUTOR,
    ReferenciaVoz,
    detectar_divergencias,
    divergencias_texto,
    emotion_distribution_text,
    indice_combinado_por_grupo,
    indice_combinado_por_trecho,
    indice_combinado_texto,
    momentos_alta_ativacao,
    montar_evidencias_audio,
    referencia_valencia,
    signals_block,
    speaker_acoustics_text,
    texto_sentimento_resumo,
)


def frame(**overrides) -> pd.DataFrame:
    base = {
        "session_id": ["a", "a", "b", "b"],
        "SpeakerName": ["Entrevistado", "Entrevistado", "Entrevistado", "Entrevistado"],
        "f0_media": [210.0, 190.0, 160.0, 170.0],
        "f0_variacao": [40.0, 35.0, 20.0, 25.0],
        "loudness_media": [0.5, 0.6, 0.2, 0.3],
        "loudness_variacao": [0.3, 0.2, 0.1, 0.1],
        "speaking_rate": [5.0, 4.5, 3.5, 4.0],
        "dim_arousal": [0.4, 0.2, -0.3, -0.1],
        "dim_valence": [0.3, 0.1, -0.4, -0.2],
        "dim_dominance": [0.2, 0.1, -0.2, -0.1],
        "emocao_happy": [0.7, 0.6, 0.1, 0.1],
        "emocao_neutral": [0.2, 0.3, 0.3, 0.2],
        "emocao_sad": [0.05, 0.05, 0.5, 0.6],
        "emocao_angry": [0.05, 0.05, 0.1, 0.1],
    }
    base.update(overrides)
    return pd.DataFrame(base)


class EmotionDistributionTests(unittest.TestCase):
    def test_reports_the_share_of_segments_per_category(self):
        texto = emotion_distribution_text(frame(), "session_id", "Áudio")

        self.assertIn("Alegria", texto)
        self.assertIn("Tristeza", texto)
        # O audio 'a' e alegre nos dois segmentos, o 'b' e triste nos dois.
        linha_a = next(l for l in texto.splitlines() if l.startswith("| a |"))
        linha_b = next(l for l in texto.splitlines() if l.startswith("| b |"))
        self.assertIn("100%", linha_a)
        self.assertTrue(linha_a.rstrip().endswith("Alegria |"))
        self.assertTrue(linha_b.rstrip().endswith("Tristeza |"))

    def test_says_so_instead_of_inventing_when_there_is_nothing(self):
        self.assertIn("Nenhum dado", emotion_distribution_text(pd.DataFrame()))

        sem_colunas = pd.DataFrame({"session_id": ["a"], "f0_media": [200.0]})
        self.assertIn("Nenhuma categoria", emotion_distribution_text(sem_colunas))

    def test_columns_present_but_empty_are_reported_as_empty(self):
        vazio = frame(**{c: [None] * 4 for c, _ in EMOCOES})

        texto = emotion_distribution_text(vazio)

        self.assertIn("vazias", texto)

    def test_carries_the_probabilistic_caveat(self):
        self.assertIn("probabilísticas", emotion_distribution_text(frame()))


class SpeakerAcousticsTests(unittest.TestCase):
    def test_brings_the_three_dimensions_and_the_acoustics(self):
        texto = speaker_acoustics_text(frame())

        for _, rotulo in ACUSTICAS + DIMENSOES:
            self.assertIn(rotulo, texto)

    def test_dominance_is_not_left_behind(self):
        """Estava no CSV e no gráfico da timeline, nunca no prompt."""
        self.assertIn("Dominância", speaker_acoustics_text(frame()))
        self.assertIn("Dominância", signals_block(frame()))

    def test_averages_per_speaker(self):
        df = frame(SpeakerName=["Ana", "Ana", "Bruno", "Bruno"])

        texto = speaker_acoustics_text(df)

        self.assertIn("| Ana |", texto)
        self.assertIn("| Bruno |", texto)
        self.assertIn("200.000", texto)  # media de f0 da Ana

    def test_missing_metrics_do_not_break_the_table(self):
        df = frame().drop(columns=["dim_dominance", "speaking_rate"])

        texto = speaker_acoustics_text(df)

        self.assertNotIn("Dominância", texto)
        self.assertIn("Valência", texto)

    def test_says_so_instead_of_inventing_when_there_is_nothing(self):
        self.assertIn("Nenhuma métrica", speaker_acoustics_text(pd.DataFrame()))


class PromptAsksOnlyForWhatWeSendTests(unittest.TestCase):
    """O prompt nao pode pedir sinal que o pipeline nao sabe produzir."""

    def test_the_emotion_categories_the_prompt_names_are_the_ones_we_format(self):
        rotulos = [rotulo for _, rotulo in EMOCOES]
        for prompt in (
            PROSODIA_SYSTEM_PROMPT_STATISTICAL,
            PROSODIA_PROJECT_SYSTEM_PROMPT_STATISTICAL,
        ):
            with self.subTest(prompt=prompt[:40]):
                for rotulo in rotulos:
                    self.assertIn(rotulo, prompt)

    def test_the_categories_named_in_the_prompt_really_come_out(self):
        texto = emotion_distribution_text(frame())
        for rotulo in (rotulo for _, rotulo in EMOCOES):
            self.assertIn(rotulo, texto)

    def test_the_prompt_asks_for_dominance_and_we_deliver_it(self):
        for prompt in (
            PROSODIA_SYSTEM_PROMPT_STATISTICAL,
            PROSODIA_PROJECT_SYSTEM_PROMPT_STATISTICAL,
        ):
            with self.subTest(prompt=prompt[:40]):
                self.assertIn("dominância", prompt.lower())
        self.assertIn("Dominância", signals_block(frame()))


    def test_the_prompts_that_read_the_sentiment_sections_get_them(self):
        """<sentimento_texto> e <divergencias> saem dos builders quando há dado."""
        prompt = build_prosodia_user_prompt(
            "tabelas", {}, "", sentimento_texto="resumo", divergencias="tabela"
        )
        self.assertIn("<sentimento_texto>\nresumo\n</sentimento_texto>", prompt)
        self.assertIn("<divergencias>\ntabela\n</divergencias>", prompt)

        projeto = build_project_user_prompt(
            {}, "metricas", "", "", "", sentimento_texto="resumo", divergencias="tabela"
        )
        self.assertIn("<sentimento_texto>", projeto)
        self.assertIn("<divergencias>", projeto)

    def test_without_sentiment_the_sections_disappear(self):
        for prompt in (
            build_prosodia_user_prompt("tabelas", {}, "amostra"),
            build_project_user_prompt({}, "metricas", "palavras", "momentos", "analises"),
        ):
            self.assertNotIn("<sentimento_texto>", prompt)
            self.assertNotIn("<divergencias>", prompt)

    def test_every_system_prompt_explains_the_sections_and_not_to_invent_them(self):
        for tipo in (None, "pesquisa_opiniao"):
            for modo in ("rapida", "estatistica", "estrategica"):
                for obter in (
                    prosodia_prompts.get_prosodia_system_prompt,
                    prosodia_prompts.get_prosodia_project_system_prompt,
                ):
                    with self.subTest(tipo=tipo, modo=modo, obter=obter.__name__):
                        prompt = obter(tipo, modo)
                        self.assertIn("<divergencias>", prompt)
                        self.assertIn("não as invente", prompt)


# ---------------------------------------------------------------------------
# Sentimento do texto e divergência voz × texto
# ---------------------------------------------------------------------------

def transcricao(**overrides) -> pd.DataFrame:
    base = {
        "session_id": ["a", "a", "a", "b"],
        "SpeakerName": ["A", "A", "B", "A"],
        "Text": ["adorei tudo", "ok", "que demora horrível", "bom"],
        "Timestamp": ["00:00:01", "00:00:05", "00:00:09", "00:00:02"],
        "start_s": [1.0, 5.0, 9.0, 2.0],
        "end_s": [4.0, 6.0, 12.0, 3.0],
        "sentimento_texto": [0.8, 0.0, -0.9, 0.3],
        "sentimento_rotulo": ["positivo", "neutro", "negativo", "positivo"],
        "sentimento_justificativa": ["elogio claro", "", "reclama do prazo", "aprova"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def sincronizado(valencias, notas, speaker="A", session="s1", cortado=False, inicio_idx=0):
    """Uma linha do VAD por valência, cada uma num segmento próprio."""
    n = len(valencias)
    return pd.DataFrame({
        "session_id": [session] * n,
        "SpeakerName": [speaker] * n if isinstance(speaker, str) else speaker,
        "start_s": [float(i) for i in range(n)],
        "end_s": [i + 0.9 for i in range(n)],
        "duracao_s": [0.9] * n,
        "Timestamp": ["00:00:{:02d}.00".format(i) for i in range(n)],
        "Text": ["fala {}".format(i) for i in range(n)],
        "dim_valence": valencias,
        "segmento_idx": list(range(inicio_idx, inicio_idx + n)),
        "sentimento_texto": notas,
        "sentimento_rotulo": ["positivo" if x > 0.2 else ("negativo" if x < -0.2 else "neutro") for x in notas],
        "sentimento_justificativa": ["j{}".format(i) for i in range(n)],
        "audio_cortado": [cortado] * n,
    })


BASE = [0.0, 0.05, -0.05, 0.02, -0.02, 0.0, 0.03, -0.03, 0.01]


class TextoSentimentoResumoTests(unittest.TestCase):

    def test_media_ponderada_pela_duracao_e_fatias_por_locutor(self):
        texto = texto_sentimento_resumo(transcricao(session_id=["a"] * 4))

        linha_a = next(l for l in texto.splitlines() if l.startswith("| A |"))
        # A: 0.8 por 3s, 0.0 por 1s, 0.3 por 1s -> 0.54; 80% do tempo positivo.
        self.assertIn("+0.54", linha_a)
        self.assertIn("80%", linha_a)
        self.assertIn("Trechos mais positivos", texto)
        self.assertIn("reclama do prazo", texto)
        self.assertIn("inferência automática", texto)

    def test_por_audio_marca_o_audio_de_cada_trecho(self):
        texto = texto_sentimento_resumo(transcricao(), "session_id", "Áudio")

        self.assertIn("| Áudio |", texto)
        self.assertIn("[a]", texto)

    def test_sem_nota_nenhuma_nao_ha_secao(self):
        self.assertEqual(texto_sentimento_resumo(transcricao(sentimento_texto=[None] * 4)), "")
        self.assertEqual(texto_sentimento_resumo(frame()), "")


class DetectarDivergenciasTests(unittest.TestCase):

    def test_texto_positivo_com_voz_bem_abaixo_do_habitual(self):
        df = sincronizado(BASE + [-0.6], [0.0] * 9 + [0.8])

        div = detectar_divergencias(df)

        self.assertEqual(div["segmento_idx"].tolist(), [9])
        self.assertEqual(div.loc[0, "tipo"], "texto positivo × voz negativa")
        self.assertLessEqual(div.loc[0, "z_valencia"], -1.0)
        self.assertEqual(div.loc[0, "start_s"], 9.0)

    def test_texto_negativo_com_voz_bem_acima(self):
        div = detectar_divergencias(sincronizado(BASE + [0.6], [0.0] * 9 + [-0.7]))

        self.assertEqual(div.loc[0, "tipo"], "texto negativo × voz positiva")

    def test_limiares(self):
        # Texto morno (|nota| < 0,5) e voz só um pouco abaixo do habitual.
        self.assertTrue(detectar_divergencias(sincronizado(BASE + [-0.6], [0.0] * 9 + [0.4])).empty)
        # BASE tem desvio de ~0,03: -0,015 fica a meio desvio do habitual.
        self.assertTrue(detectar_divergencias(sincronizado(BASE + [-0.015], [0.0] * 9 + [0.9])).empty)
        # Mesmo sentido não é divergência.
        self.assertTrue(detectar_divergencias(sincronizado(BASE + [0.6], [0.0] * 9 + [0.9])).empty)

    def test_locutor_com_poucas_linhas_fica_sem_z(self):
        poucas = BASE[: MIN_LINHAS_POR_LOCUTOR - 2] + [-0.6]

        self.assertTrue(detectar_divergencias(sincronizado(poucas, [0.0] * (len(poucas) - 1) + [0.8])).empty)

    def test_z_por_locutor_e_pelo_audio_inteiro_quando_cortado(self):
        valencias_a = [0.50, 0.52, 0.48, 0.51, 0.49, 0.50, 0.53, 0.47, 0.50, 0.51, 0.49, 0.50]
        valencias_b = [-0.50, -0.52, -0.48, -0.51, -0.49, -0.50, -0.53, -0.47]
        notas = [0.0] * 12 + [0.8] + [0.0] * 7

        def audio(cortado):
            return sincronizado(valencias_a + valencias_b, notas, speaker=["A"] * 12 + ["B"] * 8, cortado=cortado)

        # Para B, -0,50 é o habitual: sem divergência.
        self.assertTrue(detectar_divergencias(audio(False)).empty)
        # Com o áudio cortado os rótulos não valem e a referência é o áudio.
        self.assertEqual(detectar_divergencias(audio(True))["segmento_idx"].tolist(), [12])

    def test_cada_audio_e_sua_propria_referencia(self):
        s1 = sincronizado([0.5 + d for d in BASE] + [0.5], [0.0] * 10)
        s2 = sincronizado([-0.5 + d for d in BASE] + [-0.5], [0.0] * 9 + [0.8], session="s2", inicio_idx=0)

        self.assertTrue(detectar_divergencias(pd.concat([s1, s2], ignore_index=True)).empty)

    def test_varias_linhas_do_vad_no_mesmo_segmento_contam_uma_vez(self):
        df = sincronizado(BASE + [-0.6, -0.6], [0.0] * 9 + [0.8, 0.8])
        df.loc[10, "segmento_idx"] = 9

        self.assertEqual(detectar_divergencias(df)["segmento_idx"].tolist(), [9])

    def test_csv_antigo_sem_sentimento(self):
        self.assertTrue(detectar_divergencias(frame()).empty)

    def test_tabela_para_o_prompt(self):
        texto = divergencias_texto(detectar_divergencias(sincronizado(BASE + [-0.6], [0.0] * 9 + [0.8])))

        self.assertIn("texto positivo × voz negativa", texto)
        self.assertIn("j9", texto)
        self.assertIn("não prova", texto)
        self.assertIn("Nenhuma divergência", divergencias_texto(pd.DataFrame()))


class IndiceCombinadoTests(unittest.TestCase):

    def test_regua_do_projeto(self):
        ref = referencia_valencia(sincronizado(BASE + [-0.6], [0.0] * 10))

        self.assertAlmostEqual(ref.media, sum(BASE + [-0.6]) / 10)
        self.assertGreater(ref.desvio, 0)
        self.assertEqual((ref.linhas, ref.audios), (10, 1))

    def test_sem_regua_quando_faltam_linhas_ou_variacao(self):
        self.assertIsNone(referencia_valencia(sincronizado(BASE[:3], [0.0] * 3)))
        self.assertIsNone(referencia_valencia(sincronizado([0.1] * 10, [0.0] * 10)))
        self.assertIsNone(referencia_valencia(pd.DataFrame()))

    def test_metade_texto_metade_voz_com_voz_limitada(self):
        ref = ReferenciaVoz(media=0.0, desvio=0.1, linhas=100, audios=3)
        # z = +1 -> voz +0,5; z = -5 -> voz limitada a -1.
        trechos = indice_combinado_por_trecho(sincronizado([0.1, -0.5], [0.8, 0.0]), ref)

        self.assertAlmostEqual(trechos.loc[0, "voz"], 0.5)
        self.assertAlmostEqual(trechos.loc[0, "indice"], 0.65)
        self.assertAlmostEqual(trechos.loc[1, "voz"], -1.0)
        self.assertAlmostEqual(trechos.loc[1, "indice"], -0.5)

    def test_regua_do_projeto_separa_audios_que_o_z_por_locutor_igualaria(self):
        s1 = sincronizado([0.5 + d for d in BASE], [0.0] * 9)
        s2 = sincronizado([-0.5 + d for d in BASE], [0.0] * 9, session="s2")
        projeto = pd.concat([s1, s2], ignore_index=True)

        tabela = indice_combinado_por_grupo(projeto, referencia_valencia(projeto), "session_id")

        por_audio = tabela.set_index("grupo")["indice"]
        self.assertGreater(por_audio["s1"], 0.2)
        self.assertLess(por_audio["s2"], -0.2)
        self.assertAlmostEqual(tabela["texto"].abs().max(), 0.0)

    def test_varias_linhas_do_vad_no_mesmo_segmento_viram_um_trecho(self):
        ref = ReferenciaVoz(media=0.0, desvio=0.1, linhas=100, audios=1)
        df = sincronizado([0.1, 0.3], [0.4, 0.4])
        df["segmento_idx"] = [7, 7]

        trechos = indice_combinado_por_trecho(df, ref)

        # Cada linha é limitada antes da média: +0,5 e +1,5 -> 1,0 dão +0,75.
        self.assertEqual(len(trechos), 1)
        self.assertAlmostEqual(trechos.loc[0, "voz"], 0.75)

    def test_trecho_sem_texto_ou_sem_regua_fica_de_fora(self):
        ref = ReferenciaVoz(media=0.0, desvio=0.1, linhas=100, audios=1)

        self.assertEqual(len(indice_combinado_por_trecho(sincronizado([0.1, 0.2], [0.5, float("nan")]), ref)), 1)
        self.assertTrue(indice_combinado_por_trecho(sincronizado([0.1], [0.5]), None).empty)
        self.assertTrue(indice_combinado_por_grupo(frame(), ref).empty)
        self.assertEqual(indice_combinado_texto(frame(), ref), "")

    def test_evidencias_levam_o_indice_so_com_a_regua(self):
        sinc = sincronizado(BASE + [-0.6], [0.0] * 9 + [0.8])

        sem = montar_evidencias_audio(pd.DataFrame(), sinc, sinc)
        com = montar_evidencias_audio(pd.DataFrame(), sinc, sinc, referencia_voz=referencia_valencia(sinc))

        self.assertNotIn("Índice Combinado", sem.sentimento_texto)
        self.assertIn("Índice Combinado", com.sentimento_texto)
        self.assertIn("| A |", com.sentimento_texto.split("Índice Combinado")[1])
        self.assertIn("não como medida absoluta", com.sentimento_texto)

    def test_prompt_explica_o_indice(self):
        self.assertIn("Índice Combinado de Sentimento", prosodia_prompts.PROSODIA_SENTIMENTO_TEXTO)


class MomentosEEvidenciasTests(unittest.TestCase):

    def test_momentos_usam_o_inicio_do_vad_e_nao_repetem_o_segmento(self):
        df = sincronizado([0.0] * 6, [0.0] * 6)
        df["dim_arousal"] = [0.9, 0.8, 0.7, 0.1, 0.1, 0.1]
        df["f0_variacao"] = [30.0, 20.0, 10.0, 1.0, 1.0, 1.0]
        df["loudness_variacao"] = [0.3, 0.2, 0.1, 0.0, 0.0, 0.0]
        df["segmento_idx"] = [4, 4, 5, 6, 7, 8]
        df["start_s"] = [12.5, 14.0, 20.0, 30.0, 40.0, 50.0]

        momentos = momentos_alta_ativacao(df)

        self.assertEqual([m["segmento_idx"] for m in momentos][:2], [4, 5])
        self.assertEqual(momentos[0]["seconds"], 12.5)
        self.assertEqual(momentos[0]["Timestamp"], "00:00:12")

    def test_evidencias_com_csv_antigo_nao_inventam_sentimento(self):
        tr = pd.DataFrame({"SpeakerName": ["A"], "Text": ["ola"], "word_count": [1]})
        vad = pd.DataFrame({"duration": [1.5, 2.0]})

        evidencias = montar_evidencias_audio(vad, tr, frame())

        self.assertIn("VAD: 2 segmentos", evidencias.tabelas)
        self.assertIn("Participação por locutor", evidencias.tabelas)
        self.assertIn("Perfil Acústico", evidencias.tabelas)
        self.assertEqual(evidencias.sentimento_texto, "")
        self.assertEqual(evidencias.divergencias, "")

    def test_evidencias_com_sentimento(self):
        sinc = sincronizado(BASE + [-0.6], [0.0] * 9 + [0.8])
        tr = sinc.rename(columns={}).drop(columns=["dim_valence"])

        evidencias = montar_evidencias_audio(pd.DataFrame(), tr, sinc, momentos=[{"Text": "x", "dim_arousal": 0.9}])

        self.assertIn("| A |", evidencias.sentimento_texto)
        self.assertIn("texto positivo × voz negativa", evidencias.divergencias)
        self.assertIn("Momentos de Maior Ativação", evidencias.tabelas)

    def test_audio_cortado_nao_resume_por_locutor(self):
        sinc = sincronizado(BASE + [-0.6], [0.0] * 9 + [0.8], cortado=True)

        evidencias = montar_evidencias_audio(pd.DataFrame(), sinc, sinc)

        self.assertIn("| Todos |", evidencias.sentimento_texto)
        self.assertIn("rótulos de locutor", evidencias.sentimento_texto)


if __name__ == "__main__":
    unittest.main()
