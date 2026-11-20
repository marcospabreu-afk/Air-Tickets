from datetime import date, datetime, timedelta

import pytest

from passagens import cli
from passagens.alertas import motivos_de_alerta
from passagens.busca import buscar_flexivel, ordenar, pares_de_datas, remover_duplicadas
from passagens.historico import CacheSQLite, Historico
from passagens.modelos import Oferta
from passagens.provedores import ErroDeProvedor, ProvedorDePrecos, ProvedorFalso, ProvedorSerpApi

HOJE = date(2026, 11, 16)
IDA, VOLTA = date(2027, 3, 10), date(2027, 3, 24)


def oferta(preco, ida=IDA, volta=VOLTA, cia="X", duracao=600):
    return Oferta(cia, "CWB", "LIS", ida, volta, "10:00", "20:00", 1, duracao, preco, "http://x", "teste")


# ------------------------------------------------------------- interface


def test_provedores_implementam_a_interface():
    assert issubclass(ProvedorFalso, ProvedorDePrecos)
    assert issubclass(ProvedorSerpApi, ProvedorDePrecos)
    with pytest.raises(TypeError):
        ProvedorDePrecos()


def test_provedor_falso_e_deterministico():
    a = ProvedorFalso().buscar("CWB", "LIS", IDA, VOLTA)
    b = ProvedorFalso().buscar("CWB", "LIS", IDA, VOLTA)
    assert a == b and len(a) == 3


def test_serpapi_exige_chave_da_variavel_de_ambiente(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(ErroDeProvedor, match="SERPAPI_API_KEY"):
        ProvedorSerpApi()


class RespostaFalsa:
    def __init__(self, dados, status=200):
        self.dados, self.status_code = dados, status

    def json(self):
        return self.dados


RESPOSTA_SERPAPI = {
    "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?tfs=abc"},
    "best_flights": [{
        "flights": [
            {"departure_airport": {"id": "CWB", "time": "2027-03-10 14:00"},
             "arrival_airport": {"id": "GRU", "time": "2027-03-10 15:05"}, "airline": "LATAM"},
            {"departure_airport": {"id": "GRU", "time": "2027-03-10 18:00"},
             "arrival_airport": {"id": "LIS", "time": "2027-03-11 07:30"}, "airline": "TAP Air Portugal"},
        ],
        "layovers": [{"id": "GRU", "duration": 175}],
        "total_duration": 870, "price": 5123, "departure_token": "tok",
    }],
    "other_flights": [{
        "flights": [{"departure_airport": {"id": "CWB", "time": "2027-03-10 09:00"},
                     "arrival_airport": {"id": "LIS", "time": "2027-03-10 23:00"}, "airline": "TAP Air Portugal"}],
        "total_duration": 840, "price": 4890,
    }],
}


def test_serpapi_converte_resposta_e_usa_cache(monkeypatch):
    chamadas = []

    def http_get(url, params, timeout):
        chamadas.append(params)
        return RespostaFalsa(RESPOSTA_SERPAPI)

    hist = Historico(":memory:")
    p = ProvedorSerpApi(chave="teste", cache=CacheSQLite(hist), http_get=http_get)
    ofertas = p.buscar("CWB", "LIS", IDA, VOLTA)
    assert chamadas[0]["currency"] == "BRL" and chamadas[0]["api_key"] == "teste"
    assert [o.preco for o in ofertas] == [5123.0, 4890.0]
    assert ofertas[0].companhia == "LATAM / TAP Air Portugal"
    assert ofertas[0].chegada_ida == "07:30+1" and ofertas[0].escalas == 1
    assert ofertas[1].escalas == 0
    assert ofertas[0].link.startswith("https://www.google.com/travel/flights")
    p.buscar("CWB", "LIS", IDA, VOLTA)  # segunda vez: cache
    assert len(chamadas) == 1


def test_serpapi_erro_vira_ErroDeProvedor():
    p = ProvedorSerpApi(chave="x", http_get=lambda *a, **k: RespostaFalsa({"error": "Invalid API key."}, 401))
    with pytest.raises(ErroDeProvedor, match="Invalid API key"):
        p.buscar("CWB", "LIS", IDA, VOLTA)


# ------------------------------------------------------------- ordenação


