from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Oferta:
    """Uma oferta de ida e volta, preço total em BRL por adulto."""

    companhia: str
    origem: str
    destino: str
    data_ida: date
    data_volta: date
    partida_ida: str  # "HH:MM"
    chegada_ida: str  # "HH:MM" (pode ter "+1" se chega no dia seguinte)
    escalas: int
    duracao_min: int  # duração do trecho de ida
    preco: float  # total ida+volta em BRL
    link: str
    provedor: str
    partida_volta: str | None = None
    chegada_volta: str | None = None

    @property
    def rota(self) -> str:
        return f"{self.origem}-{self.destino}"
