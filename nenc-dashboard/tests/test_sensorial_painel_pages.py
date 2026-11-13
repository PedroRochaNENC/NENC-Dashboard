"""Painel de efeitos e ficha da amostra, executados com o AppTest do Streamlit.

O login exige conta real; aqui a guarda do módulo, a organização ativa e a
camada de dados são simuladas, e os projetos são sintéticos.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from tests.test_sensorial_effects import NO_OUTLIER_RULE, _sessions, _windows
from utils import auth, sensorial_cache, sensorial_db, sensorial_effects, sensorial_store, ui
from utils.sensorial_model import build_model

APP_ROOT = Path(__file__).resolve().parent.parent
PANEL = str(APP_ROOT / "modules" / "teste_sensorial" / "painel_efeitos.py")
PREPARATION = str(APP_ROOT / "modules" / "teste_sensorial" / "preparacao.py")
SAME_CONFIG = {"teste": "wilcoxon", "alfa": 0.05, "ppi": "spss", "base_limpa": True, "regra_spss": True}


def _user(platform_admin=False):
    return auth.User(id=1, name="Equipe", email="equipe@example.com", phone="5511900000000", organization_id=7,
                     organization_name="Org Sete", is_organization_admin=True, is_platform_admin=platform_admin,
                     is_active=True, modules=("teste_sensorial",))


def _effects(samples, measures=("FAI", "PPI"), result="diferença"):
    rows = []
    for code, label in samples:
        for stage, stage_label in (("Olfacao", "Olfação"), ("PosOlfacao", "Pós-olfação")):
            for measure in measures:
                rows.append({"condicao": code, "amostra": label, "controle": "Neutro", "etapa": stage,
                             "etapa_rotulo": stage_label, "medida": measure, "n": 8, "diferenca_media": 0.1,
                             "ic_inf": 0.0, "ic_sup": 0.2, "dp_diferenca": 0.12, "dz": 0.8, "dz_inf": 0.0,
                             "dz_sup": 1.6, "p": 0.04, "resultado": result})
    return pd.DataFrame(rows, columns=sensorial_effects.EFFECT_COLUMNS)


PROJECTS = [
    {"id": 1, "name": "Estudo Um", "organization_id": 7, "organization_name": "Org Sete", "categoria": "Perfumaria",
     "data_version": 1, "ficha_amostras": '{"A": {"base": "Cítrica", "claims_alvo": ["Energia"]}, '
                                          '"B": {"base": "Amadeirada", "claims_alvo": ["Relaxamento"]}}'},
    {"id": 2, "name": "Estudo Dois", "organization_id": 7, "organization_name": "Org Sete", "categoria": "",
     "data_version": 1, "ficha_amostras": '{"X": {"base": "Cítrica"}}'},
    {"id": 3, "name": "Estudo Sem Controle", "organization_id": 7, "organization_name": "Org Sete",
     "data_version": 1},
    {"id": 4, "name": "Estudo de Outra Org", "organization_id": 8, "organization_name": "Org Oito",
     "data_version": 1},
]
RESULTS = {
    1: {"efeitos": _effects([("A", "Amostra A"), ("B", "Amostra B")]), "motivo": "", "config": SAME_CONFIG},
    2: {"efeitos": _effects([("X", "Amostra X")], result="sem diferença"), "motivo": "",
        "config": dict(SAME_CONFIG, ppi="z")},
    3: {"efeitos": pd.DataFrame(columns=sensorial_effects.EFFECT_COLUMNS), "motivo": "sem controle no Desenho",
        "config": SAME_CONFIG},
    4: {"efeitos": _effects([("Z", "Amostra Z")]), "motivo": "", "config": SAME_CONFIG},
}


class _PageBase(unittest.TestCase):
    organization_id = 7

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        environment = patch.dict(os.environ, {"NENC_DB_PATH": str(self.folder / "nenc.db"),
                                              "NENC_SENSORIAL_DIR": str(self.folder / "sensorial")})
        environment.start()
        self.addCleanup(environment.stop)
        self.audit_export = MagicMock()
        for patcher in (
            patch.object(auth, "require_module", return_value=_user(self.organization_id == 0)),
            patch.object(auth, "can_write", return_value=True),
            patch.object(auth, "active_organization_id", return_value=self.organization_id),
            patch.object(ui, "inject_theme"),
            patch.object(ui, "breadcrumb"),
            patch.object(sensorial_db, "init_db"),
            patch.object(sensorial_db, "audit_export", self.audit_export),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _table(app):
        return app.dataframe[0].value


class PanelPageTests(_PageBase):
    def setUp(self):
        super().setUp()
        for patcher in (
            patch.object(sensorial_db, "list_projects", return_value=PROJECTS),
            patch.object(sensorial_cache, "get_project_effects", side_effect=lambda p: RESULTS[p["id"]]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self):
        app = AppTest.from_file(PANEL, default_timeout=60)
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        return app

    def test_the_panel_shows_every_sample_of_the_organization_one_chart_per_index(self):
        app = self._run()
        self.assertEqual(len(app.get("plotly_chart")), 2)
        table = self._table(app)
        self.assertEqual(set(table["Projeto"]), {"Estudo Um", "Estudo Dois"})
        self.assertEqual(set(table["Amostra"]), {"Amostra A", "Amostra B", "Amostra X"})
        self.assertEqual(set(table["Índice"]), {"Atratividade sensorial", "PPI – nota final"})
        self.assertNotIn("Estudo de Outra Org", set(table["Projeto"]))
        self.assertEqual([e.label for e in app.expander if e.label.startswith("Fora do painel")],
                         ["Fora do painel (1)"])
        self.assertIn("Estudo Sem Controle", " ".join(m.value for m in app.markdown))
        # O Estudo Dois usa o PPI padronizado: o painel avisa.
        self.assertTrue(any("Modo do PPI difere" in w.value for w in app.warning))

    def test_the_sheet_filters_narrow_the_table(self):
        app = self._run()
        app.multiselect(key="ts_panel_bases").set_value(["Cítrica"]).run()
        self.assertEqual(set(self._table(app)["Amostra"]), {"Amostra A", "Amostra X"})
        app.multiselect(key="ts_panel_claims").set_value(["Energia"]).run()
        self.assertEqual(set(self._table(app)["Amostra"]), {"Amostra A"})
        # Só projetos do recorte entram no aviso de configuração.
        self.assertFalse(any("difere" in w.value for w in app.warning))
        app.multiselect(key="ts_panel_measures").set_value(["FAI"]).run()
        self.assertEqual(len(app.get("plotly_chart")), 1)

    def test_the_scale_switches_the_effect_column(self):
        app = self._run()
        self.assertEqual(set(self._table(app)["Efeito"]), {0.8})
        app.radio(key="ts_panel_scale").set_value("bruta").run()
        self.assertEqual(set(self._table(app)["Efeito"]), {0.1})


class PanelAllOrganizationsTests(_PageBase):
    organization_id = 0

    def setUp(self):
        super().setUp()
        for patcher in (
            patch.object(sensorial_db, "list_projects", return_value=PROJECTS),
            patch.object(sensorial_cache, "get_project_effects", side_effect=lambda p: RESULTS[p["id"]]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_all_organizations_asks_for_one_and_never_mixes_them(self):
        app = AppTest.from_file(PANEL, default_timeout=60)
        app.run()
        self.assertFalse(app.exception)
        chooser = app.selectbox(key="ts_panel_org")
        self.assertEqual(list(chooser.options), ["Org Oito", "Org Sete"])
        self.assertEqual(set(self._table(app)["Projeto"]), {"Estudo de Outra Org"})
        chooser.set_value(7).run()
        self.assertEqual(set(self._table(app)["Projeto"]), {"Estudo Um", "Estudo Dois"})


class SampleSheetPageTests(_PageBase):
    def setUp(self):
        super().setUp()
        psd = _windows(_sessions(), per_stage=2, seed=5)
        path = sensorial_store.write_table(psd, self.folder / "psd.parquet")
        self.model = build_model({"project": {"id": 1, "data_version": 1}, "settings": NO_OUTLIER_RULE,
                                  "files": [{"id": 1, "role": "eeg_psd", "filename": "psd", "run_id": None,
                                             "meta": {}, "table_path": str(path)}],
                                  "participants": [], "sessions": []})
        self.project = {"id": 1, "name": "Estudo Um", "organization_id": 7, "categoria": "Perfumaria",
                        "data_version": 1, "created_by_user_id": 1, "settings_json": "{}",
                        "ficha_amostras": '{"A": {"base": "Cítrica", "claims_alvo": ["Energia", "Foco"]}}'}
        self.update = MagicMock(return_value=True)
        for patcher in (
            patch.object(sensorial_db, "get_project", return_value=self.project),
            patch.object(sensorial_db, "user_can_modify_project", return_value=True),
            patch.object(sensorial_db, "update_project", self.update),
            patch.object(sensorial_db, "list_files", return_value=[]),
            patch.object(sensorial_db, "study_report", return_value=None),
            patch.object(sensorial_cache, "get_project_model", return_value=self.model),
            patch.object(sensorial_cache, "get_project_metrics", return_value={}),
            patch("utils.sensorial_ai.claims_by_sample_code", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_design_tab_saves_the_sheet_of_each_sample(self):
        app = AppTest.from_file(PREPARATION, default_timeout=60)
        app.session_state["ts_project_id"] = 1
        app.run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        app.button(key="ts_save_sheets").click().run()
        self.assertFalse(app.exception, [e.value for e in app.exception])
        sheets = self.update.call_args.kwargs["ficha_amostras"]
        self.assertEqual(self.update.call_args.args, (1,))
        self.assertEqual(set(sheets), {"A", "B"})
        self.assertEqual(sheets["A"], {"base": "Cítrica", "categoria": "Perfumaria", "claims_alvo": ["Energia", "Foco"]})
        # Sem ficha, a categoria vem pré-preenchida com a do projeto.
        self.assertEqual(sheets["B"]["categoria"], "Perfumaria")
        self.assertEqual(sheets["B"]["claims_alvo"], [])


if __name__ == "__main__":
    unittest.main()