def test_ordenacao_do_menor_para_o_maior():
    ofertas = [oferta(5000), oferta(3000), oferta(4000), oferta(3000, ida=date(2027, 3, 9))]
    precos = [o.preco for o in ordenar(ofertas)]
    assert precos == [3000, 3000, 4000, 5000]
    assert ordenar(ofertas)[0].data_ida == date(2027, 3, 9)  # desempate pela data


def test_busca_flexivel_retorna_ordenado():
    r = buscar_flexivel(ProvedorFalso(), "CWB", "LIS", IDA, VOLTA, flex=3, hoje=HOJE)
    precos = [o.preco for o in r.ofertas]
    assert precos == sorted(precos)
    assert len(r.ofertas) == 49 * 3


def test_remove_ofertas_duplicadas():
    assert len(remover_duplicadas([oferta(3000), oferta(3000), oferta(3100)])) == 2


# ------------------------------------------------------------- datas flexíveis


def test_pares_de_datas_flex_3_gera_49_combinacoes():
    pares = pares_de_datas(IDA, VOLTA, 3, hoje=HOJE)
    assert len(pares) == 49
    assert {i for i, _ in pares} == {IDA + timedelta(days=d) for d in range(-3, 4)}
    assert {v for _, v in pares} == {VOLTA + timedelta(days=d) for d in range(-3, 4)}


def test_pares_de_datas_flex_0():
    assert pares_de_datas(IDA, VOLTA, 0, hoje=HOJE) == [(IDA, VOLTA)]


def test_pares_de_datas_descarta_volta_antes_da_ida_e_passado():
    pares = pares_de_datas(date(2026, 11, 17), date(2026, 11, 19), 2, hoje=HOJE)
    assert all(i < v for i, v in pares)
    assert all(i >= HOJE for i, _ in pares)
    assert (date(2026, 11, 15), date(2026, 11, 19)) not in pares


def test_pares_de_datas_valida_entrada():
    with pytest.raises(ValueError):
        pares_de_datas(VOLTA, IDA, 1, hoje=HOJE)
    with pytest.raises(ValueError):
        pares_de_datas(IDA, VOLTA, -1, hoje=HOJE)


def test_busca_consulta_cada_combinacao_e_respeita_teto():
    p = ProvedorFalso()
    buscar_flexivel(p, "CWB", "LIS", IDA, VOLTA, flex=3, hoje=HOJE)
    assert len(set(p.consultas)) == 49
    p2 = ProvedorFalso()
    r = buscar_flexivel(p2, "CWB", "LIS", IDA, VOLTA, flex=3, max_consultas=5, hoje=HOJE)
    assert len(p2.consultas) == 5 and (IDA, VOLTA) in r.pares_consultados


# ------------------------------------------------------------- histórico


def test_gravacao_do_historico_com_data_e_hora():
    h = Historico(":memory:")
    quando = datetime(2026, 11, 16, 9, 30, 15)
    assert h.gravar([oferta(4000), oferta(5000)], quando=quando) == 2
    registros = h.registros()
    assert registros[0][0] == "2026-11-16T09:30:15"
    assert registros[0][1:5] == ("CWB", "LIS", "2027-03-10", "2027-03-24")


def test_resumo_menor_maior_media_por_rota():
    h = Historico(":memory:")
    h.gravar([oferta(4000), oferta(5000), oferta(6000)])
    h.gravar([Oferta("Y", "GRU", "MAD", IDA, VOLTA, "1", "2", 0, 1, 3000, "", "t")])
    resumos = {(r.origem, r.destino): r for r in h.resumo()}
    r = resumos[("CWB", "LIS")]
    assert (r.menor, r.maior, r.media, r.registros) == (4000, 6000, 5000, 3)
    assert resumos[("GRU", "MAD")].media == 3000
    assert h.media("CWB", "LIS") == 5000
    assert h.media_dos_menores("CWB", "LIS") == 4000  # uma consulta, menor = 4000
    assert h.media("AAA", "BBB") is None


def test_media_dos_menores_usa_o_menor_de_cada_consulta():
    h = Historico(":memory:")
    h.gravar([oferta(4000), oferta(20000)], quando=datetime(2026, 11, 1, 8))
    h.gravar([oferta(5000), oferta(30000)], quando=datetime(2026, 11, 2, 8))
    assert h.media_dos_menores("CWB", "LIS") == 4500
    # 4200 está abaixo da média geral (14750) mas NÃO 15% abaixo da média dos menores
    assert not motivos_de_alerta(4200, None, h.media_dos_menores("CWB", "LIS"))


