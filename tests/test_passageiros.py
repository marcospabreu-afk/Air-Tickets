import sqlite3
from datetime import date

import pytest

from passagens import cli
from passagens.busca import buscar_flexivel
from passagens.historico import Historico
from passagens.modelos import UM_ADULTO, Passageiros
from passagens.monitor import carregar_rotas, verificar_rota
from passagens.provedores import ProvedorFalso, ProvedorSerpApi

IDA, VOLTA = date(2027, 3, 10), date(2027, 3, 24)


def test_composicao_do_grupo_codigo_e_descricao():
    p = Passageiros(2, 1, 1)
    assert p.total == 4 and p.codigo == "2A1C1B"
    assert p.descricao == "2 adultos, 1 criança, 1 bebê de colo"
    assert Passageiros.do_codigo("2A1C1B") == p and Passageiros.do_codigo("1A") == UM_ADULTO
    assert UM_ADULTO.descricao == "1 adulto"


@pytest.mark.parametrize("a,c,b,erro", [(0, 1, 0, "1 a 9 adultos"), (1, 0, 2, "bebês ≤ adultos"),
                                        (5, 5, 0, "9 passageiros"), (1, -1, 0, "negativo")])
def test_grupo_invalido(a, c, b, erro):
    with pytest.raises(ValueError, match=erro):
        Passageiros(a, c, b)


def test_preco_e_total_do_grupo_e_por_pessoa():
    um = buscar_flexivel(ProvedorFalso(), "CWB", "LIS", IDA, VOLTA, hoje=date(2026, 11, 20)).ofertas[0]
    familia = buscar_flexivel(ProvedorFalso(), "CWB", "LIS", IDA, VOLTA, hoje=date(2026, 11, 20),
                              passageiros=Passageiros(2, 1, 1)).ofertas[0]
    # provedor falso: criança 75%, bebê 10% (as companhias reais variam)
    assert familia.preco == pytest.approx(um.preco * (2 + 0.75 + 0.10), abs=0.05)
    assert familia.preco_por_pessoa == pytest.approx(familia.preco / 4)
    assert familia.passageiros == Passageiros(2, 1, 1)


def test_serpapi_envia_criancas_e_bebes_e_mantem_cache_de_1_adulto():
    enviados = []

    def http_get(url, params, timeout):
        enviados.append(params)

        class R:
            status_code = 200

            def json(self):
                return {"best_flights": [], "other_flights": []}
        return R()

    p = ProvedorSerpApi(chave="x", http_get=http_get)
    p.buscar("CWB", "LIS", IDA, VOLTA, Passageiros(2, 1, 1))
    p.buscar("CWB", "LIS", IDA, VOLTA)
    assert (enviados[0]["adults"], enviados[0]["children"], enviados[0]["infants_on_lap"]) == (2, 1, 1)
    assert enviados[1]["adults"] == 1 and "children" not in enviados[1] and "infants_on_lap" not in enviados[1]


def test_historico_separa_por_grupo():
    h = Historico(":memory:")
    p = ProvedorFalso()
    h.gravar(p.buscar("CWB", "LIS", IDA, VOLTA))
    h.gravar(p.buscar("CWB", "LIS", IDA, VOLTA, Passageiros(2, 1)))
    resumos = {r.passageiros: r for r in h.resumo()}
    assert set(resumos) == {"1A", "2A1C"}
    assert resumos["2A1C"].menor > resumos["1A"].menor * 2
    assert h.media_dos_menores("CWB", "LIS", Passageiros(2, 1)) == resumos["2A1C"].menor
    assert h.media_dos_menores("CWB", "LIS") == resumos["1A"].menor


def test_migra_banco_antigo_sem_coluna_de_passageiros(tmp_path):
    db = tmp_path / "antigo.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE precos (id INTEGER PRIMARY KEY AUTOINCREMENT, consultado_em TEXT NOT NULL,"
                " origem TEXT NOT NULL, destino TEXT NOT NULL, data_ida TEXT NOT NULL, data_volta TEXT NOT NULL,"
                " companhia TEXT NOT NULL, escalas INTEGER NOT NULL, preco REAL NOT NULL, provedor TEXT NOT NULL)")
    con.execute("INSERT INTO precos VALUES (NULL,'2026-11-01T08:00:00','CWB','LIS','2027-03-10','2027-03-24',"
                "'LATAM',1,4500,'serpapi')")
    con.commit(); con.close()
    h = Historico(db)
    (r,) = h.resumo()
    assert r.passageiros == "1A" and r.menor == 4500
    h.gravar(ProvedorFalso().buscar("CWB", "LIS", IDA, VOLTA, Passageiros(2)))
    assert {x.passageiros for x in h.resumo()} == {"1A", "2A"}


def test_rotas_yaml_com_criancas(tmp_path):
    arq = tmp_path / "rotas.yaml"
    arq.write_text("rotas:\n  - {origem: CWB, destino: LIS, ida: 2027-03-10, volta: 2027-03-24,"
                   " adultos: 2, criancas: 2, preco_alvo: 99999}\n", encoding="utf-8")
    (rota,) = carregar_rotas(arq)
    assert rota.passageiros == Passageiros(2, 2) and "2 crianças" in rota.rotulo
    r = verificar_rota(rota, Historico(":memory:"), ProvedorFalso())
    assert r.alerta and r.melhor.passageiros == Passageiros(2, 2)


def test_cli_buscar_familia(tmp_path, capsys):
    db = str(tmp_path / "h.db")
    assert cli.main(["buscar", "--provedor", "falso", "--db", db, "--origem", "CWB", "--destino", "LIS",
                     "--ida", "2027-03-10", "--volta", "2027-03-24", "--adultos", "2", "--criancas", "1"]) == 0
    saida = capsys.readouterr().out
    assert "para 2 adultos, 1 criança" in saida and "Por pessoa" in saida
    assert cli.main(["historico", "--db", db]) == 0
    assert "2 adultos, 1 criança" in capsys.readouterr().out
    assert cli.main(["buscar", "--provedor", "falso", "--db", db, "--origem", "CWB", "--destino", "LIS",
                     "--ida", "2027-03-10", "--volta", "2027-03-24", "--bebes", "2"]) == 2
