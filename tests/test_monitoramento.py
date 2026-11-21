import os
import time
from datetime import date

import pytest

from passagens import cli
from passagens.historico import Historico
from passagens.modelos import Alerta, Passageiros, Rota
from passagens.monitor import destinos_validos, executar_monitoramento, rotas_do_banco
from passagens.notificacao import ErroDeEnvio, Notificador, montar_mensagem
from passagens.provedores import ProvedorFalso
from passagens.web import criar_app

IDA, VOLTA = date(2027, 3, 10), date(2027, 3, 24)
PG_URL = os.environ.get("PASSAGENS_TESTE_PG")  # ex.: postgresql://postgres@127.0.0.1:5433/passagens_teste


class NotificadorFalso:
    configurado = True

    def __init__(self, falhar=False):
        self.enviadas, self.falhar = [], falhar

    def descricao(self):
        return "falso"

    def enviar(self, mensagem):
        if self.falhar:
            raise ErroDeEnvio("servidor fora do ar")
        self.enviadas.append(mensagem)


def rota(**kw):
    base = dict(origem="CWB", destino="LIS", ida=IDA, volta=VOLTA, flex=0, preco_alvo=99999.0)
    base.update(kw)
    return Rota(**base)


# ------------------------------------------------------------ regiões / destinos


def test_destinos_validos_normaliza_e_limita():
    assert destinos_validos("lis, opo FAO;lis") == "LIS,OPO,FAO"
    with pytest.raises(ValueError, match="ao menos um"):
        destinos_validos(" , ")
    with pytest.raises(ValueError, match="no máximo 7"):
        destinos_validos("LIS,OPO,FAO,FNC,PDL,TER,PXO,MAD")


def test_regiao_busca_varios_destinos_e_informa_chegada():
    ofertas = ProvedorFalso().buscar("CWB", "LIS,OPO,FAO", IDA, VOLTA)
    assert {o.aeroporto_destino for o in ofertas} == {"LIS", "OPO", "FAO"}
    assert all(o.destino == "LIS,OPO,FAO" for o in ofertas)


# ------------------------------------------------------------ regra de aviso


def test_avisa_so_quando_o_preco_cai_abaixo_do_ultimo_alerta():
    h = Historico(":memory:")
    rid = h.salvar_rota(rota(nome="Lisboa"))
    n = NotificadorFalso()
    r1 = executar_monitoramento(h, ProvedorFalso(), h.listar_rotas(True), n)
    assert len(r1.novos_alertas) == 1 and r1.email == "enviado" and len(n.enviadas) == 1
    assert h.ultimo_alerta(rid).email_enviado
    r2 = executar_monitoramento(h, ProvedorFalso(), h.listar_rotas(True), n)  # mesmo preço
    assert r2.resultados[0].alerta and r2.novos_alertas == [] and r2.email == "sem novos alertas"
    r3 = executar_monitoramento(h, ProvedorFalso(fator=0.9), h.listar_rotas(True), n)  # caiu 10%
    assert len(r3.novos_alertas) == 1 and len(n.enviadas) == 2
    assert r3.novos_alertas[0].preco < r1.novos_alertas[0].preco


def test_editar_rota_reinicia_controle_e_pausada_nao_e_verificada():
    h = Historico(":memory:")
    rid = h.salvar_rota(rota())
    executar_monitoramento(h, ProvedorFalso(), h.listar_rotas(True))
    time.sleep(1.1)  # alterada_em tem resolução de segundos
    h.salvar_rota(rota(id=rid, preco_alvo=88888.0))
    assert h.ultimo_alerta(rid) is None
    h.definir_ativa(rid, False)
    assert h.listar_rotas(True) == []


def test_email_nao_configurado_ou_com_falha_mantem_alerta_na_pagina():
    h = Historico(":memory:")
    h.salvar_rota(rota())
    r = executar_monitoramento(h, ProvedorFalso(), h.listar_rotas(True), None)
    assert r.email == "não configurado" and len(h.alertas_recentes()) == 1
    h2 = Historico(":memory:")
    h2.salvar_rota(rota())
    r2 = executar_monitoramento(h2, ProvedorFalso(), h2.listar_rotas(True), NotificadorFalso(falhar=True))
    assert r2.email.startswith("falhou") and not h2.alertas_recentes()[0].email_enviado


