"""
Paleta e template Plotly do NENC Insights.

As cores e o template "nenc" valem para os gráficos de todos os módulos
(`utils/chart_kit.py`, `utils/prosodia_charts.py`). As fábricas de gráfico do
Teste Sensorial que viviam aqui saíram com a versão antiga do módulo; as novas
ficam em `utils/sensorial_charts.py`.
"""

import plotly.graph_objects as go
import plotly.io as pio

# ---------------------------------------------------------------------------
# Template Plotly
# ---------------------------------------------------------------------------
# Definido aqui uma vez e aplicado por `template="nenc"` nas figuras das tres
# fabricas de grafico. Os valores sao os mesmos tokens de utils/ui.py e de
# .streamlit/config.toml, para o grafico nao ler como outra aplicacao.

# Uma matiz por serie, nao passos de luminosidade: numa linha com varias
# series a rampa monocromatica e indistinguivel, e os tons escuros da familia
# violeta somem sobre a superficie. O violeta da marca fica em primeiro, para
# que grafico de serie unica continue com a cor do produto. A ordem alterna
# matizes; lilas e ciano vao para o fim por serem os mais proximos dos dois
# primeiros da lista.
NENC_SEQUENCE = [
    "#9184d9",  # violeta (marca)
    "#e9c46a",  # ambar
    "#6aa9d9",  # azul
    "#e0748b",  # rosa
    "#5fbf9f",  # verde-agua
    "#d98d5f",  # laranja
    "#8fca6a",  # verde
    "#b5abfc",  # lilas
    "#5fc6d9",  # ciano
]


# Rampa para heatmaps: do fundo do canvas ao violeta mais claro.
NENC_HEATMAP = [[0, "#161826"], [0.5, "#5d5294"], [1, "#d2cefd"]]

pio.templates["nenc"] = go.layout.Template(
    layout=dict(
        paper_bgcolor="#161826",
        plot_bgcolor="#1c1e2c",
        font=dict(
            family="Inter, system-ui, sans-serif", color="#e9e9ed", size=12
        ),
        colorway=NENC_SEQUENCE,
        xaxis=dict(gridcolor="#3f424d", zerolinecolor="#3f424d"),
        yaxis=dict(gridcolor="#3f424d", zerolinecolor="#3f424d"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=48, r=24, t=40, b=40),
    )
)
pio.templates.default = "nenc"
