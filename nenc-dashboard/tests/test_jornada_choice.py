"""Registro de campo: escolha, consideração e tempo de compra em texto livre.

Os textos imitam o jeito de anotar da equipe de campo (marcas citadas em
qualquer ordem, "diurno e noturno", erros de digitação, "pct c/32") com marcas
genéricas.
"""

import io
import math
import unittest

import pandas as pd

from utils.jornada_choice import (
    describe_values,
    find_brands,
    find_packs,
    find_values,
    parse_choice,
    parse_considered,
    parse_fraction,
)
from utils.jornada_ingest import detect_kind, parse_upload

BRANDS = ["Alfa", "Beta", "Gama Livre", "Livre"]
DIMENSIONS = {"tipo": ["Diurno", "Noturno"], "cobertura": ["Seco", "Suave"]}


class TextTests(unittest.TestCase):
    def test_brands_come_in_text_order_and_the_longest_name_wins(self):
        text = "Beta, Alfa, Gama Livre ( comparou os preços e pegou o pct com 32 do Alfa)"
        self.assertEqual(find_brands(text, BRANDS), ["Beta", "Alfa", "Gama Livre"])
        self.assertEqual(find_brands("só Livre", BRANDS), ["Livre"])
        self.assertEqual(find_brands("nenhuma marca", BRANDS), [])

    def test_attribute_values_accept_field_typos(self):
        self.assertEqual(find_values("Alfa diurno e noturno", DIMENSIONS), {"tipo": ["Diurno", "Noturno"]})
        self.assertEqual(find_values("alfa noturo e diruno", DIMENSIONS), {"tipo": ["Noturno", "Diurno"]})
        self.assertEqual(find_values("suave", DIMENSIONS), {"cobertura": ["Suave"]})

    def test_pack_sizes_in_any_way_the_team_writes_them(self):
        self.assertEqual(find_packs("pct de 32 e 48 und"), [32, 48])
        self.assertEqual(find_packs("Gama Livre diurno pct c/16 und"), [16])
        self.assertEqual(find_packs("ambos pct com 8"), [8])
        self.assertEqual(find_packs("pegou o pct com 32 abs noturno"), [32])
        self.assertEqual(find_packs("sem número"), [])

    def test_the_choice_borrows_the_variant_from_the_other_record(self):
        choice = parse_choice("Beta", BRANDS, DIMENSIONS, fallback="Beta Diurno")
        self.assertEqual(choice["values"], {"tipo": ["Diurno"]})
        # Marca diferente no outro registro: nada é emprestado.
        other = parse_choice("Beta", BRANDS, DIMENSIONS, fallback="Alfa Diurno")
        self.assertEqual(other["values"], {})
        line = parse_choice("Beta Toda Protegida", BRANDS, DIMENSIONS, lines=["Toda Protegida"])
        self.assertEqual((line["brands"], line["lines"]), (["Beta"], ["Toda Protegida"]))

    def test_considered_brands_packs_and_values(self):
        considered = parse_considered("Beta, Alfa, Gama Livre (pegou o pct com 32 abs noturno do Alfa)",
                                      BRANDS, DIMENSIONS)
        self.assertEqual(considered["brands"], ["Beta", "Alfa", "Gama Livre"])
        self.assertEqual(considered["packs"], [32])
        self.assertEqual(describe_values(considered["values"]), "tipo: Noturno")

    def test_hand_written_fractions(self):
        self.assertEqual(parse_fraction("0.0737"), (0.0737, ""))
        self.assertEqual(parse_fraction("13, 33%"), (0.1333, ""))
        self.assertEqual(parse_fraction("13,33"), (0.1333, ""))
        value, note = parse_fraction("tinha na jornada, mas não comprou")
        self.assertTrue(math.isnan(value))
        self.assertIn("não comprou", note)


