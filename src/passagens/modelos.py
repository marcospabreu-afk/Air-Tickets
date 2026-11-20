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
    escalas_volta: int | None = None
    duracao_volta_min: int | None = None
    # Identificador que a fonte usa para buscar os horários da volta (1 consulta extra).
    token_volta: str | None = None
    # Preço anunciado na lista, quando a combinação real com a volta custa diferente.
    preco_anunciado: float | None = None
    # True quando a fonte foi consultada e não informou o voo de volta.
    volta_indisponivel: bool = False

    @property
    def tem_volta(self) -> bool:
        return self.partida_volta is not None

    @property
    def rota(self) -> str:
        return f"{self.origem}-{self.destino}"
