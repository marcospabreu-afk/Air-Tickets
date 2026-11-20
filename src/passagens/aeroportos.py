"""Lista de aeroportos (código IATA) e busca por código, cidade, nome ou país.

Dados: src/passagens/dados/aeroportos.json, gerado por scripts/gerar_aeroportos.py
a partir do pacote airportsdata (licença MIT).
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

# Nomes em português (e nomes de região metropolitana) que a base não traz.
# apelido -> códigos IATA que ele deve encontrar.
APELIDOS: dict[str, list[str]] = {
    "sao paulo": ["GRU", "CGH", "VCP"], "rio de janeiro": ["GIG", "SDU"],
    "belo horizonte": ["CNF", "PLU"], "brasilia": ["BSB"], "foz do iguacu": ["IGU"],
    "lisboa": ["LIS"], "porto": ["OPO"], "londres": ["LHR", "LGW", "STN", "LTN", "LCY"],
    "nova york": ["JFK", "EWR", "LGA"], "nova iorque": ["JFK", "EWR", "LGA"],
    "paris": ["CDG", "ORY"], "roma": ["FCO", "CIA"], "milao": ["MXP", "LIN", "BGY"],
    "madri": ["MAD"], "barcelona": ["BCN"], "sevilha": ["SVQ"], "munique": ["MUC"],
    "frankfurt": ["FRA"], "berlim": ["BER"], "colonia": ["CGN"], "zurique": ["ZRH"],
    "genebra": ["GVA"], "viena": ["VIE"], "praga": ["PRG"], "varsovia": ["WAW"],
    "amsterda": ["AMS"], "amsterdam": ["AMS"], "bruxelas": ["BRU"], "copenhague": ["CPH"],
    "estocolmo": ["ARN"], "oslo": ["OSL"], "helsinque": ["HEL"], "dublin": ["DUB"],
    "edimburgo": ["EDI"], "atenas": ["ATH"], "istambul": ["IST", "SAW"], "moscou": ["SVO", "DME", "VKO"],
    "veneza": ["VCE"], "florenca": ["FLR"], "napoles": ["NAP"], "budapeste": ["BUD"],
    "buenos aires": ["EZE", "AEP"], "santiago": ["SCL"], "montevideu": ["MVD"],
    "assuncao": ["ASU"], "bogota": ["BOG"], "cidade do mexico": ["MEX"], "cancun": ["CUN"],
    "miami": ["MIA"], "orlando": ["MCO"], "los angeles": ["LAX"], "washington": ["IAD", "DCA"],
    "chicago": ["ORD", "MDW"], "toronto": ["YYZ"], "montreal": ["YUL"], "havana": ["HAV"],
    "cidade do cabo": ["CPT"], "joanesburgo": ["JNB"], "cairo": ["CAI"], "marrakech": ["RAK"],
    "dubai": ["DXB"], "doha": ["DOH"], "tel aviv": ["TLV"], "toquio": ["HND", "NRT"],
    "pequim": ["PEK", "PKX"], "xangai": ["PVG", "SHA"], "seul": ["ICN", "GMP"],
    "singapura": ["SIN"], "bangkok": ["BKK", "DMK"], "nova delhi": ["DEL"], "sydney": ["SYD"],
    "lima": ["LIM"], "cusco": ["CUZ"], "punta cana": ["PUJ"], "aruba": ["AUA"],
}

# Aeroportos com voos comerciais regulares relevantes, que devem aparecer antes de
# aeródromos pequenos da mesma cidade/país (a base não informa o porte).
PRINCIPAIS: set[str] = {c for codigos in APELIDOS.values() for c in codigos} | {
    # Brasil
    "CWB", "POA", "FLN", "SSA", "REC", "FOR", "BEL", "MAO", "NAT", "MCZ", "VIX", "GYN",
    "CGB", "CGR", "CNF", "BSB", "GIG", "SDU", "GRU", "CGH", "VCP", "IGU", "NVT", "JOI",
    "LDB", "MGF", "UDI", "RAO", "SJP", "AJU", "JPA", "THE", "SLZ", "PMW", "PVH", "RBR",
    "MCP", "BVB", "IOS", "BPS", "PNZ", "JDO", "CPV", "CAC", "XAP", "FEN", "CXJ", "IMP",
    "STM", "MAB", "PFB", "JJD", "CAW", "CFB", "BAZ", "JTC", "CKS", "LEC", "TFF",
    # Portugal e ilhas, Espanha, outros
    "FAO", "FNC", "PDL", "TER", "PXO", "AGP", "PMI", "VLC", "BIO", "TFS", "LPA", "NCE",
    "MRS", "LYS", "HAM", "DUS", "STR", "MAN", "BHX", "GLA", "CPH", "KEF", "LIM", "UIO",
    "GYE", "CCS", "PTY", "SJO", "SAL", "GUA", "SDQ", "MBJ", "NAS", "ATL", "DFW", "IAH",
    "DEN", "SFO", "SEA", "BOS", "PHL", "LAS", "FLL", "YVR", "AKL", "MEL", "HKG", "TPE",
    "KUL", "CGK", "MNL", "BOM", "AUH", "RUH", "JED", "ADD", "NBO", "LOS", "ACC", "DSS",
    "LAD", "MPM", "CMN", "TUN", "ALG", "COR", "MDZ", "BRC", "IGR", "FTE", "USH",
}

# Ordem de preferência entre aeroportos da mesma cidade (posição no apelido:
# "londres" -> LHR antes de LGW...). Demais principais vêm depois.
_PESO: dict[str, int] = {}
for _codigos in APELIDOS.values():
    for _i, _c in enumerate(_codigos):
        _PESO[_c] = min(_PESO.get(_c, _i), _i)


@dataclass(frozen=True)
class Aeroporto:
    iata: str
    cidade: str
    nome: str
    pais: str
    pais_iso: str

    @property
    def rotulo(self) -> str:
        return f"{self.cidade} – {self.nome} ({self.pais})"


def normalizar(texto: str) -> str:
    """Minúsculas e sem acentos: 'São Paulo' -> 'sao paulo'."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


