"""Verificação das rotas monitoradas (usada pela CLI e pela interface web)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .alertas import motivos_de_alerta
from .busca import buscar_flexivel
from .historico import Historico
from .modelos import Oferta
from .provedores import ProvedorDePrecos


@dataclass(frozen=True)
class Rota:
    origem: str
    destino: str
    ida: date
    volta: date
    flex: int = 0
    preco_alvo: float | None = None

    @property
    def rotulo(self) -> str:
        return f"{self.origem}→{self.destino} ({self.ida} a {self.volta}, ±{self.flex})"


@dataclass
class ResultadoRota:
    rota: Rota
    melhor: Oferta | None
    media_dos_menores: float | None
    motivos: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)

    @property
    def alerta(self) -> bool:
        return bool(self.motivos)


def _iata(valor) -> str:
    codigo = str(valor).strip().upper()
    if len(codigo) != 3 or not codigo.isalpha():
        raise ValueError(f"código IATA inválido: {valor!r}")
    return codigo


def _data(valor) -> date:
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        raise ValueError(f"data inválida: {valor!r} (use AAAA-MM-DD)") from None


def carregar_rotas(caminho: str | Path) -> list[Rota]:
    with open(caminho, encoding="utf-8") as arquivo:
        dados = yaml.safe_load(arquivo) or {}
    rotas = []
    for i, r in enumerate(dados.get("rotas") or [], 1):
        faltando = {"origem", "destino", "ida", "volta"} - set(r)
        if faltando:
            raise ValueError(f"rota #{i} em {caminho}: faltam campos {sorted(faltando)}")
        rotas.append(Rota(
            origem=_iata(r["origem"]),
            destino=_iata(r["destino"]),
            ida=_data(r["ida"]),
            volta=_data(r["volta"]),
            flex=int(r.get("flex", 0)),
            preco_alvo=float(r["preco_alvo"]) if r.get("preco_alvo") is not None else None,
        ))
    return rotas


def verificar_rota(rota: Rota, historico: Historico, provedor: ProvedorDePrecos,
                   paralelo: int = 1) -> ResultadoRota:
    # Referência: média do menor preço de cada consulta anterior, calculada ANTES
    # de gravar os preços novos, para comparar hoje com o passado.
    media = historico.media_dos_menores(rota.origem, rota.destino)
    busca = buscar_flexivel(provedor, rota.origem, rota.destino, rota.ida, rota.volta,
                            rota.flex, paralelo=paralelo)
    historico.gravar(busca.ofertas)
    if not busca.ofertas:
        return ResultadoRota(rota, None, media, erros=busca.erros)
    melhor = busca.ofertas[0]
    return ResultadoRota(rota, melhor, media, motivos_de_alerta(melhor.preco, rota.preco_alvo, media),
                         busca.erros)