def test_historico_persiste_em_arquivo(tmp_path):
    db = tmp_path / "h.db"
    Historico(db).gravar([oferta(4321)])
    assert Historico(db).resumo()[0].menor == 4321


# ------------------------------------------------------------- regra de alerta


def test_alerta_abaixo_do_alvo():
    assert motivos_de_alerta(4499, 4500, None)
    assert not motivos_de_alerta(4500, 4500, None)
    assert not motivos_de_alerta(4600, 4500, None)


def test_alerta_queda_maior_que_15_por_cento_da_media():
    assert motivos_de_alerta(8400, None, 10000)          # 16% abaixo
    assert not motivos_de_alerta(8500, None, 10000)      # exatamente 15%: não
    assert not motivos_de_alerta(9000, None, 10000)
    assert not motivos_de_alerta(5000, None, None)       # sem histórico


def test_alerta_com_os_dois_motivos():
    assert len(motivos_de_alerta(4000, 4500, 6000)) == 2


# ------------------------------------------------------------- CLI ponta a ponta


def test_cli_buscar_e_historico(tmp_path, capsys):
    db = str(tmp_path / "h.db")
    args = ["buscar", "--provedor", "falso", "--db", db, "--origem", "cwb", "--destino", "LIS",
            "--ida", "2027-03-10", "--volta", "2027-03-24", "--flex", "1", "--limite", "0"]
    assert cli.main(args) == 0
    saida = capsys.readouterr().out
    assert "9 combinação(ões)" in saida and "27 preços gravados" in saida
    assert cli.main(["historico", "--db", db]) == 0
    assert "CWB→LIS" in capsys.readouterr().out


def _rotas(tmp_path, alvo):
    arq = tmp_path / "rotas.yaml"
    arq.write_text(f"rotas:\n  - origem: CWB\n    destino: LIS\n    ida: 2027-03-10\n"
                   f"    volta: 2027-03-24\n    flex: 0\n    preco_alvo: {alvo}\n", encoding="utf-8")
    return str(arq)


def test_cli_monitorar_alerta_pelo_alvo(tmp_path, capsys):
    db = str(tmp_path / "h.db")
    assert cli.main(["monitorar", "--provedor", "falso", "--db", db, "--rotas", _rotas(tmp_path, 99999)]) == 0
    assert "ALERTA" in capsys.readouterr().out
    assert cli.main(["monitorar", "--provedor", "falso", "--db", db, "--rotas", _rotas(tmp_path, 10)]) == 0
    assert "ALERTA" not in capsys.readouterr().out


def test_cli_monitorar_alerta_pela_queda_da_media(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "h.db")
    rotas = _rotas(tmp_path, 10)
    cli.main(["monitorar", "--provedor", "falso", "--db", db, "--rotas", rotas])
    capsys.readouterr()
    monkeypatch.setenv("PASSAGENS_FALSO_FATOR", "0.75")  # preço cai 25%
    cli.main(["monitorar", "--provedor", "falso", "--db", db, "--rotas", rotas])
    saida = capsys.readouterr().out
    assert "ALERTA" in saida and "abaixo da média histórica" in saida


def test_cli_sem_chave_explica_o_que_falta(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    codigo = cli.main(["buscar", "--provedor", "serpapi", "--db", str(tmp_path / "h.db"),
                       "--origem", "CWB", "--destino", "LIS", "--ida", "2027-03-10", "--volta", "2027-03-24"])
    assert codigo == 2 and "SERPAPI_API_KEY" in capsys.readouterr().err


def test_busca_paralela_da_o_mesmo_resultado_que_sequencial():
    seq = buscar_flexivel(ProvedorFalso(), "CWB", "LIS", IDA, VOLTA, flex=3, hoje=HOJE)
    par = buscar_flexivel(ProvedorFalso(), "CWB", "LIS", IDA, VOLTA, flex=3, hoje=HOJE, paralelo=8)
    assert par.ofertas == seq.ofertas and len(par.pares_consultados) == 49