@lru_cache(maxsize=1)
def todos() -> dict[str, Aeroporto]:
    arquivo = resources.files("passagens") / "dados" / "aeroportos.json"
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    return {linha[0]: Aeroporto(*linha) for linha in dados["aeroportos"]}


@lru_cache(maxsize=1)
def _indice() -> list[tuple[Aeroporto, str, str, str]]:
    apelidos_por_codigo: dict[str, list[str]] = {}
    for apelido, codigos in APELIDOS.items():
        for codigo in codigos:
            apelidos_por_codigo.setdefault(codigo, []).append(apelido)
    indice = []
    for a in todos().values():
        cidades = " | ".join([normalizar(a.cidade), *apelidos_por_codigo.get(a.iata, [])])
        indice.append((a, cidades, normalizar(a.nome), normalizar(a.pais)))
    return indice


def buscar(consulta: str, limite: int = 15) -> list[Aeroporto]:
    """Aeroportos que combinam com a consulta, os mais prováveis primeiro.

    Ordem: código exato; cidade que começa com o texto; apelido em português que
    começa com o texto; nome do aeroporto ou país que começa com o texto; texto
    contido em qualquer campo. Dentro de cada grupo, os aeroportos principais e os
    internacionais vêm antes.
    """
    q = normalizar(consulta)
    if not q:
        return []
    codigo = q.upper()
    resultados = []
    for a, cidades, nome, pais in _indice():
        if a.iata == codigo:
            nivel = 0
        else:
            cidade_real, *apelidos = cidades.split(" | ")
            if cidade_real.startswith(q):
                nivel = 1
            elif any(c.startswith(q) for c in apelidos):
                nivel = 2
            elif nome.startswith(q) or pais.startswith(q):
                nivel = 3
            elif q in cidades or q in nome or (len(q) >= 3 and a.iata.startswith(codigo)):
                nivel = 4
            else:
                continue
        principal = _PESO.get(a.iata, 50) if a.iata in PRINCIPAIS else 99
        internacional = 0 if "international" in nome or "internacional" in nome else 1
        resultados.append((nivel, principal, internacional, a.cidade, a.iata, a))
    resultados.sort(key=lambda r: r[:5])
    return [r[-1] for r in resultados[:limite]]


def existe(codigo: str) -> bool:
    return codigo.upper() in todos()
