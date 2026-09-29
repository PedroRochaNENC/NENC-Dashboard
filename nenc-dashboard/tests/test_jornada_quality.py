import json
import math
import unittest

import pandas as pd

from utils.jornada_quality import (
    check_recording,
    default_thresholds,
    run_quality,
    thresholds_for_project,
)


def _record(**changes):
    record = {
        "recording_key": "Pt01|livre|1234",
        "participant": "Pt01",
        "task": "livre",
        "has_frames": True,
        "hz": 23.3,
        "loss_pct": 1.0,
        "max_gap_s": 0.9,
        "duration_s": 200.0,
        "status": "incluida",
        "status_reason": "",
        "unit": "amostras",
        "unit_evidence": "comprimento do export = 4000 quadros",
        "conversion": "quadros da gravação",
    }
    record.update(changes)
    return record


def _statuses(checks):
    return {check["id"]: check["status"] for check in checks}


class ThresholdTests(unittest.TestCase):
    def test_the_task_changes_the_expected_duration(self):
        self.assertEqual(default_thresholds("livre")["min_duration_s"], 60.0)
        self.assertEqual(default_thresholds("estimulada")["min_duration_s"], 15.0)
        self.assertEqual(default_thresholds(None)["hz_warn"], 20.0)

    def test_project_overrides_for_all_tasks_and_for_one_task(self):
        project = {"quality_thresholds": json.dumps({"*": {"hz_warn": 18}, "livre": {"min_duration_s": 90}})}
        self.assertEqual(thresholds_for_project(project, "livre")["min_duration_s"], 90.0)
        self.assertEqual(thresholds_for_project(project, "livre")["hz_warn"], 18.0)
        self.assertEqual(thresholds_for_project(project, "estimulada")["min_duration_s"], 15.0)
        self.assertEqual(thresholds_for_project({"quality_thresholds": "{quebrado"}, "livre"),
                         default_thresholds("livre"))


class CheckTests(unittest.TestCase):
    def test_a_good_recording_passes(self):
        statuses = _statuses(check_recording(_record(), default_thresholds("livre"), "Farma"))
        self.assertEqual(set(statuses.values()), {"pass"})

    def test_low_sampling_rate_warns_and_very_low_fails(self):
        warn = _statuses(check_recording(_record(hz=13.5), default_thresholds("livre"), "x"))
        fail = _statuses(check_recording(_record(hz=8.0), default_thresholds("livre"), "x"))
        self.assertEqual(warn["taxa"], "warn")
        self.assertEqual(fail["taxa"], "fail")

    def test_uncoded_short_and_profile_less(self):
        statuses = _statuses(check_recording(
            _record(status="nao_codificada", duration_s=30.0), default_thresholds("livre"), "",
        ))
        self.assertEqual(statuses["codificacao"], "fail")
        self.assertEqual(statuses["duracao"], "warn")
        self.assertEqual(statuses["perfil"], "warn")
        self.assertNotIn("unidade", statuses)

    def test_unit_problems_are_visible(self):
        nominal = _statuses(check_recording(
            _record(conversion="Hz nominal do projeto"), default_thresholds("livre"), "x"))
        missing = _statuses(check_recording(
            _record(conversion="sem conversão"), default_thresholds("livre"), "x"))
        no_frames = _statuses(check_recording(
            _record(has_frames=False, hz=math.nan), default_thresholds("livre"), "x"))
        self.assertEqual(nominal["unidade"], "warn")
        self.assertEqual(missing["unidade"], "warn")
        self.assertEqual(no_frames["taxa"], "warn")
        self.assertNotIn("perda", no_frames)

    def test_run_quality_summarises_each_recording(self):
        model = {
            "recordings": pd.DataFrame([_record(), _record(recording_key="Pt02|livre|1234",
                                                            participant="Pt02", hz=12.0)]),
            "participants": pd.DataFrame({"participant": ["Pt01", "Pt02"], "profile": ["A", "B"]}),
        }
        result = run_quality(model, {})
        summary = result["summary"].set_index("recording_key")
        self.assertEqual(summary.loc["Pt01|livre|1234", "quality"], "pass")
        self.assertEqual(summary.loc["Pt02|livre|1234", "quality_label"], "Atenção")
        self.assertIn("Taxa de amostragem", summary.loc["Pt02|livre|1234", "alerts"])
        self.assertTrue(run_quality({"recordings": pd.DataFrame()})["checks"].empty)


if __name__ == "__main__":
    unittest.main()
