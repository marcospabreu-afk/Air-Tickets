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
from .busca import buscar_flexivel, detalhar_voltas, pares_de_datas
from .historico import CacheSQLite, Historico
from .modelos import Oferta, Passageiros
from .monitor import _data, carregar_rotas, verificar_rota
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
