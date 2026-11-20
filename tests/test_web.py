import base64

import pytest

from passagens.provedores import ProvedorFalso
from passagens.web import criar_app

ROTAS = """rotas:
  - origem: CWB
    destino: LIS
    ida: 2027-03-10
    volta: 2027-03-24
    flex: 0
    preco_alvo: 99999
"""


@pytest.fixture
def cliente(tmp_path):
    (tmp_path / "rotas.yaml").write_text(ROTAS, encoding="utf-8")
    app = criar_app(db=str(tmp_path / "h.db"), rotas=str(tmp_path / "rotas.yaml"), senha="",
                    criar_provedor=lambda h: ProvedorFalso())
    return app.test_client()


def test_pagina_inicial_tem_formulario(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'name="origem"' in html and "Provedor falso" in html


def test_busca_flexivel_mostra_tabela_ordenada_e_grava_historico(cliente):
    r = cliente.post("/buscar", data={"origem": "cwb", "destino": "LIS", "ida": "2027-03-10",
                                      "volta": "2027-03-24", "flex": "1"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "27 ofertas em 9 combinações" in " ".join(html.split())
    assert "Abrir no Google Flights" in html
    precos = [float(p.replace(".", "").replace(",", "."))
              for p in __import__("re").findall(r'class="num">R\$ ([\d.,]+)', html)]
    assert precos == sorted(precos) and len(precos) == 27
    hist = cliente.get("/historico").get_data(as_text=True)
    assert "CWB → LIS" in hist and "27" in hist


def test_busca_valida_entrada_sem_consultar(cliente):
    html = cliente.post("/buscar", data={"origem": "xqzwk", "destino": "LIS", "ida": "2027-03-10",
                                         "volta": "2027-03-24", "flex": "0"}).get_data(as_text=True)
    assert "aeroporto de origem não encontrado" in html
    html = cliente.post("/buscar", data={"origem": "LIS", "destino": "lis", "ida": "2027-03-10",
                                         "volta": "2027-03-24", "flex": "0"}).get_data(as_text=True)
    assert "mesmo aeroporto" in html
    html = cliente.post("/buscar", data={"origem": "CWB", "destino": "LIS", "ida": "2027-03-10",
                                         "volta": "2027-03-24", "flex": "9"}).get_data(as_text=True)
    assert "flexibilidade deve ser de 0 a 3" in html


def test_monitorar_mostra_alerta(cliente):
    assert "Verificar agora" in cliente.get("/monitorar").get_data(as_text=True)
    html = cliente.post("/monitorar").get_data(as_text=True)
    assert "ALERTA" in html and "abaixo do alvo" in html


def test_senha_protege_o_site(tmp_path):
    app = criar_app(db=str(tmp_path / "h.db"), senha="segredo", criar_provedor=lambda h: ProvedorFalso())
    c = app.test_client()
    assert c.get("/").status_code == 401
    assert c.get("/saude").status_code == 200
    errada = base64.b64encode(b"x:errada").decode()
    assert c.get("/", headers={"Authorization": f"Basic {errada}"}).status_code == 401
    certa = base64.b64encode(b"qualquer:segredo").decode()
    assert c.get("/", headers={"Authorization": f"Basic {certa}"}).status_code == 200


def test_sem_chave_mostra_o_que_falta(tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("PASSAGENS_PROVEDOR", raising=False)
    app = criar_app(db=str(tmp_path / "h.db"), senha="")
    assert "SERPAPI_API_KEY" in app.test_client().get("/").get_data(as_text=True)


def test_busca_aceita_nome_da_cidade_em_portugues(cliente):
    html = cliente.post("/buscar", data={"origem": "Curitiba", "destino": "Lisboa", "ida": "2027-03-10",
                                         "volta": "2027-03-24", "flex": "0"}).get_data(as_text=True)
    assert 'value="CWB"' in html and 'value="LIS"' in html
    assert "Lisbon Portela Airport (Portugal)" in html
    assert "3 ofertas em 1 combinações" in " ".join(html.split())


def test_api_de_sugestoes_de_aeroportos(cliente):
    dados = cliente.get("/api/aeroportos?q=lisboa").get_json()
    assert dados[0]["iata"] == "LIS" and dados[0]["pais"] == "Portugal"
    assert cliente.get("/api/aeroportos?q=").get_json() == []