def test_importa_rotas_yaml_uma_vez(tmp_path):
    arq = tmp_path / "rotas.yaml"
    arq.write_text("rotas:\n  - {origem: CWB, destino: 'LIS,OPO', ida: 2027-03-10, volta: 2027-03-24,"
                   " nome: Portugal, criancas: 1}\n", encoding="utf-8")
    h = Historico(":memory:")
    (r,) = rotas_do_banco(h, arq)
    assert r.destinos == ["LIS", "OPO"] and r.nome == "Portugal" and r.passageiros == Passageiros(1, 1)
    h.excluir_rota(r.id)
    assert rotas_do_banco(h, arq) == []  # não reimporta depois que o usuário apagou


# ------------------------------------------------------------ e-mail


def alerta_exemplo():
    return Alerta(1, "2026-11-21T08:00:00", 4500.0, "LATAM", IDA, VOLTA, "OPO", "abaixo do alvo (R$ 5.000,00)",
                  "https://www.google.com/travel/flights?x", rota_rotulo="Portugal: CWB→LIS/OPO (…)")


def test_mensagem_de_alerta():
    m = montar_mensagem([alerta_exemplo()], "https://meu-site.onrender.com/")
    assert "R$ 4.500,00" in m.assunto and "Portugal" in m.assunto
    assert "chegada OPO" in m.texto and "https://meu-site.onrender.com/monitorar" in m.texto
    assert "<a href='https://www.google.com/travel/flights?x'>" in m.html


def test_notificador_escolhe_resend_ou_smtp_pela_configuracao():
    assert not Notificador({}).configurado
    assert not Notificador({"RESEND_API_KEY": "x"}).configurado  # falta o destinatário
    assert Notificador({"PASSAGENS_EMAIL_PARA": "a@b.c", "RESEND_API_KEY": "x"}).metodo == "resend"
    assert Notificador({"PASSAGENS_EMAIL_PARA": "a@b.c", "SMTP_USUARIO": "u", "SMTP_SENHA": "s"}).metodo == "smtp"


def test_envio_pelo_resend():
    chamadas = []

    class Resposta:
        status_code = 200

    def post(url, headers, json, timeout):
        chamadas.append((url, headers, json))
        return Resposta()

    n = Notificador({"PASSAGENS_EMAIL_PARA": "eu@exemplo.com", "RESEND_API_KEY": "re_123"}, http_post=post)
    n.enviar(montar_mensagem([alerta_exemplo()]))
    url, headers, corpo = chamadas[0]
    assert url == "https://api.resend.com/emails" and headers["Authorization"] == "Bearer re_123"
    assert corpo["to"] == ["eu@exemplo.com"] and corpo["from"] == "Passagens <onboarding@resend.dev>"

    class Erro:
        status_code = 403

        def json(self):
            return {"message": "domínio não verificado"}

    n2 = Notificador({"PASSAGENS_EMAIL_PARA": "x@y.z", "RESEND_API_KEY": "re"}, http_post=lambda *a, **k: Erro())
    with pytest.raises(ErroDeEnvio, match="403 domínio não verificado"):
        n2.enviar(montar_mensagem([alerta_exemplo()]))


