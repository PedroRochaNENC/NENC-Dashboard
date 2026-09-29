"""
Jornada de Compra — Uploads.

Página provisória: a ingestão dos arquivos entra no passo seguinte.
"""

import streamlit as st
from utils import auth, ui
from utils.icons import page_title

user = auth.require_module_write("jornada_compra")

from utils import jornada_db
from utils.jornada_ui import active_project

jornada_db.init_db()
project = active_project()

ui.inject_theme()
ui.breadcrumb("Jornada de Compra", project["name"], "Uploads")
page_title("upload-simple", "Uploads", "Arquivos de eye tracking, imagens e vídeos.")
st.info("Em construção.")
