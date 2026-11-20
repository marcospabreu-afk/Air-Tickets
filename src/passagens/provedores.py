"""Fontes de preço. Todo o resto do programa só conhece `ProvedorDePrecos`."""

from __future__ import annotations

import json
import os
import threading
import zlib
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Callable

from .modelos import Oferta

VARIAVEL_CHAVE_SERPAPI = "SERPAPI_API_KEY"


class ErroDeProvedor(RuntimeError):
    """Falha ao consultar a fonte de preços."""


class ProvedorDePrecos(ABC):
    """Interface de qualquer fonte de preços de ida e volta."""

    nome: str = "abstrato"
    dados_reais: bool = True

    @abstractmethod
    def buscar(self, origem: str, destino: str, ida: date, volta: date) -> list[Oferta]:
        """Retorna as ofertas encontradas para exatamente essas datas."""

    def detalhar_volta(self, oferta: Oferta) -> Oferta:
        """Completa os horários do voo de volta da oferta (pode custar 1 consulta).

        Fontes que já trazem a volta na busca principal não precisam sobrescrever.
        """
        return oferta


# --------------------------------------------------------------------------- #
# Provedor falso: dados fixos e determinísticos (testes e demonstração)
# --------------------------------------------------------------------------- #

_COMPANHIAS_FALSAS = [
    # companhia, partida, chegada, escalas, duração (min), fator de preço
    ("TAP Air Portugal", "16:40", "08:15+1", 1, 815, 1.00),
    ("LATAM", "21:05", "14:30+1", 1, 925, 1.07),
    ("Azul", "06:10", "05:55+1", 2, 1065, 0.93),
]


class ProvedorFalso(ProvedorDePrecos):
    """Gera preços fixos a partir da rota e das datas (sem rede, sem aleatoriedade).

    `fator` multiplica todos os preços — útil para simular uma queda de preço.
    """

    nome = "falso"
    dados_reais = False

    def __init__(self, fator: float = 1.0):
        self.fator = fator
        self.consultas: list[tuple[str, str, date, date]] = []

    def buscar(self, origem: str, destino: str, ida: date, volta: date) -> list[Oferta]:
        self.consultas.append((origem, destino, ida, volta))
        base = 3000 + zlib.crc32(f"{origem}{destino}".encode()) % 2000
        # Variações fixas pelo dia da semana e pela duração da viagem.
        ajuste_dia = [0, -120, -180, -90, 150, 260, 80][ida.weekday()]
        ajuste_volta = [40, -60, -100, -30, 90, 200, 160][volta.weekday()]
        estadia = (volta - ida).days
        ajuste_estadia = abs(estadia - 14) * 25
        total = base + ajuste_dia + ajuste_volta + ajuste_estadia
        link = (
            "https://www.google.com/travel/flights?q="
            f"{origem}+{destino}+{ida.isoformat()}+{volta.isoformat()}"
        )
        return [
            Oferta(
                companhia=cia,
                origem=origem,
                destino=destino,
                data_ida=ida,
                data_volta=volta,
                partida_ida=partida,
                chegada_ida=chegada,
                escalas=escalas,
                duracao_min=duracao,
                preco=round(total * fator_cia * self.fator, 2),
                link=link,
                provedor=self.nome,
                token_volta=f"falso:{cia}",
            )
            for cia, partida, chegada, escalas, duracao, fator_cia in _COMPANHIAS_FALSAS
        ]

    def detalhar_volta(self, oferta: Oferta) -> Oferta:
        if not oferta.token_volta:
            return oferta
        self.consultas_volta = getattr(self, "consultas_volta", 0) + 1
        return replace(oferta, partida_volta="10:20", chegada_volta="05:40+1",
                       escalas_volta=1, duracao_volta_min=900)


# --------------------------------------------------------------------------- #
# Provedor real: SerpApi (Google Flights)
# --------------------------------------------------------------------------- #

URL_SERPAPI = "https://serpapi.com/search.json"


def _hora(texto: str, dia_base: date) -> str:
    """'2027-03-10 22:05' -> '22:05' (com '+N' se cair em outro dia)."""
    momento = datetime.strptime(texto, "%Y-%m-%d %H:%M")
    dias = (momento.date() - dia_base).days
    return momento.strftime("%H:%M") + (f"+{dias}" if dias > 0 else "")


def _companhias(trechos: list[dict[str, Any]]) -> str:
    nomes: list[str] = []
    for trecho in trechos:
        nome = trecho.get("airline", "?")
        if nome not in nomes:
            nomes.append(nome)
    return " / ".join(nomes)


