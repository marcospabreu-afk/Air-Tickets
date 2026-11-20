"""Busca com datas flexíveis e ordenação por preço."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

from .modelos import UM_ADULTO, Oferta, Passageiros
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


def remover_duplicadas(ofertas: list[Oferta]) -> list[Oferta]:
    """A fonte às vezes repete o mesmo voo (mesmo horário, companhia e preço)."""
    vistas, unicas = set(), []
    for o in ofertas:
        chave = (o.companhia, o.data_ida, o.data_volta, o.partida_ida, o.chegada_ida, o.escalas, o.preco)
        if chave not in vistas:
            vistas.add(chave)
            unicas.append(o)
    return unicas


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
    paralelo: int = 1,
    passageiros: Passageiros = UM_ADULTO,
) -> ResultadoBusca:
    """Consulta cada combinação de datas (até `paralelo` ao mesmo tempo)."""
    resultado = ResultadoBusca()
    pares = pares_de_datas(ida, volta, flex, hoje)
    # Se houver teto, começa pelas datas mais próximas das pedidas.
    pares.sort(key=lambda p: (abs((p[0] - ida).days) + abs((p[1] - volta).days), p))
    if max_consultas is not None:
        pares = pares[:max_consultas]

    def consultar(par: tuple[date, date]):
        try:
            return par, provedor.buscar(origem, destino, *par, passageiros), None
        except ErroDeProvedor as exc:
            return par, None, exc

    with ThreadPoolExecutor(max_workers=max(1, paralelo)) as executor:
        respostas = list(executor.map(consultar, pares))
    for (data_ida, data_volta), ofertas, erro in respostas:
        if erro is not None:
            resultado.erros.append(f"{data_ida} → {data_volta}: {erro}")
        else:
            resultado.ofertas.extend(ofertas)
            resultado.pares_consultados.append((data_ida, data_volta))
    resultado.ofertas = ordenar(remover_duplicadas(resultado.ofertas))
    return resultado


def detalhar_voltas(provedor: ProvedorDePrecos, ofertas: list[Oferta], quantidade: int,
                    paralelo: int = 1) -> tuple[list[Oferta], list[str]]:
    """Busca os horários da volta das `quantidade` primeiras ofertas (1 consulta cada).

    Retorna a lista reordenada (o preço pode ser ajustado pela fonte) e os erros.
    """
    quantidade = max(0, min(quantidade, len(ofertas)))
    if quantidade == 0:
        return ofertas, []
    erros: list[str] = []

    def detalhar(oferta: Oferta) -> Oferta:
        try:
            return provedor.detalhar_volta(oferta)
        except ErroDeProvedor as exc:
            erros.append(f"volta de {oferta.companhia} {oferta.data_ida} → {oferta.data_volta}: {exc}")
            return oferta

    with ThreadPoolExecutor(max_workers=max(1, paralelo)) as executor:
        detalhadas = list(executor.map(detalhar, ofertas[:quantidade]))
    return ordenar(detalhadas + ofertas[quantidade:]), erros
