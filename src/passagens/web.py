"""Interface web do `passagens` (Flask).

Rodar localmente:   flask --app 'passagens.web:criar_app()' run
Produção:           gunicorn 'passagens.web:criar_app()'

Variáveis de ambiente:
  SERPAPI_API_KEY     chave da SerpApi (obrigatória com o provedor real)
  PASSAGENS_SENHA     se definida, o site pede essa senha (usuário: qualquer)
  PASSAGENS_PROVEDOR  serpapi (padrão) ou falso
  DATABASE_URL        banco Postgres (ex.: Neon grátis); sem ela, usa SQLite
  PASSAGENS_DB        arquivo SQLite do histórico (quando não há DATABASE_URL)
  PASSAGENS_ROTAS     rotas.yaml importado para o banco na primeira vez (padrão: rotas.yaml)
  PASSAGENS_TOKEN_MONITOR  token do agendamento diário (POST /api/monitorar/executar)
  PASSAGENS_URL_SITE  endereço público do site (vai no link do e-mail)
  RESEND_API_KEY, PASSAGENS_EMAIL_PARA, ...  e-mail dos alertas (ver notificacao.py)
"""

from __future__ import annotations

import hmac
import os
import time
from datetime import date, datetime, timedelta
from typing import Callable

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
from urllib.parse import urlsplit

from . import aeroportos
from .alertas import formatar_brl
from .busca import buscar_flexivel, detalhar_voltas, pares_de_datas
from .historico import CacheSQLite, Historico
from .modelos import Oferta, Passageiros
from .modelos import Rota
from .monitor import _data, destinos_validos, executar_monitoramento, rotas_do_banco
from .notificacao import Notificador
from .provedores import ErroDeProvedor, ProvedorDePrecos, ProvedorFalso, ProvedorSerpApi

FLEX_MAXIMO = 3
VOLTAS_OPCOES = (0, 5, 10, 20)  # horários da volta buscados automaticamente
VOLTAS_PADRAO = 5
PARALELO = 6


def resolver_aeroporto(texto: str, campo: str) -> str:
    """Aceita o código IATA ('LIS') ou o nome da cidade/aeroporto ('Lisboa')."""
    texto = (texto or "").strip()
    if not texto:
        raise ValueError(f"informe o aeroporto de {campo}")
    codigo = texto.split()[0].upper()
    if len(codigo) == 3 and codigo.isalpha() and aeroportos.existe(codigo):
        return codigo
    encontrados = aeroportos.buscar(texto, limite=1)
    if not encontrados:
        raise ValueError(f"aeroporto de {campo} não encontrado: {texto!r}")
    return encontrados[0].iata


def facetas(ofertas: list[Oferta]) -> dict:
    """Valores disponíveis para os filtros da página de resultados."""
    if not ofertas:
        return {}
    contagem: dict[str, int] = {}
    for o in ofertas:
        for cia in o.companhia.split(" / "):
            contagem[cia] = contagem.get(cia, 0) + 1
    return {
        "preco_min": min(o.preco for o in ofertas), "preco_max": max(o.preco for o in ofertas),
        "duracao_min": min(o.duracao_min for o in ofertas), "duracao_max": max(o.duracao_min for o in ofertas),
        "datas_ida": sorted({o.data_ida for o in ofertas}), "datas_volta": sorted({o.data_volta for o in ofertas}),
        "companhias": sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0])),
    }


def _provedor_padrao(historico: Historico) -> ProvedorDePrecos:
    if os.environ.get("PASSAGENS_PROVEDOR", "serpapi") == "falso":
        return ProvedorFalso(fator=float(os.environ.get("PASSAGENS_FALSO_FATOR", "1")))
    return ProvedorSerpApi(cache=CacheSQLite(historico))


