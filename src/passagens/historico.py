"""Histórico de preços em SQLite (e cache das respostas da API)."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .modelos import Oferta

VARIAVEL_DB = "PASSAGENS_DB"


def caminho_padrao() -> Path:
    if os.environ.get(VARIAVEL_DB):
        return Path(os.environ[VARIAVEL_DB])
    return Path.home() / ".passagens" / "historico.db"


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS precos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    consultado_em TEXT NOT NULL,
    origem        TEXT NOT NULL,
    destino       TEXT NOT NULL,
    data_ida      TEXT NOT NULL,
    data_volta    TEXT NOT NULL,
    companhia     TEXT NOT NULL,
    escalas       INTEGER NOT NULL,
    preco         REAL NOT NULL,
    provedor      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_precos_rota ON precos (origem, destino);
CREATE TABLE IF NOT EXISTS cache (
    chave      TEXT PRIMARY KEY,
    criado_em  TEXT NOT NULL,
    resposta   TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ResumoRota:
    origem: str
    destino: str
    menor: float
    maior: float
    media: float
    registros: int
    primeira: str
    ultima: str


class Historico:
    def __init__(self, caminho: str | Path | None = None):
        caminho = Path(caminho) if caminho else caminho_padrao()
        if str(caminho) != ":memory:":
            caminho.parent.mkdir(parents=True, exist_ok=True)
        self.conexao = sqlite3.connect(str(caminho))
        self.conexao.executescript(_ESQUEMA)

    def fechar(self) -> None:
        self.conexao.close()

    def gravar(self, ofertas: Iterable[Oferta], quando: datetime | None = None) -> int:
        quando_txt = (quando or datetime.now()).isoformat(timespec="seconds")
        linhas = [
            (quando_txt, o.origem, o.destino, o.data_ida.isoformat(), o.data_volta.isoformat(),
             o.companhia, o.escalas, o.preco, o.provedor)
            for o in ofertas
        ]
        with self.conexao:
            self.conexao.executemany(
                "INSERT INTO precos (consultado_em, origem, destino, data_ida, data_volta,"
                " companhia, escalas, preco, provedor) VALUES (?,?,?,?,?,?,?,?,?)",
                linhas,
            )
        return len(linhas)

    def registros(self) -> list[tuple]:
        return self.conexao.execute(
            "SELECT consultado_em, origem, destino, data_ida, data_volta, companhia, preco, provedor"
            " FROM precos ORDER BY id"
        ).fetchall()

    def resumo(self, origem: str | None = None, destino: str | None = None) -> list[ResumoRota]:
        sql = ("SELECT origem, destino, MIN(preco), MAX(preco), AVG(preco), COUNT(*),"
               " MIN(consultado_em), MAX(consultado_em) FROM precos")
        filtros, args = [], []
        if origem:
            filtros.append("origem = ?"); args.append(origem)
        if destino:
            filtros.append("destino = ?"); args.append(destino)
        if filtros:
            sql += " WHERE " + " AND ".join(filtros)
        sql += " GROUP BY origem, destino ORDER BY origem, destino"
        return [ResumoRota(*linha) for linha in self.conexao.execute(sql, args)]

    def media(self, origem: str, destino: str) -> float | None:
        (valor,) = self.conexao.execute(
            "SELECT AVG(preco) FROM precos WHERE origem = ? AND destino = ?", (origem, destino)
        ).fetchone()
        return valor


class CacheSQLite:
    """Cache simples de respostas JSON, para não gastar cota da API à toa."""

    def __init__(self, historico: Historico, validade: timedelta = timedelta(hours=6)):
        self.conexao = historico.conexao
        self.validade = validade

    def obter(self, chave: str) -> Any | None:
        linha = self.conexao.execute(
            "SELECT criado_em, resposta FROM cache WHERE chave = ?", (chave,)
        ).fetchone()
        if not linha:
            return None
        if datetime.now() - datetime.fromisoformat(linha[0]) > self.validade:
            return None
        return json.loads(linha[1])

    def guardar(self, chave: str, valor: Any) -> None:
        with self.conexao:
            self.conexao.execute(
                "INSERT OR REPLACE INTO cache (chave, criado_em, resposta) VALUES (?,?,?)",
                (chave, datetime.now().isoformat(timespec="seconds"), json.dumps(valor)),
            )
