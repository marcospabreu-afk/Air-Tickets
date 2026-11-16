"""Busca com datas flexíveis e ordenação por preço."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .modelos import Oferta
from .provedores import ErroDeProvedor, ProvedorDePrecos


def pares_de_datas(ida: date, volta: date, flex: int, hoje: date | None = None) -> list[tuple[date, date]]:
    """Todas as combinações ida±flex × volta±flex, com ida < volta e ida >= hoje."""
    if flex < 0:
        raise ValueError("flex não pode ser negativo")
    if volta <= ida:
        raise ValueError("a data de volta precisa ser depois da ida")
    hoje = hoje or date.today()
    deslocamentos = range(-flex, flex + 1)
    pares = []
    for di in deslocamentos:
        for dv in deslocamentos:
            nova_ida, nova_volta = ida + timedelta(days=di), volta + timedelta(days=dv)
            if nova_ida < nova_volta and nova_ida >= hoje:
                pares.append((nova_ida, nova_volta))
    return pares


def ordenar(ofertas: list[Oferta]) -> list[Oferta]:
    return sorted(ofertas, key=lambda o: (o.preco, o.data_ida, o.data_volta, o.duracao_min))


@dataclass
class ResultadoBusca:
    ofertas: list[Oferta] = field(default_factory=list)
    pares_consultados: list[tuple[date, date]] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


def buscar_flexivel(
    provedor: ProvedorDePrecos,
    origem: str,
    destino: str,
    ida: date,
    volta: date,
    flex: int = 0,
    max_consultas: int | None = None,
    hoje: date | None = None,
) -> ResultadoBusca:
    resultado = ResultadoBusca()
    pares = pares_de_datas(ida, volta, flex, hoje)
    # Se houver teto, começa pelas datas mais próximas das pedidas.
    pares.sort(key=lambda p: (abs((p[0] - ida).days) + abs((p[1] - volta).days), p))
    if max_consultas is not None:
        pares = pares[:max_consultas]
    for data_ida, data_volta in pares:
        try:
            resultado.ofertas.extend(provedor.buscar(origem, destino, data_ida, data_volta))
            resultado.pares_consultados.append((data_ida, data_volta))
        except ErroDeProvedor as exc:
            resultado.erros.append(f"{data_ida} → {data_volta}: {exc}")
    resultado.ofertas = ordenar(resultado.ofertas)
    return resultado