def _field_log() -> bytes:
    control = pd.DataFrame({
        "DIA": ["2026-09-01 00:00:00"] * 4,
        "LOJA": ["DGSP1234", "DGSP1234", "ATACADO", "ATACADO"],
        "PARTICIPANTE": ["PT01", "PT02", "PT03", "PT04"],
        "PERFIL": ["PERFIL 1", "PERFIL 1", "PERFIL 2", "PERFIL 2"],
        "JORNADA LIVRE": ["ok", "ok", "", ""],
        "JORNADA ESTIMULADA": ["", "ok", "ok", "ok"],
        "EMBALAGEM": ["ok", "ok", "ok", ""],
        "PRODUTO ESCOLHIDO": ["Alfa diurno e noturno", "Beta Diurno", "Gama Livre", "Alfa"],
        "% PRODUTO/\nJORNADA LIVRE": ["0.07", "13, 33%", "", ""],
        "OBSERVAÇÕES": ["anda pela loja", "vai direto", "compara preço", ""],
    })
    stimulated = pd.DataFrame({
        "DIA": ["2026-09-01 00:00:00"] * 3,
        "LOJA": ["DGSP1234", "ATACADO", "ATACADO"],
        "PARTICIPANTE": ["PT02", "PT03", "PT04"],
        "PERFIL": ["PERFIL 1", "PERFIL 2", "PERFIL 2"],
        "OBSERVAÇÕES": ["pega rápido", "observa preço", ""],
        "MARCAS CONSIDERADAS DURANTE A COMPRA": ["Beta pct com 8", "Alfa, Gama Livre pct c/32", "Alfa"],
        "PRODUTO ESCOLHIDO": ["Beta", "Gama Livre noturno", "Alfa"],
        "TEMPO DE  COMPRA": ["19s", "1m21s", ""],
    })
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        # Como a equipe preenche: uma linha e uma coluna em branco antes do cabeçalho.
        control.to_excel(writer, sheet_name="Controle", startrow=1, startcol=1, index=False)
        stimulated.to_excel(writer, sheet_name="Estimuladas", startrow=1, startcol=1, index=False)
    return buffer.getvalue()


class FieldLogTests(unittest.TestCase):
    def setUp(self):
        self.content = _field_log()
        self.parsed = parse_upload("Relação Coletas.xlsx", self.content)

    def test_the_field_log_is_detected_even_with_blank_rows_on_top(self):
        self.assertEqual(detect_kind("Relação Coletas.xlsx", self.content), "field_log")
        self.assertTrue(self.parsed.ok, self.parsed.issues)
        self.assertEqual(self.parsed.kind, "field_log")

    def test_the_stimulated_choice_goes_to_the_stimulated_task(self):
        table = self.parsed.table.set_index(["participant", "task"])
        self.assertEqual(table.loc[("Pt02", "estimulada"), "chosen_text"], "Beta")
        self.assertEqual(table.loc[("Pt02", "estimulada"), "chosen_fallback"], "Beta Diurno")
        self.assertEqual(table.loc[("Pt02", "estimulada"), "purchase_time_s"], 19.0)
        self.assertEqual(table.loc[("Pt03", "estimulada"), "purchase_time_s"], 81.0)
        self.assertTrue(math.isnan(table.loc[("Pt04", "estimulada"), "purchase_time_s"]))
        self.assertEqual(table.loc[("Pt03", "estimulada"), "store"], "atacado")

    def test_free_journey_only_participants_keep_their_choice_in_the_free_task(self):
        table = self.parsed.table.set_index(["participant", "task"])
        self.assertEqual(table.loc[("Pt01", "livre"), "chosen_text"], "Alfa diurno e noturno")
        self.assertEqual(table.loc[("Pt01", "livre"), "category_fraction_text"], "0.07")
        # Quem fez as duas tarefas: a livre fica sem escolha, mas com a fração da categoria.
        self.assertEqual(table.loc[("Pt02", "livre"), "chosen_text"], "")
        self.assertEqual(table.loc[("Pt02", "livre"), "category_fraction_text"], "13, 33%")
        self.assertNotIn(("Pt03", "livre"), table.index)
        self.assertEqual(self.parsed.meta["n_choices"], 4)

    def test_general_notes_and_tasks_stay_with_the_participant(self):
        info = {item["code"]: item for item in self.parsed.meta["participant_info"]}
        self.assertEqual(info["Pt01"]["field_notes"], "anda pela loja")
        self.assertEqual(info["Pt01"]["profile_group"], "PERFIL 1")
        self.assertEqual(info["Pt02"]["tasks_done"], ["embalagens", "estimulada", "livre"])
        self.assertEqual(info["Pt04"]["tasks_done"], ["estimulada"])


if __name__ == "__main__":
    unittest.main()
