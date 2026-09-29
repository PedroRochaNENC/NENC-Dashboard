"""Números em pt-BR para telas e arquivos da Jornada (sem Streamlit)."""

import math


def fmt_seconds(value) -> str:
    """"1:21" para 81 s; vazio para NaN."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(seconds):
        return ""
    minutes, rest = divmod(int(round(seconds)), 60)
    return "{}:{:02d}".format(minutes, rest)


def fmt_number(value, digits: int = 1, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(number):
        return ""
    return "{:.{d}f}{s}".format(number, d=digits, s=suffix).replace(".", ",")


def fmt_pct(value, digits: int = 0) -> str:
    """Proporção (0..1) como porcentagem pt-BR."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(number):
        return ""
    return fmt_number(100 * number, digits, "%")
