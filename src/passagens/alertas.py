"""Regra de alerta de preço."""

from __future__ import annotations

QUEDA_MINIMA = 0.15  # 15% abaixo da média histórica


def motivos_de_alerta(preco: float, preco_alvo: float | None, media_historica: float | None,
                      queda_minima: float = QUEDA_MINIMA) -> list[str]:
    """Lista os motivos de alerta (vazia = sem alerta).

    - preço abaixo do alvo definido pelo usuário;
    - preço mais de 15% abaixo da média histórica da rota.
    """
    motivos = []
    if preco_alvo is not None and preco < preco_alvo:
        motivos.append(f"abaixo do alvo ({formatar_brl(preco_alvo)})")
    if media_historica:
        queda = round(1 - preco / media_historica, 9)  # evita 0.15000000000000002
        if queda > queda_minima:
            motivos.append(f"{queda:.0%} abaixo da média histórica ({formatar_brl(media_historica)})")
    return motivos


def formatar_brl(valor: float) -> str:
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"