def test_envio_por_smtp_real_num_servidor_local():
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")
    from aiosmtpd.handlers import Sink

    class Guardar(Sink):
        def __init__(self):
            self.mensagens = []

        async def handle_DATA(self, server, session, envelope):
            self.mensagens.append(envelope)
            return "250 OK"

    import socket

    with socket.socket() as livre:
        livre.bind(("127.0.0.1", 0))
        porta = livre.getsockname()[1]
    guardar = Guardar()
    servidor = aiosmtpd.Controller(guardar, hostname="127.0.0.1", port=porta)
    servidor.start()
    try:
        n = Notificador({"PASSAGENS_EMAIL_PARA": "eu@exemplo.com", "SMTP_USUARIO": "alertas@exemplo.com",
                         "SMTP_SENHA": "x", "SMTP_HOST": "127.0.0.1", "SMTP_PORTA": str(porta),
                         "SMTP_SEGURANCA": "nenhuma"})
        n.enviar(montar_mensagem([alerta_exemplo()]))
    finally:
        servidor.stop()
    (env,) = guardar.mensagens
    assert env.rcpt_tos == ["eu@exemplo.com"] and b"Passagem baixou" in env.content


# ------------------------------------------------------------ site


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PASSAGENS_TOKEN_MONITOR", "tok-secreto")
    for var in ("PASSAGENS_EMAIL_PARA", "RESEND_API_KEY", "SMTP_USUARIO"):
        monkeypatch.delenv(var, raising=False)
    return criar_app(db=str(tmp_path / "h.db"), rotas=str(tmp_path / "nao-existe.yaml"), senha="",
                     criar_provedor=lambda h: ProvedorFalso())


def form(**kw):
    base = {"nome": "Portugal", "origem": "Curitiba", "destino": "LIS,OPO,Faro", "ida": "2027-03-10",
            "volta": "2027-03-24", "flex": "0", "preco_alvo": "4.500", "adultos": "2", "criancas": "1",
            "bebes": "0", "ativa": "1"}
    base.update(kw)
    return base


def test_site_cria_edita_pausa_e_exclui_rota(app):
    c = app.test_client()
    r = c.post("/monitorar/rotas", data=form())
    assert r.status_code == 303 and "Rota+criada" in r.headers["Location"]
    html = c.get("/monitorar?msg=ok").get_data(as_text=True)
    assert "CWB → LIS / OPO / FAO" in html and "2 adultos, 1 criança" in html and "R$ 4.500,00" in html
    edit = c.get("/monitorar?editar=1").get_data(as_text=True)
    assert 'value="LIS,OPO,FAO"' in edit and "Salvar alterações" in edit
    c.post("/monitorar/rotas", data=form(id="1", destino="MAD", preco_alvo="3000"))
    assert "CWB → MAD" in c.get("/monitorar").get_data(as_text=True)
    c.post("/monitorar/rotas/1/ativa")
    assert "retomar" in c.get("/monitorar").get_data(as_text=True)
    c.post("/monitorar/rotas/1/excluir")
    assert "Nenhuma rota ainda" in c.get("/monitorar").get_data(as_text=True)


@pytest.mark.parametrize("campo,valor,erro", [
    ("destino", "", "ao menos um aeroporto"), ("destino", "CWB", "origem não pode estar"),
    ("volta", "2027-03-01", "volta precisa ser depois"), ("preco_alvo", "-5", "maior que zero"),
    ("bebes", "3", "bebês ≤ adultos"), ("origem", "xqzwk", "não encontrado")])
def test_site_valida_rota(app, campo, valor, erro):
    r = app.test_client().post("/monitorar/rotas", data=form(**{campo: valor}))
    assert r.status_code == 400 and erro in r.get_data(as_text=True)


def test_site_verificar_agora_registra_alerta(app):
    c = app.test_client()
    c.post("/monitorar/rotas", data=form(preco_alvo="99999"))
    html = c.post("/monitorar/verificar").get_data(as_text=True)
    assert "ALERTA" in html and "1 alerta(s) novo(s)" in html and "e-mail: não configurado" in html
    assert "Alertas recebidos" in html and "Nenhum alerta ainda" not in html


def test_api_de_agendamento_exige_token(app):
    c = app.test_client()
    c.post("/monitorar/rotas", data=form(preco_alvo="99999"))
    assert c.post("/api/monitorar/executar").status_code == 401
    assert c.post("/api/monitorar/executar", headers={"Authorization": "Bearer errado"}).status_code == 401
    r = c.post("/api/monitorar/executar", headers={"Authorization": "Bearer tok-secreto"})
    dados = r.get_json()
    assert r.status_code == 200 and len(dados["novos_alertas"]) == 1 and dados["rotas"][0]["alerta"]
    assert "Última verificação:</b> " in c.get("/monitorar").get_data(as_text=True)


