"""
Scratch script to verify database creation and CRUD operations for Jornada de Compra and Teste Sensorial.
"""

import sys
from pathlib import Path
import pandas as pd

# Path setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import jornada_db, teste_sensorial_db, auth

def test_jornada_db():
    print("Testing Jornada DB...")
    jornada_db.init_db()

    # Create project
    pid = jornada_db.create_project(
        name="Projeto Teste Gôndola Cafe",
        categoria="Alimentos e Bebidas",
        historico="Histórico do projeto de teste",
        problemas="Quais marcas atritam mais atenção?",
        marcas="Marca A, Marca B",
    )
    print(f"Created Jornada project with ID: {pid}")

    proj = jornada_db.get_project(pid)
    assert proj is not None
    assert proj["name"] == "Projeto Teste Gôndola Cafe"
    print("Retrieved project details successfully.")

    # Save dataset
    df_tabelas = pd.DataFrame({"Participante": ["P1", "P2"], "AOI": ["Gondola_A", "Gondola_B"], "FixationCount": [12, 18]})
    df_por_marca = pd.DataFrame({"Marca": ["Marca A", "Marca B"], "TotalGazeDuration": [1.45, 2.30]})
    jornada_db.save_dataset(pid, tabelas=df_tabelas, por_marca=df_por_marca)
    print("Saved dataset successfully.")

    retrieved_data = jornada_db.get_dataset(pid)
    assert "tabelas" in retrieved_data
    assert "por_marca" in retrieved_data
    assert len(retrieved_data["tabelas"]) == 2
    print("Retrieved dataset DataFrames successfully with matching row count.")

    # Save interview
    iid = jornada_db.save_interview(pid, titulo="Entrevista P1", texto="Achei o café muito visível.", participante_id="P1")
    print(f"Saved interview with ID: {iid}")
    interviews = jornada_db.get_interviews(pid)
    assert len(interviews) == 1

    # Save analysis
    aid = jornada_db.save_analysis(pid, analysis_text="Relatório de IA gerado com sucesso.", model="gpt-4.1-mini")
    print(f"Saved analysis with ID: {aid}")
    analyses = jornada_db.get_analyses(pid)
    assert len(analyses) == 1

    # Delete project
    deleted = jornada_db.delete_project(pid)
    assert deleted
    print("Deleted project successfully. Jornada DB test PASSED!")


def test_teste_sensorial_db():
    print("\nTesting Teste Sensorial DB...")
    teste_sensorial_db.init_db()

    # Create project
    pid = teste_sensorial_db.create_project(
        name="Projeto Sensorial Chocolate 70%",
        produto_estimulo="Chocolate Amargo 70%",
        historico="Avaliação de resposta de valência emocional",
        questions="O produto desperta resposta positiva em AWI?",
    )
    print(f"Created Teste Sensorial project with ID: {pid}")

    proj = teste_sensorial_db.get_project(pid)
    assert proj is not None
    assert proj["name"] == "Projeto Sensorial Chocolate 70%"

    # Save dataset
    df_ind = pd.DataFrame({"participante": ["P1", "P2"], "etapa": ["Consumo", "Consumo"], "atencao": [0.85, 0.91]})
    df_per = pd.DataFrame({"participante": ["P1", "P2"], "etapa": ["Consumo", "Consumo"], "GSR": [1.2, 1.5]})
    teste_sensorial_db.save_dataset(pid, indicadores=df_ind, perifericos=df_per)
    print("Saved sensory dataset successfully.")

    retrieved_data = teste_sensorial_db.get_dataset(pid)
    assert "indicadores" in retrieved_data
    assert "perifericos" in retrieved_data
    assert len(retrieved_data["indicadores"]) == 2
    print("Retrieved sensory dataset DataFrames successfully.")

    # Save analysis
    aid = teste_sensorial_db.save_analysis(pid, analysis_text="Análise neurocientífica sensorial concluída.", model="gpt-4.1-mini")
    print(f"Saved sensory analysis with ID: {aid}")
    analyses = teste_sensorial_db.get_analyses(pid)
    assert len(analyses) == 1

    # Delete project
    deleted = teste_sensorial_db.delete_project(pid)
    assert deleted
    print("Deleted project successfully. Teste Sensorial DB test PASSED!")


if __name__ == "__main__":
    test_jornada_db()
    test_teste_sensorial_db()