class ProvedorSerpApi(ProvedorDePrecos):
    """Google Flights via SerpApi (https://serpapi.com/google-flights-api).

    A chave vem da variável de ambiente SERPAPI_API_KEY. `cache` é opcional e
    evita gastar cota repetindo a mesma consulta (ver historico.CacheSQLite).
    """

    nome = "serpapi"
    dados_reais = True

    def __init__(
        self,
        chave: str | None = None,
        cache: Any | None = None,
        http_get: Callable[..., Any] | None = None,
    ):
        self.chave = chave or os.environ.get(VARIAVEL_CHAVE_SERPAPI)
        if not self.chave:
            raise ErroDeProvedor(
                f"Variável de ambiente {VARIAVEL_CHAVE_SERPAPI} não definida. "
                "Crie uma chave grátis em https://serpapi.com/users/sign_up e rode "
                f"`export {VARIAVEL_CHAVE_SERPAPI}=...` (ou use --provedor falso)."
            )
        self.cache = cache
        if http_get is None:
            import requests

            http_get = requests.get
        self._get = http_get
        self.consultas_feitas = 0
        self._trava = threading.Lock()

    def _consultar(self, params: dict[str, Any]) -> dict[str, Any]:
        params = {
            "engine": "google_flights",
            "currency": "BRL",
            "hl": "pt-BR",
            "gl": "br",
            "adults": 1,
            **params,
        }
        chave_cache = json.dumps(params, sort_keys=True, default=str)
        if self.cache is not None:
            guardado = self.cache.obter(chave_cache)
            if guardado is not None:
                return guardado
        resposta = self._get(URL_SERPAPI, params={**params, "api_key": self.chave}, timeout=60)
        with self._trava:
            self.consultas_feitas += 1
        try:
            dados = resposta.json()
        except ValueError as exc:
            raise ErroDeProvedor(f"Resposta inválida da SerpApi (HTTP {resposta.status_code})") from exc
        if resposta.status_code != 200 or "error" in dados:
            erro = dados.get("error", f"HTTP {resposta.status_code}")
            # "hasn't returned any results" não é erro: só não há voos nessas datas.
            if "returned any results" in str(erro):
                dados = {"best_flights": [], "other_flights": []}
            else:
                raise ErroDeProvedor(f"SerpApi: {erro}")
        if self.cache is not None:
            self.cache.guardar(chave_cache, dados)
        return dados

    @staticmethod
    def _parametros(origem: str, destino: str, ida: date, volta: date) -> dict[str, Any]:
        return {
            "type": 1,  # ida e volta
            "departure_id": origem,
            "arrival_id": destino,
            "outbound_date": ida.isoformat(),
            "return_date": volta.isoformat(),
        }

    def buscar(self, origem: str, destino: str, ida: date, volta: date) -> list[Oferta]:
        dados = self._consultar(self._parametros(origem, destino, ida, volta))
        link = dados.get("search_metadata", {}).get("google_flights_url", "")
        ofertas = []
        for item in dados.get("best_flights", []) + dados.get("other_flights", []):
            trechos = item.get("flights") or []
            if not trechos or "price" not in item:
                continue
            ofertas.append(
                Oferta(
                    companhia=_companhias(trechos),
                    origem=origem,
                    destino=destino,
                    data_ida=ida,
                    data_volta=volta,
                    partida_ida=_hora(trechos[0]["departure_airport"]["time"], ida),
                    chegada_ida=_hora(trechos[-1]["arrival_airport"]["time"], ida),
                    escalas=len(item.get("layovers") or []) or len(trechos) - 1,
                    duracao_min=int(item.get("total_duration", 0)),
                    preco=float(item["price"]),
                    link=link,
                    provedor=self.nome,
                    token_volta=item.get("departure_token"),
                )
            )
        return ofertas

    def detalhar_volta(self, oferta: Oferta) -> Oferta:
        """Uma consulta extra (com cache): opções de volta para o voo de ida escolhido.

        Usa a opção de volta com o mesmo preço total da oferta (é a combinação que
        o Google Flights anuncia); se não houver, a mais barata, com o preço dela.
        """
        if not oferta.token_volta or oferta.tem_volta or oferta.volta_indisponivel:
            return oferta
        params = self._parametros(oferta.origem, oferta.destino, oferta.data_ida, oferta.data_volta)
        dados = self._consultar({**params, "departure_token": oferta.token_volta})
        opcoes = [o for o in dados.get("best_flights", []) + dados.get("other_flights", [])
                  if o.get("flights") and "price" in o]
        if not opcoes:
            return replace(oferta, volta_indisponivel=True)
        escolhida = next((o for o in opcoes if float(o["price"]) == oferta.preco),
                         min(opcoes, key=lambda o: float(o["price"])))
        trechos = escolhida["flights"]
        return replace(
            oferta,
            partida_volta=_hora(trechos[0]["departure_airport"]["time"], oferta.data_volta),
            chegada_volta=_hora(trechos[-1]["arrival_airport"]["time"], oferta.data_volta),
            escalas_volta=len(escolhida.get("layovers") or []) or len(trechos) - 1,
            duracao_volta_min=int(escolhida.get("total_duration", 0)),
            preco=float(escolhida["price"]),
            preco_anunciado=oferta.preco if float(escolhida["price"]) != oferta.preco else None,
        )
