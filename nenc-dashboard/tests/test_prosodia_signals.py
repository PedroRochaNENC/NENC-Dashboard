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

from utils.prosodia_prompts import (
    PROSODIA_PROJECT_SYSTEM_PROMPT_STATISTICAL,
    PROSODIA_SYSTEM_PROMPT_STATISTICAL,
)
from utils.prosodia_signals import (
    ACUSTICAS,
    DIMENSOES,
    EMOCOES,
    emotion_distribution_text,
    signals_block,
    speaker_acoustics_text,
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


if __name__ == "__main__":
    unittest.main()
