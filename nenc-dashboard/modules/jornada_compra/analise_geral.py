"""
Jornada de Compra — Análise Geral.

Página provisória: métricas, gráficos, IA e exportações entram nos próximos passos.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module("jornada_compra")

from utils import jornada_db
from utils.jornada_ui import active_project

jornada_db.init_db()
project = active_project()

ui.inject_theme()
ui.breadcrumb("Jornada de Compra", project["name"], "Análise Geral")
page_title("chart-bar", "Análise Geral", project["name"])
st.info("Em construção.")
