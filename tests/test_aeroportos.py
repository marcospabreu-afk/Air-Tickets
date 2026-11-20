from passagens.aeroportos import buscar, existe, normalizar, todos


def codigos(q, n=3):
    return [a.iata for a in buscar(q, n)]


def test_base_tem_milhares_de_aeroportos_com_pais_em_portugues():
    base = todos()
    assert len(base) > 7000
    assert base["CWB"].cidade == "Curitiba" and base["CWB"].pais == "Brasil"
    assert base["LHR"].pais == "Reino Unido"


def test_normaliza_acentos_e_maiusculas():
    assert normalizar("  São   PAULO ") == "sao paulo"


def test_codigo_exato_vem_primeiro():
    assert codigos("lis", 1) == ["LIS"]
    assert codigos("GRU", 1) == ["GRU"]


def test_busca_por_cidade_sem_acento_e_aeroporto_principal_primeiro():
    assert codigos("curitiba", 1) == ["CWB"]
    assert set(codigos("são paulo", 2)) == {"GRU", "CGH"}


def test_apelidos_em_portugues():
    assert codigos("lisboa", 1) == ["LIS"]
    assert set(codigos("nova york", 3)) == {"JFK", "EWR", "LGA"}
    assert "EZE" in codigos("buenos aires", 2)


def test_busca_por_pais():
    assert "LIS" in codigos("portugal", 6)


def test_consulta_vazia_e_existe():
    assert buscar("   ") == []
    assert existe("cwb") and not existe("QQQ")
