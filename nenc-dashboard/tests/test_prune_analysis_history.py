import sqlite3
import unittest

from scripts.prune_analysis_history import _processar, excedentes


def _database_with_history() -> sqlite3.Connection:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute(
        """
        CREATE TABLE analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audio_id INTEGER NOT NULL,
            analysis_text TEXT,
            created_at TEXT
        )
        """
    )
    linhas = [
        (1, "mais antiga", "2026-01-01 10:00:00"),
        (1, "meio", "2026-02-01 10:00:00"),
        (1, "mais nova", "2026-03-01 10:00:00"),
        (2, "unica", "2026-01-15 10:00:00"),
    ]
    database.executemany(
        "INSERT INTO analyses (audio_id, analysis_text, created_at) VALUES (?, ?, ?)",
        linhas,
    )
    database.commit()
    return database


class PruneHistoryTests(unittest.TestCase):
    def setUp(self):
        self.database = _database_with_history()
        self.addCleanup(self.database.close)

    def test_the_oldest_rows_are_the_ones_that_go(self):
        alvo = excedentes(self.database, "analyses", "audio_id", 1)

        textos = [
            self.database.execute(
                "SELECT analysis_text FROM analyses WHERE id = ?", (linha_id,)
            ).fetchone()["analysis_text"]
            for linha_id in alvo[1]
        ]
        self.assertEqual(textos, ["meio", "mais antiga"])
        # Quem esta abaixo do limite nao aparece.
        self.assertNotIn(2, alvo)

    def test_nobody_loses_their_latest_analysis(self):
        with self.assertRaises(ValueError):
            excedentes(self.database, "analyses", "audio_id", 0)

    def test_applying_keeps_exactly_the_requested_amount(self):
        _processar(self.database, manter=2, aplicar=True)

        restantes = [
            row["analysis_text"]
            for row in self.database.execute(
                "SELECT analysis_text FROM analyses WHERE audio_id = 1 "
                "ORDER BY created_at DESC"
            )
        ]
        self.assertEqual(restantes, ["mais nova", "meio"])
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM analyses WHERE audio_id = 2"
            ).fetchone()[0],
            1,
        )

    def test_a_dry_run_changes_nothing(self):
        antes = self.database.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        resumo = _processar(self.database, manter=1, aplicar=False)

        self.assertEqual(resumo["analyses"], 2)
        self.assertEqual(
            self.database.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], antes
        )

    def test_missing_tables_are_skipped(self):
        # O banco do teste so tem `analyses`; as outras tres nao podem quebrar.
        resumo = _processar(self.database, manter=5, aplicar=False)
        self.assertEqual(set(resumo), {"analyses"})


if __name__ == "__main__":
    unittest.main()
