"""
Prosódia — Timeline do Áudio (redirecionamento).

A Timeline e a Análise viraram uma página só, `audio.py` (tela 7a). Este
arquivo fica por um ciclo só para que links e saltos antigos (`_navigate_to`,
`st.switch_page`) caiam na página nova, com o foco do player preservado em
`pros_timeline_focus`. Pode ser removido na próxima revisão.
"""

import streamlit as st
from utils import auth

auth.require_module("prosodia")

st.switch_page("modules/prosodia/audio.py")
