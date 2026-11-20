from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Passageiros:
    """Composição do grupo. O preço das ofertas é sempre o TOTAL do grupo.

    Não há fórmula fixa para criança: cada companhia aplica o próprio desconto
    (em testes reais: de 0% a ~22% para criança; bebê de colo ~9–15% da tarifa
    adulta). Por isso o preço do grupo é pedido diretamente à fonte.
    """

    adultos: int = 1
    criancas: int = 0  # 2 a 11 anos
    bebes: int = 0  # até 2 anos, no colo de um adulto

    def __post_init__(self):
        if not 1 <= self.adultos <= 9:
            raise ValueError("é preciso de 1 a 9 adultos")
        if self.criancas < 0 or self.bebes < 0:
            raise ValueError("número de crianças e bebês não pode ser negativo")
        if self.bebes > self.adultos:
            raise ValueError("cada bebê de colo precisa de um adulto (bebês ≤ adultos)")
        if self.total > 9:
            raise ValueError("no máximo 9 passageiros por busca")

    @property
    def total(self) -> int:
        return self.adultos + self.criancas + self.bebes

    @property
    def codigo(self) -> str:
        """Identificador curto usado no histórico: '1A', '2A1C', '2A1C1B'."""
        return (f"{self.adultos}A" + (f"{self.criancas}C" if self.criancas else "")
                + (f"{self.bebes}B" if self.bebes else ""))

    @classmethod
    def do_codigo(cls, codigo: str) -> "Passageiros":
        import re
        partes = dict((letra, int(n)) for n, letra in re.findall(r"(\d+)([ACB])", codigo or "1A"))
        return cls(partes.get("A", 1), partes.get("C", 0), partes.get("B", 0))

    @property
    def descricao(self) -> str:
        def plural(n: int, um: str, varios: str) -> str:
            return f"{n} {um if n == 1 else varios}"
        partes = [plural(self.adultos, "adulto", "adultos")]
        if self.criancas:
            partes.append(plural(self.criancas, "criança", "crianças"))
        if self.bebes:
            partes.append(plural(self.bebes, "bebê de colo", "bebês de colo"))
        return ", ".join(partes)


UM_ADULTO = Passageiros()


@dataclass(frozen=True)
class Oferta:
    """Uma oferta de ida e volta; `preco` é o total em BRL para o grupo de passageiros."""

    companhia: str
    origem: str
    destino: str
    data_ida: date
    data_volta: date
    partida_ida: str  # "HH:MM"
    chegada_ida: str  # "HH:MM" (pode ter "+1" se chega no dia seguinte)
    escalas: int
    duracao_min: int  # duração do trecho de ida
    preco: float  # total ida+volta em BRL, para TODO o grupo de passageiros
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
    passageiros: Passageiros = UM_ADULTO

    @property
    def preco_por_pessoa(self) -> float:
        return self.preco / self.passageiros.total

    @property
    def tem_volta(self) -> bool:
        return self.partida_volta is not None

    @property
    def rota(self) -> str:
        return f"{self.origem}-{self.destino}"