def test_api_de_agendamento_com_senha_do_site_e_sem_token(tmp_path, monkeypatch):
    monkeypatch.delenv("PASSAGENS_TOKEN_MONITOR", raising=False)
    sem_token = criar_app(db=str(tmp_path / "a.db"), senha="", criar_provedor=lambda h: ProvedorFalso())
    assert sem_token.test_client().post("/api/monitorar/executar").status_code == 503
    monkeypatch.setenv("PASSAGENS_TOKEN_MONITOR", "t")
    com_senha = criar_app(db=str(tmp_path / "b.db"), senha="s", criar_provedor=lambda h: ProvedorFalso())
    c = com_senha.test_client()
    assert c.get("/monitorar").status_code == 401  # a página continua protegida
    assert c.post("/api/monitorar/executar", headers={"Authorization": "Bearer t"}).status_code == 200


def test_formulario_de_outro_site_e_recusado(app):
    c = app.test_client()
    r = c.post("/monitorar/rotas", data=form(), headers={"Origin": "https://site-malicioso.example"})
    assert r.status_code == 403
    assert c.post("/monitorar/rotas", data=form(), headers={"Origin": "http://localhost"}).status_code == 303


def test_api_sugere_regiao(app):
    itens = app.test_client().get("/api/aeroportos?q=portugal&regioes=1").get_json()
    assert itens[0]["regiao"] and itens[0]["iata"].startswith("LIS,OPO,FAO")
    assert not any(i.get("regiao") for i in app.test_client().get("/api/aeroportos?q=portugal").get_json())


# ------------------------------------------------------------ linha de comando


def test_cli_monitorar_usa_banco_e_avisa_novos(tmp_path, capsys, monkeypatch):
    for var in ("PASSAGENS_EMAIL_PARA", "RESEND_API_KEY", "SMTP_USUARIO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rotas.yaml").write_text(
        "rotas:\n  - {origem: CWB, destino: 'LIS,OPO', ida: 2027-03-10, volta: 2027-03-24, preco_alvo: 99999}\n",
        encoding="utf-8")
    db = str(tmp_path / "h.db")
    assert cli.main(["monitorar", "--provedor", "falso", "--db", db]) == 0
    saida = capsys.readouterr().out
    assert "ALERTA" in saida and "[NOVO]" in saida and "(chegada" in saida and "E-mail: não configurado" in saida
    assert cli.main(["monitorar", "--provedor", "falso", "--db", db]) == 0
    assert "[já avisado" in capsys.readouterr().out
    assert cli.main(["rotas", "--db", db]) == 0
    assert "#1" in capsys.readouterr().out


# ------------------------------------------------------------ Postgres (opcional)


@pytest.mark.skipif(not PG_URL, reason="defina PASSAGENS_TESTE_PG para testar com Postgres")
def test_postgres_fluxo_completo():
    h = Historico(PG_URL)
    for tabela in ("precos", "cache", "rotas", "alertas", "config"):
        h.executar(f"DELETE FROM {tabela}")
    rid = h.salvar_rota(rota(destino="LIS,OPO", passageiros=Passageiros(2, 1)))
    n = NotificadorFalso()
    r1 = executar_monitoramento(h, ProvedorFalso(), h.listar_rotas(True), n, paralelo=3)
    r2 = executar_monitoramento(h, ProvedorFalso(fator=0.8), h.listar_rotas(True), n, paralelo=3)
    assert len(r1.novos_alertas) == len(r2.novos_alertas) == 1 and len(n.enviadas) == 2
    assert h.ultimo_alerta(rid).preco == r2.novos_alertas[0].preco
    assert h.media_dos_menores("CWB", "LIS,OPO", Passageiros(2, 1)) is not None
    assert {r.passageiros for r in h.resumo()} == {"2A1C"}
    h.fechar()
