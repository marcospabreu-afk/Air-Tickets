"""Gera src/passagens/dados/aeroportos.json (rodar só para atualizar a base).

    pip install airportsdata pycountry
    python scripts/gerar_aeroportos.py

Fontes: airportsdata (MIT, https://github.com/mborsetti/airportsdata) para os
aeroportos e pycountry (nomes de países do projeto iso-codes) para traduzir o
nome do país para português.
"""

import gettext
import json
from pathlib import Path

import airportsdata
import pycountry

DESTINO = Path(__file__).resolve().parent.parent / "src" / "passagens" / "dados" / "aeroportos.json"


def main() -> None:
    pt = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["pt_BR"], fallback=True)
    nomes_pais = {}
    for pais in pycountry.countries:
        nomes_pais[pais.alpha_2] = pt.gettext(getattr(pais, "common_name", None) or pais.name)
    nomes_pais["XK"] = "Kosovo"
    linhas = []
    for codigo, a in sorted(airportsdata.load("IATA").items()):
        if len(codigo) != 3 or not codigo.isalpha():
            continue
        linhas.append([codigo, a["city"] or "", a["name"], nomes_pais.get(a["country"], a["country"]), a["country"]])
    DESTINO.write_text(json.dumps({"campos": ["iata", "cidade", "nome", "pais", "pais_iso"],
                                   "aeroportos": linhas}, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    print(f"{len(linhas)} aeroportos gravados em {DESTINO}")


if __name__ == "__main__":
    main()
