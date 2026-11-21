"""Verificação das rotas monitoradas (usada pela CLI e pela interface web)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from .alertas import motivos_de_alerta
from .busca import buscar_flexivel
from .historico import Historico
from .modelos import UM_ADULTO, Alerta, Oferta, Passageiros, Rota
from .provedores import ProvedorDePrecos

try:  # só para anotações de tipo
    from .notificacao import Notificador
except ImportError:  # pragma: no cover
    Notificador = None  # type: ignore


@dataclass
class ResultadoRota:
    rota: Rota
    melhor: Oferta | None
    media_dos_menores: float | None
    motivos: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)

    @property
    def alerta(self) -> bool:
        return bool(self.motivos)


def _iata(valor) -> str:
    codigo = str(valor).strip().upper()
    if len(codigo) != 3 or not codigo.isalpha():
        raise ValueError(f"código IATA inválido: {valor!r}")
    return codigo


MAX_DESTINOS = 7


def destinos_validos(valor) -> str:
    """'lis, opo FAO' -> 'LIS,OPO,FAO' (uma região: vários aeroportos numa só consulta)."""
    partes = [p for p in str(valor).replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    codigos: list[str] = []
    for parte in partes:
        codigo = _iata(parte)
        if codigo not in codigos:
            codigos.append(codigo)
    if not codigos:
        raise ValueError("informe ao menos um aeroporto de destino")
    if len(codigos) > MAX_DESTINOS:
        raise ValueError(f"no máximo {MAX_DESTINOS} aeroportos de destino por rota")
    return ",".join(codigos)


def _data(valor) -> date:
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        raise ValueError(f"data inválida: {valor!r} (use AAAA-MM-DD)") from None


def carregar_rotas(caminho: str | Path) -> list[Rota]:
    with open(caminho, encoding="utf-8") as arquivo:
        dados = yaml.safe_load(arquivo) or {}
    rotas = []
    for i, r in enumerate(dados.get("rotas") or [], 1):
        faltando = {"origem", "destino", "ida", "volta"} - set(r)
        if faltando:
            raise ValueError(f"rota #{i} em {caminho}: faltam campos {sorted(faltando)}")
        rotas.append(Rota(
            origem=_iata(r["origem"]),
            destino=destinos_validos(r["destino"]),
            ida=_data(r["ida"]),
            volta=_data(r["volta"]),
            flex=int(r.get("flex", 0)),
            preco_alvo=float(r["preco_alvo"]) if r.get("preco_alvo") is not None else None,
            passageiros=Passageiros(int(r.get("adultos", 1)), int(r.get("criancas", 0)),
                                    int(r.get("bebes", 0))),
            nome=str(r.get("nome", "")),
        ))
    return rotas


def verificar_rota(rota: Rota, historico: Historico, provedor: ProvedorDePrecos,
                   paralelo: int = 1) -> ResultadoRota:
    # Referência: média do menor preço de cada consulta anterior, calculada ANTES
    # de gravar os preços novos, para comparar hoje com o passado.
    media = historico.media_dos_menores(rota.origem, rota.destino, rota.passageiros)
    busca = buscar_flexivel(provedor, rota.origem, rota.destino, rota.ida, rota.volta,
                            rota.flex, paralelo=paralelo, passageiros=rota.passageiros)
    historico.gravar(busca.ofertas)
    if not busca.ofertas:
        return ResultadoRota(rota, None, media, erros=busca.erros)
    melhor = busca.ofertas[0]
    return ResultadoRota(rota, melhor, media, motivos_de_alerta(melhor.preco, rota.preco_alvo, media),
                         busca.erros)


# --------------------------------------------------------------------------- #
# Execução do monitoramento com registro de alertas e aviso por e-mail
# --------------------------------------------------------------------------- #


@dataclass
class RelatorioMonitoramento:
    resultados: list[ResultadoRota] = field(default_factory=list)
    novos_alertas: list[Alerta] = field(default_factory=list)
    email: str = ""  # "enviado", "não configurado", "sem novos alertas" ou o erro

    @property
    def resumo(self) -> str:
        return (f"{len(self.resultados)} rota(s) verificada(s), {len(self.novos_alertas)} alerta(s) novo(s); "
                f"e-mail: {self.email}")


def rotas_do_banco(historico: Historico, arquivo_inicial: str | Path | None = "rotas.yaml") -> list[Rota]:
    """Rotas ativas do banco. Na primeira vez, importa as do rotas.yaml (se existir)."""
    if arquivo_inicial and Path(arquivo_inicial).exists() and not historico.listar_rotas():
        historico.importar_rotas(carregar_rotas(arquivo_inicial))
    return historico.listar_rotas(somente_ativas=True)


def novo_alerta(resultado: ResultadoRota, historico: Historico) -> Alerta | None:
    """Alerta a registrar, ou None.

    Avisa quando a regra de alerta vale E o preço é menor que o do último alerta
    desta rota (desde a última edição dela) — assim o e-mail chega quando o preço
    cai, e não todo dia pelo mesmo valor.
    """
    if not resultado.alerta or resultado.melhor is None:
        return None
    rota, melhor = resultado.rota, resultado.melhor
    if rota.id is not None:
        anterior = historico.ultimo_alerta(rota.id)
        if anterior is not None and melhor.preco >= anterior.preco:
            return None
    return Alerta(
        rota_id=rota.id, rota_rotulo=rota.rotulo, criado_em=datetime.now().isoformat(timespec="seconds"),
        preco=melhor.preco, companhia=melhor.companhia, data_ida=melhor.data_ida,
        data_volta=melhor.data_volta, destino=melhor.aeroporto_destino or rota.destino,
        motivos="; ".join(resultado.motivos), link=melhor.link)


def executar_monitoramento(historico: Historico, provedor: ProvedorDePrecos, rotas: list[Rota],
                           notificador: "Notificador | None" = None, paralelo: int = 1,
                           url_site: str = "") -> RelatorioMonitoramento:
    from .notificacao import ErroDeEnvio, montar_mensagem

    relatorio = RelatorioMonitoramento()
    ids_novos: list[int] = []
    for rota in rotas:
        resultado = verificar_rota(rota, historico, provedor, paralelo=paralelo)
        relatorio.resultados.append(resultado)
        alerta = novo_alerta(resultado, historico)
        if alerta is not None:
            ids_novos.append(historico.registrar_alerta(alerta))
            relatorio.novos_alertas.append(alerta)
    if not relatorio.novos_alertas:
        relatorio.email = "sem novos alertas"
    elif notificador is None or not notificador.configurado:
        relatorio.email = "não configurado"
    else:
        try:
            notificador.enviar(montar_mensagem(relatorio.novos_alertas, url_site))
            historico.marcar_email_enviado(ids_novos)
            relatorio.email = "enviado"
        except ErroDeEnvio as exc:
            relatorio.email = f"falhou: {exc}"
    return relatorio
