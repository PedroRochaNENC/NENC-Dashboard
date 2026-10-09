"""
Prosódia — Análise do Áudio (redirecionamento).

A Análise individual e a Timeline viraram uma página só, `audio.py` (tela
7a): análise por IA, verificação de qualidade e chat estão lá, com a mesma
lógica. Este arquivo fica por um ciclo só para que links e saltos antigos
caiam na página nova. Pode ser removido na próxima revisão.
"""

import streamlit as st
from utils import auth

auth.require_module("prosodia")

st.switch_page("modules/prosodia/audio.py")
