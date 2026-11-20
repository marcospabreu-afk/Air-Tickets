"""Interface web do `passagens` (Flask).

Rodar localmente:   flask --app 'passagens.web:criar_app()' run
Produção:           gunicorn 'passagens.web:criar_app()'

Variáveis de ambiente:
  SERPAPI_API_KEY     chave da SerpApi (obrigatória com o provedor real)
  PASSAGENS_SENHA     se definida, o site pede essa senha (usuário: qualquer)
  PASSAGENS_PROVEDOR  serpapi (padrão) ou falso
  PASSAGENS_DB        arquivo SQLite do histórico
  PASSAGENS_ROTAS     arquivo de rotas monitoradas (padrão: rotas.yaml)
"""

from __future__ import annotations

import hmac
import os
import time
from datetime import date, timedelta
from typing import Callable

from flask import Flask, Response, jsonify, render_template, request

from . import aeroportos
from .alertas import formatar_brl
from .busca import buscar_flexivel, pares_de_datas
from .historico import CacheSQLite, Historico
from .monitor import _data, carregar_rotas, verificar_rota
from .provedores import ErroDeProvedor, ProvedorDePrecos, ProvedorFalso, ProvedorSerpApi

FLEX_MAXIMO = 3
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

    @app.before_request
    def exigir_senha():
        if not senha or request.path == "/saude":
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
        lista = aeroportos.buscar(request.args.get("q", ""), limite=12)
        return jsonify([{"iata": a.iata, "cidade": a.cidade, "nome": a.nome, "pais": a.pais,
                         "rotulo": a.rotulo} for a in lista])

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
        }
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
        finally:
            historico.fechar()
        return render_template("busca.html", form=form, resultado=None, flex_maximo=FLEX_MAXIMO,
                               pagina="busca", **ctx)

    @app.post("/buscar")
    def buscar():
        form = {k: request.form.get(k, "").strip() for k in ("origem", "destino", "ida", "volta", "flex")}
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
            erro = None
            resultado = None
            consultas = None
            segundos = None
            try:
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
            except ValueError as exc:
                erro = str(exc)
            if erro is None and ctx["provedor"] is not None:
                inicio = time.monotonic()
                resultado = buscar_flexivel(ctx["provedor"], origem, destino, ida, volta, flex,
                                            paralelo=PARALELO)
                segundos = time.monotonic() - inicio
                historico.gravar(resultado.ofertas)
                consultas = getattr(ctx["provedor"], "consultas_feitas", None)
        finally:
            historico.fechar()
        return render_template("busca.html", form=form, resultado=resultado, erro=erro,
                               consultas=consultas, segundos=segundos, flex_maximo=FLEX_MAXIMO,
                               pagina="busca", **ctx)

    @app.get("/historico")
    def ver_historico():
        historico = Historico(db)
        try:
            resumos = historico.resumo()
            menores = {(r.origem, r.destino): historico.media_dos_menores(r.origem, r.destino)
                       for r in resumos}
        finally:
            historico.fechar()
        return render_template("historico.html", resumos=resumos, menores=menores, pagina="historico")

    @app.route("/monitorar", methods=["GET", "POST"])
    def monitorar():
        erro = None
        lista = []
        resultados = None
        try:
            lista = carregar_rotas(rotas)
        except (OSError, ValueError) as exc:
            erro = f"Não foi possível ler {rotas}: {exc}"
        historico = Historico(db)
        try:
            ctx = contexto_base(historico)
            if request.method == "POST" and not erro and ctx["provedor"] is not None:
                resultados = [verificar_rota(r, historico, ctx["provedor"], paralelo=PARALELO)
                              for r in lista]
        finally:
            historico.fechar()
        return render_template("monitorar.html", rotas=lista, resultados=resultados, erro=erro,
                               arquivo=rotas, pagina="monitorar", **ctx)

    return app