def criar_app(
    db: str | None = None,
    rotas: str | None = None,
    senha: str | None = None,
    criar_provedor: Callable[[Historico], ProvedorDePrecos] | None = None,
) -> Flask:
    app = Flask(__name__)
    db = db or os.environ.get("PASSAGENS_DB")
    rotas = rotas or os.environ.get("PASSAGENS_ROTAS", "rotas.yaml")
    senha = senha if senha is not None else os.environ.get("PASSAGENS_SENHA", "")
    criar_provedor = criar_provedor or _provedor_padrao
    app.jinja_env.filters["brl"] = formatar_brl

    def duracao(minutos: int) -> str:
        return f"{minutos // 60}h{minutos % 60:02d}"

    app.jinja_env.filters["duracao"] = duracao
    app.jinja_env.filters["dia_semana"] = lambda d: ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")[d.weekday()]

    def escalas_txt(o: Oferta) -> str:
        def texto(n):
            return "?" if n is None else ("direto" if n == 0 else str(n))
        return f"{texto(o.escalas)} / {texto(o.escalas_volta)}" if o.tem_volta else texto(o.escalas)

    app.jinja_env.globals["escalas_txt"] = escalas_txt

    token_monitor = os.environ.get("PASSAGENS_TOKEN_MONITOR", "")
    url_site = os.environ.get("PASSAGENS_URL_SITE", "")

    @app.before_request
    def mesma_origem():
        """Formulários só aceitos a partir do próprio site (evita CSRF com a senha do navegador)."""
        if request.method != "POST" or request.path.startswith("/api/monitorar/"):
            return None
        origem_req = request.headers.get("Origin") or request.headers.get("Referer")
        if origem_req and urlsplit(origem_req).netloc != request.host:
            return Response("Origem não permitida.", 403)
        return None

    @app.before_request
    def exigir_senha():
        # O agendamento diário usa o próprio token (ver api_monitorar_executar).
        if not senha or request.path in ("/saude", "/api/monitorar/executar"):
            return None
        auth = request.authorization
        if auth and auth.password and hmac.compare_digest(auth.password, senha):
            return None
        return Response("Senha necessária.", 401, {"WWW-Authenticate": 'Basic realm="passagens"'})

    @app.get("/saude")
    def saude():
        return {"ok": True}

    @app.get("/api/aeroportos")
    def api_aeroportos():
        q = request.args.get("q", "")
        itens = []
        if request.args.get("regioes"):
            itens += [{"iata": ",".join(r.codigos), "regiao": True, "cidade": "", "pais": r.pais,
                       "nome": f"Região: {len(r.codigos)} aeroportos ({', '.join(r.codigos)})",
                       "rotulo": r.rotulo} for r in aeroportos.regioes(q)]
        itens += [{"iata": a.iata, "cidade": a.cidade, "nome": a.nome, "pais": a.pais,
                   "rotulo": a.rotulo} for a in aeroportos.buscar(q, limite=12)]
        return jsonify(itens)

    def rotulo(codigo: str) -> str:
        a = aeroportos.todos().get((codigo or "").strip().upper())
        return a.rotulo if a else ""

    app.jinja_env.globals["rotulo_aeroporto"] = rotulo

    def contexto_base(historico: Historico) -> dict:
        try:
            provedor = criar_provedor(historico)
            return {"provedor": provedor, "erro_provedor": None}
        except ErroDeProvedor as exc:
            return {"provedor": None, "erro_provedor": str(exc)}

    @app.get("/")
    def inicio():
        hoje = date.today()
        form = {
            "origem": request.args.get("origem", "CWB"),
            "destino": request.args.get("destino", "LIS"),
            "ida": request.args.get("ida", (hoje + timedelta(days=60)).isoformat()),
            "volta": request.args.get("volta", (hoje + timedelta(days=74)).isoformat()),
            "flex": request.args.get("flex", "1"),
            "voltas": request.args.get("voltas", str(VOLTAS_PADRAO)),
            "adultos": request.args.get("adultos", "1"),
            "criancas": request.args.get("criancas", "0"),
            "bebes": request.args.get("bebes", "0"),
        }
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
        finally:
            historico.fechar()
        return render_template("busca.html", form=form, resultado=None, flex_maximo=FLEX_MAXIMO,
                               voltas_opcoes=VOLTAS_OPCOES, pagina="busca", **ctx)

    @app.post("/buscar")
    def buscar():
        form = {k: request.form.get(k, "").strip()
                for k in ("origem", "destino", "ida", "volta", "flex", "voltas", "adultos", "criancas", "bebes")}
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
            erro = None
            resultado = None
            consultas = None
            segundos = None
            passageiros = None
            try:
                passageiros = Passageiros(int(form["adultos"] or 1), int(form["criancas"] or 0),
                                          int(form["bebes"] or 0))
                origem = resolver_aeroporto(form["origem"], "origem")
                destino = resolver_aeroporto(form["destino"], "destino")
                if origem == destino:
                    raise ValueError("origem e destino são o mesmo aeroporto")
                form["origem"], form["destino"] = origem, destino
                ida, volta = _data(form["ida"]), _data(form["volta"])
                flex = int(form["flex"] or 0)
                if not 0 <= flex <= FLEX_MAXIMO:
                    raise ValueError(f"flexibilidade deve ser de 0 a {FLEX_MAXIMO} dias")
                pares_de_datas(ida, volta, flex)  # valida datas antes de gastar cota
                voltas = int(form["voltas"] or 0)
                if voltas not in VOLTAS_OPCOES:
                    raise ValueError("quantidade de horários de volta inválida")
            except ValueError as exc:
                erro = str(exc)
            if erro is None and ctx["provedor"] is not None:
                inicio = time.monotonic()
                resultado = buscar_flexivel(ctx["provedor"], origem, destino, ida, volta, flex,
                                            paralelo=PARALELO, passageiros=passageiros)
                historico.gravar(resultado.ofertas)
                if voltas:
                    resultado.ofertas, erros_volta = detalhar_voltas(ctx["provedor"], resultado.ofertas,
                                                                     voltas, PARALELO)
                    resultado.erros.extend(erros_volta)
                segundos = time.monotonic() - inicio
                consultas = getattr(ctx["provedor"], "consultas_feitas", None)
        finally:
            historico.fechar()
        return render_template("busca.html", form=form, resultado=resultado, erro=erro,
                               facetas=facetas(resultado.ofertas) if resultado else None,
                               passageiros=passageiros,
                               consultas=consultas, segundos=segundos, flex_maximo=FLEX_MAXIMO,
                               voltas_opcoes=VOLTAS_OPCOES, pagina="busca", **ctx)

    @app.post("/api/volta")
    def api_volta():
        """Horários da volta de uma oferta (botão "ver volta"): 1 consulta, com cache."""
        dados = request.get_json(silent=True) or {}
        try:
            oferta = Oferta(
                companhia=str(dados.get("companhia", "")), origem=resolver_aeroporto(dados.get("origem"), "origem"),
                destino=resolver_aeroporto(dados.get("destino"), "destino"),
                data_ida=_data(dados.get("ida")), data_volta=_data(dados.get("volta")),
                partida_ida="", chegada_ida="", escalas=0, duracao_min=0,
                preco=float(dados.get("preco")), link="", provedor="", token_volta=str(dados.get("token") or ""),
                passageiros=Passageiros.do_codigo(str(dados.get("passageiros") or "1A")),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"erro": f"pedido inválido: {exc}"}), 400
        historico = Historico(db)
        try:
            provedor = criar_provedor(historico)
            detalhada = provedor.detalhar_volta(oferta)
        except ErroDeProvedor as exc:
            return jsonify({"erro": str(exc)}), 502
        finally:
            historico.fechar()
        if not detalhada.tem_volta:
            return jsonify({"indisponivel": True, "erro": "a fonte não informou o voo de volta"}), 404
        return jsonify({"partida": detalhada.partida_volta, "chegada": detalhada.chegada_volta,
                        "escalas": detalhada.escalas_volta, "duracao": duracao(detalhada.duracao_volta_min or 0),
                        "preco": detalhada.preco, "preco_brl": formatar_brl(detalhada.preco),
                        "total_passageiros": detalhada.passageiros.total})

    @app.get("/historico")
    def ver_historico():
        historico = Historico(db)
        try:
            resumos = historico.resumo()
            menores = {(r.origem, r.destino, r.passageiros):
                       historico.media_dos_menores(r.origem, r.destino, r.grupo) for r in resumos}
        finally:
            historico.fechar()
        return render_template("historico.html", resumos=resumos, menores=menores, pagina="historico")

    # ------------------------------------------------------------ monitoramento

    def pagina_monitorar(historico: Historico, ctx: dict, form: dict | None = None, erro: str | None = None,
                         msg: str | None = None, relatorio=None, status: int = 200):
        rotas_salvas = historico.listar_rotas()
        ativas = [r for r in rotas_salvas if r.ativa]
        consultas = sum((2 * r.flex + 1) ** 2 for r in ativas)
        ultima = historico.config("ultima_verificacao")
        corpo = render_template(
            "monitorar.html", pagina="monitorar", rotas=rotas_salvas, form=form or form_rota_vazio(),
            erro=erro, msg=msg, relatorio=relatorio, alertas=historico.alertas_recentes(30),
            consultas_por_verificacao=consultas, notificador=Notificador(),
            agendamento_ativo=bool(token_monitor), ultima_verificacao=ultima, flex_maximo=FLEX_MAXIMO, **ctx)
        return corpo, status

    def form_rota_vazio() -> dict:
        hoje = date.today()
        return {"id": "", "nome": "", "origem": "", "destino": "", "ida": (hoje + timedelta(days=60)).isoformat(),
                "volta": (hoje + timedelta(days=74)).isoformat(), "flex": "0", "preco_alvo": "",
                "adultos": "1", "criancas": "0", "bebes": "0", "ativa": True}

    def form_da_rota(r: Rota) -> dict:
        return {"id": str(r.id), "nome": r.nome, "origem": r.origem, "destino": r.destino,
                "ida": r.ida.isoformat(), "volta": r.volta.isoformat(), "flex": str(r.flex),
                "preco_alvo": "" if r.preco_alvo is None else f"{r.preco_alvo:.0f}",
                "adultos": str(r.passageiros.adultos), "criancas": str(r.passageiros.criancas),
                "bebes": str(r.passageiros.bebes), "ativa": r.ativa}

    def rota_do_form(form: dict) -> Rota:
        origem = resolver_aeroporto(form.get("origem"), "origem")
        partes = [p.strip() for p in (form.get("destino") or "").split(",") if p.strip()]
        destino = destinos_validos(",".join(resolver_aeroporto(p, "destino") for p in partes))
        if origem in destino.split(","):
            raise ValueError("a origem não pode estar entre os destinos")
        ida, volta = _data(form.get("ida")), _data(form.get("volta"))
        flex = int(form.get("flex") or 0)
        if not 0 <= flex <= FLEX_MAXIMO:
            raise ValueError(f"flexibilidade deve ser de 0 a {FLEX_MAXIMO} dias")
        pares_de_datas(ida, volta, flex)  # valida as datas
        alvo_txt = (form.get("preco_alvo") or "").strip().replace("R$", "").replace(".", "").replace(",", ".")
        alvo = float(alvo_txt) if alvo_txt else None
        if alvo is not None and alvo <= 0:
            raise ValueError("o preço-alvo deve ser maior que zero")
        passageiros = Passageiros(int(form.get("adultos") or 1), int(form.get("criancas") or 0),
                                  int(form.get("bebes") or 0))
        rota_id = int(form["id"]) if form.get("id") else None
        return Rota(origem=origem, destino=destino, ida=ida, volta=volta, flex=flex, preco_alvo=alvo,
                    passageiros=passageiros, id=rota_id, nome=(form.get("nome") or "").strip()[:60],
                    ativa=bool(form.get("ativa")))

    @app.get("/monitorar")
    def monitorar():
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
            rotas_do_banco(historico, rotas)  # importa o rotas.yaml na primeira vez
            form = None
            if request.args.get("editar"):
                rota = historico.obter_rota(int(request.args["editar"]))
                form = form_da_rota(rota) if rota else None
            return pagina_monitorar(historico, ctx, form=form, msg=request.args.get("msg"))
        finally:
            historico.fechar()

    @app.post("/monitorar/rotas")
    def salvar_rota():
        form = {k: request.form.get(k, "") for k in ("id", "nome", "origem", "destino", "ida", "volta", "flex",
                                                      "preco_alvo", "adultos", "criancas", "bebes")}
        form["ativa"] = bool(request.form.get("ativa"))
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
            try:
                rota = rota_do_form(form)
                if rota.id is not None and historico.obter_rota(rota.id) is None:
                    raise ValueError("rota não encontrada (talvez tenha sido excluída)")
                historico.salvar_rota(rota)
            except ValueError as exc:
                return pagina_monitorar(historico, ctx, form=form, erro=str(exc), status=400)
        finally:
            historico.fechar()
        msg = "Rota atualizada." if form["id"] else "Rota criada."
        return redirect(url_for("monitorar", msg=msg) + "#rotas", 303)

    @app.post("/monitorar/rotas/<int:rota_id>/excluir")
    def excluir_rota(rota_id: int):
        historico = Historico(db)
        try:
            historico.excluir_rota(rota_id)
        finally:
            historico.fechar()
        return redirect(url_for("monitorar", msg="Rota excluída.") + "#rotas", 303)

    @app.post("/monitorar/rotas/<int:rota_id>/ativa")
    def alternar_rota(rota_id: int):
        historico = Historico(db)
        try:
            rota = historico.obter_rota(rota_id)
            if rota:
                historico.definir_ativa(rota_id, not rota.ativa)
        finally:
            historico.fechar()
        msg = "Rota retomada." if rota and not rota.ativa else "Rota pausada."
        return redirect(url_for("monitorar", msg=msg) + "#rotas", 303)

    def rodar_monitoramento(historico: Historico, provedor: ProvedorDePrecos):
        relatorio = executar_monitoramento(historico, provedor, historico.listar_rotas(somente_ativas=True),
                                           Notificador(), paralelo=PARALELO, url_site=url_site)
        historico.definir_config("ultima_verificacao",
                                 f"{datetime.now():%d/%m/%Y %H:%M} — {relatorio.resumo}")
        return relatorio

    @app.post("/monitorar/verificar")
    def verificar_agora():
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
            if ctx["provedor"] is None:
                return pagina_monitorar(historico, ctx, status=503)
            relatorio = rodar_monitoramento(historico, ctx["provedor"])
            return pagina_monitorar(historico, ctx, relatorio=relatorio)
        finally:
            historico.fechar()

    @app.post("/api/monitorar/executar")
    def api_monitorar_executar():
        """Chamado pelo agendamento diário (GitHub Actions). Exige o token."""
        if not token_monitor:
            return jsonify({"erro": "PASSAGENS_TOKEN_MONITOR não definido: agendamento desativado"}), 503
        auth = request.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.removeprefix("Bearer ").strip(), token_monitor):
            return jsonify({"erro": "token inválido"}), 401
        historico = Historico(db)
        try:
            provedor = criar_provedor(historico)
            relatorio = rodar_monitoramento(historico, provedor)
        except ErroDeProvedor as exc:
            return jsonify({"erro": str(exc)}), 502
        finally:
            historico.fechar()
        return jsonify({
            "resumo": relatorio.resumo, "email": relatorio.email,
            "rotas": [{"rota": r.rota.rotulo, "menor_preco": r.melhor.preco if r.melhor else None,
                       "alerta": r.alerta, "motivos": r.motivos} for r in relatorio.resultados],
            "novos_alertas": [{"rota": a.rota_rotulo, "preco": a.preco, "destino": a.destino}
                              for a in relatorio.novos_alertas],
        })

    return app
