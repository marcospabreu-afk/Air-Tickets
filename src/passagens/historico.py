"""Banco de dados: histórico de preços, cache da API, rotas monitoradas e alertas.

Funciona com SQLite (arquivo local, padrão) ou Postgres (ex.: Neon gratuito), para
quem publica o site num serviço cujo disco é apagado ao reiniciar (Render grátis).

Escolha do banco, nesta ordem:
  1. caminho/URL passado explicitamente (ex.: --db);
  2. variável DATABASE_URL (postgresql://...);
  3. variável PASSAGENS_DB (arquivo SQLite);
  4. ~/.passagens/historico.db
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .modelos import UM_ADULTO, Alerta, Oferta, Passageiros, Rota

VARIAVEL_DB = "PASSAGENS_DB"
VARIAVEL_URL = "DATABASE_URL"


def caminho_padrao() -> str:
    if os.environ.get(VARIAVEL_URL):
        return os.environ[VARIAVEL_URL]
    if os.environ.get(VARIAVEL_DB):
        return os.environ[VARIAVEL_DB]
    return str(Path.home() / ".passagens" / "historico.db")


def _e_postgres(destino: str) -> bool:
    return destino.startswith(("postgres://", "postgresql://"))


def _esquema(postgres: bool) -> list[str]:
    chave = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    real = "DOUBLE PRECISION" if postgres else "REAL"
    return [
        f"""CREATE TABLE IF NOT EXISTS precos (
            id            {chave},
            consultado_em TEXT NOT NULL,
            origem        TEXT NOT NULL,
            destino       TEXT NOT NULL,
            data_ida      TEXT NOT NULL,
            data_volta    TEXT NOT NULL,
            companhia     TEXT NOT NULL,
            escalas       INTEGER NOT NULL,
            preco         {real} NOT NULL,
            provedor      TEXT NOT NULL,
            passageiros   TEXT NOT NULL DEFAULT '1A'
        )""",
        """CREATE TABLE IF NOT EXISTS cache (
            chave      TEXT PRIMARY KEY,
            criado_em  TEXT NOT NULL,
            resposta   TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS rotas (
            id          {chave},
            nome        TEXT NOT NULL DEFAULT '',
            origem      TEXT NOT NULL,
            destino     TEXT NOT NULL,
            ida         TEXT NOT NULL,
            volta       TEXT NOT NULL,
            flex        INTEGER NOT NULL DEFAULT 0,
            preco_alvo  {real},
            adultos     INTEGER NOT NULL DEFAULT 1,
            criancas    INTEGER NOT NULL DEFAULT 0,
            bebes       INTEGER NOT NULL DEFAULT 0,
            ativa       INTEGER NOT NULL DEFAULT 1,
            criada_em   TEXT NOT NULL,
            alterada_em TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS alertas (
            id            {chave},
            rota_id       INTEGER,
            rota_rotulo   TEXT NOT NULL DEFAULT '',
            criado_em     TEXT NOT NULL,
            preco         {real} NOT NULL,
            companhia     TEXT NOT NULL,
            data_ida      TEXT NOT NULL,
            data_volta    TEXT NOT NULL,
            destino       TEXT NOT NULL,
            motivos       TEXT NOT NULL,
            link          TEXT NOT NULL,
            email_enviado INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_cache_criado ON cache (criado_em)",
        "CREATE INDEX IF NOT EXISTS idx_alertas_rota ON alertas (rota_id, criado_em)",
    ]


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
    passageiros: str = "1A"

    @property
    def grupo(self) -> Passageiros:
        return Passageiros.do_codigo(self.passageiros)


class Historico:
    """Acesso ao banco. Seguro para uso em várias threads (uma trava por conexão)."""

    def __init__(self, caminho: str | Path | None = None):
        destino = str(caminho) if caminho else caminho_padrao()
        self.postgres = _e_postgres(destino)
        self.trava = threading.RLock()
        if self.postgres:
            import psycopg

            self.conexao = psycopg.connect(destino, autocommit=True)
        else:
            if destino != ":memory:":
                Path(destino).parent.mkdir(parents=True, exist_ok=True)
            self.conexao = sqlite3.connect(destino, check_same_thread=False)
        with self.transacao():
            for comando in _esquema(self.postgres):
                self._executar(comando)
            self._migrar()

    # ------------------------------------------------------------ infraestrutura

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.postgres else sql

    def _executar(self, sql: str, args: Sequence[Any] = ()) -> Any:
        return self.conexao.execute(self._sql(sql), tuple(args))

    @contextmanager
    def transacao(self) -> Iterator[None]:
        with self.trava:
            if self.postgres:
                with self.conexao.transaction():
                    yield
            else:
                with self.conexao:
                    yield

    def consultar(self, sql: str, args: Sequence[Any] = ()) -> list[tuple]:
        with self.trava:
            return list(self._executar(sql, args).fetchall())

    def executar(self, sql: str, args: Sequence[Any] = ()) -> None:
        with self.transacao():
            self._executar(sql, args)

    def _inserir(self, sql: str, args: Sequence[Any]) -> int:
        """INSERT que devolve o id criado (sem transação própria)."""
        if self.postgres:
            return int(self._executar(sql + " RETURNING id", args).fetchone()[0])
        return int(self._executar(sql, args).lastrowid)

    def _migrar(self) -> None:
        """Bancos SQLite criados antes do suporte a vários passageiros: tudo era 1 adulto."""
        if self.postgres:
            self._executar("ALTER TABLE precos ADD COLUMN IF NOT EXISTS passageiros TEXT NOT NULL DEFAULT '1A'")
        else:
            colunas = {linha[1] for linha in self._executar("PRAGMA table_info(precos)").fetchall()}
            if "passageiros" not in colunas:
                self._executar("ALTER TABLE precos ADD COLUMN passageiros TEXT NOT NULL DEFAULT '1A'")
        self._executar("DROP INDEX IF EXISTS idx_precos_rota")
        self._executar("CREATE INDEX IF NOT EXISTS idx_precos_rota_grupo ON precos (origem, destino, passageiros)")

    def fechar(self) -> None:
        self.conexao.close()

    # ------------------------------------------------------------ preços

    def gravar(self, ofertas: Iterable[Oferta], quando: datetime | None = None) -> int:
        quando_txt = (quando or datetime.now()).isoformat(timespec="seconds")
        linhas = [
            (quando_txt, o.origem, o.destino, o.data_ida.isoformat(), o.data_volta.isoformat(),
             o.companhia, o.escalas, o.preco, o.provedor, o.passageiros.codigo)
            for o in ofertas
        ]
        if not linhas:
            return 0
        with self.transacao():
            cursor = self.conexao.cursor()
            cursor.executemany(
                self._sql("INSERT INTO precos (consultado_em, origem, destino, data_ida, data_volta,"
                          " companhia, escalas, preco, provedor, passageiros) VALUES (?,?,?,?,?,?,?,?,?,?)"),
                linhas,
            )
        return len(linhas)

    def registros(self) -> list[tuple]:
        return self.consultar(
            "SELECT consultado_em, origem, destino, data_ida, data_volta, companhia, preco, provedor,"
            " passageiros FROM precos ORDER BY id"
        )

    def resumo(self, origem: str | None = None, destino: str | None = None) -> list[ResumoRota]:
        sql = ("SELECT origem, destino, MIN(preco), MAX(preco), AVG(preco), COUNT(*),"
               " MIN(consultado_em), MAX(consultado_em), passageiros FROM precos")
        filtros, args = [], []
        if origem:
            filtros.append("origem = ?"); args.append(origem)
        if destino:
            filtros.append("destino = ?"); args.append(destino)
        if filtros:
            sql += " WHERE " + " AND ".join(filtros)
        # Cada composição de grupo é uma série separada: o preço é o total do grupo.
        sql += " GROUP BY origem, destino, passageiros ORDER BY origem, destino, passageiros"
        return [ResumoRota(l[0], l[1], float(l[2]), float(l[3]), float(l[4]), int(l[5]), l[6], l[7], l[8])
                for l in self.consultar(sql, args)]

    def media(self, origem: str, destino: str, passageiros: Passageiros = UM_ADULTO) -> float | None:
        """Média de todos os preços gravados da rota (inclui classes caras)."""
        ((valor,),) = self.consultar(
            "SELECT AVG(preco) FROM precos WHERE origem = ? AND destino = ? AND passageiros = ?",
            (origem, destino, passageiros.codigo),
        )
        return None if valor is None else float(valor)

    def media_dos_menores(self, origem: str, destino: str,
                          passageiros: Passageiros = UM_ADULTO) -> float | None:
        """Média do MENOR preço de cada consulta da rota.

        É a referência usada no alerta: comparar o menor preço de hoje com a média
        de todas as ofertas (que inclui voos muito mais caros) daria alerta quase sempre.
        Cada execução de `gravar` usa um único carimbo de data e hora = uma consulta.
        """
        ((valor,),) = self.consultar(
            "SELECT AVG(menor) FROM (SELECT MIN(preco) AS menor FROM precos"
            " WHERE origem = ? AND destino = ? AND passageiros = ? GROUP BY consultado_em) AS consultas",
            (origem, destino, passageiros.codigo),
        )
        return None if valor is None else float(valor)

    # ------------------------------------------------------------ rotas monitoradas

    _COLUNAS_ROTA = ("id, nome, origem, destino, ida, volta, flex, preco_alvo, adultos, criancas, bebes,"
                     " ativa, alterada_em")

    @staticmethod
    def _rota(linha: tuple) -> Rota:
        (id_, nome, origem, destino, ida, volta, flex, alvo, adultos, criancas, bebes, ativa, _) = linha
        return Rota(origem=origem, destino=destino, ida=date.fromisoformat(ida),
                    volta=date.fromisoformat(volta), flex=int(flex),
                    preco_alvo=None if alvo is None else float(alvo),
                    passageiros=Passageiros(int(adultos), int(criancas), int(bebes)),
                    id=int(id_), nome=nome, ativa=bool(ativa))

    def listar_rotas(self, somente_ativas: bool = False) -> list[Rota]:
        sql = f"SELECT {self._COLUNAS_ROTA} FROM rotas"
        if somente_ativas:
            sql += " WHERE ativa = 1"
        return [self._rota(l) for l in self.consultar(sql + " ORDER BY id")]

    def obter_rota(self, rota_id: int) -> Rota | None:
        linhas = self.consultar(f"SELECT {self._COLUNAS_ROTA} FROM rotas WHERE id = ?", (rota_id,))
        return self._rota(linhas[0]) if linhas else None

    def salvar_rota(self, rota: Rota) -> int:
        """Cria (rota.id None) ou atualiza a rota. Devolve o id."""
        p = rota.passageiros
        valores = (rota.nome, rota.origem, rota.destino, rota.ida.isoformat(), rota.volta.isoformat(),
                   rota.flex, rota.preco_alvo, p.adultos, p.criancas, p.bebes, int(rota.ativa))
        agora = _agora()
        with self.transacao():
            if rota.id is None:
                return self._inserir(
                    "INSERT INTO rotas (nome, origem, destino, ida, volta, flex, preco_alvo, adultos, criancas,"
                    " bebes, ativa, criada_em, alterada_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    valores + (agora, agora))
            self._executar(
                "UPDATE rotas SET nome=?, origem=?, destino=?, ida=?, volta=?, flex=?, preco_alvo=?, adultos=?,"
                " criancas=?, bebes=?, ativa=?, alterada_em=? WHERE id=?",
                valores + (agora, rota.id))
            return rota.id

    def definir_ativa(self, rota_id: int, ativa: bool) -> None:
        # Pausar/retomar não muda a rota: não reinicia o controle de alertas.
        self.executar("UPDATE rotas SET ativa = ? WHERE id = ?", (int(ativa), rota_id))

    def excluir_rota(self, rota_id: int) -> None:
        with self.transacao():
            self._executar("DELETE FROM rotas WHERE id = ?", (rota_id,))

    def importar_rotas(self, rotas: Iterable[Rota]) -> int:
        """Copia as rotas de um rotas.yaml para o banco uma única vez."""
        if self.config("rotas_importadas"):
            return 0
        n = 0
        for rota in rotas:
            self.salvar_rota(rota)
            n += 1
        self.definir_config("rotas_importadas", _agora())
        return n

    # ------------------------------------------------------------ alertas

    def registrar_alerta(self, alerta: Alerta) -> int:
        with self.transacao():
            return self._inserir(
                "INSERT INTO alertas (rota_id, rota_rotulo, criado_em, preco, companhia, data_ida, data_volta,"
                " destino, motivos, link, email_enviado) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (alerta.rota_id, alerta.rota_rotulo, alerta.criado_em, alerta.preco, alerta.companhia,
                 alerta.data_ida.isoformat(), alerta.data_volta.isoformat(), alerta.destino, alerta.motivos,
                 alerta.link, int(alerta.email_enviado)))

    def marcar_email_enviado(self, ids: Iterable[int]) -> None:
        with self.transacao():
            for alerta_id in ids:
                self._executar("UPDATE alertas SET email_enviado = 1 WHERE id = ?", (alerta_id,))

    @staticmethod
    def _alerta(l: tuple) -> Alerta:
        return Alerta(id=int(l[0]), rota_id=l[1], rota_rotulo=l[2], criado_em=l[3], preco=float(l[4]),
                      companhia=l[5], data_ida=date.fromisoformat(l[6]), data_volta=date.fromisoformat(l[7]),
                      destino=l[8], motivos=l[9], link=l[10], email_enviado=bool(l[11]))

    _COLUNAS_ALERTA = ("id, rota_id, rota_rotulo, criado_em, preco, companhia, data_ida, data_volta, destino,"
                       " motivos, link, email_enviado")

    def alertas_recentes(self, limite: int = 30) -> list[Alerta]:
        return [self._alerta(l) for l in self.consultar(
            f"SELECT {self._COLUNAS_ALERTA} FROM alertas ORDER BY id DESC LIMIT ?", (limite,))]

    def ultimo_alerta(self, rota_id: int) -> Alerta | None:
        """Último alerta da rota desde a última edição dela (editar reinicia o controle)."""
        linhas = self.consultar(
            f"SELECT {self._COLUNAS_ALERTA} FROM alertas a WHERE rota_id = ? AND criado_em >="
            " (SELECT alterada_em FROM rotas r WHERE r.id = a.rota_id) ORDER BY id DESC LIMIT 1",
            (rota_id,))
        return self._alerta(linhas[0]) if linhas else None

    # ------------------------------------------------------------ configurações

    def config(self, chave: str) -> str | None:
        linhas = self.consultar("SELECT valor FROM config WHERE chave = ?", (chave,))
        return linhas[0][0] if linhas else None

    def definir_config(self, chave: str, valor: str) -> None:
        self.executar(
            "INSERT INTO config (chave, valor) VALUES (?, ?)"
            " ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor", (chave, valor))


class CacheSQLite:
    """Cache de respostas JSON da API (SQLite ou Postgres), para não gastar cota à toa.

    Entradas vencidas são apagadas ao gravar novas, para o banco não crescer
    (o plano grátis do Neon tem 1 GB, e cada resposta tem dezenas de KB).
    """

    def __init__(self, historico: Historico, validade: timedelta = timedelta(hours=6)):
        self.historico = historico
        self.validade = validade

    def obter(self, chave: str) -> Any | None:
        linhas = self.historico.consultar("SELECT criado_em, resposta FROM cache WHERE chave = ?", (chave,))
        if not linhas:
            return None
        criado_em, resposta = linhas[0]
        if datetime.now() - datetime.fromisoformat(criado_em) > self.validade:
            return None
        return json.loads(resposta)

    def guardar(self, chave: str, valor: Any) -> None:
        limite = (datetime.now() - self.validade).isoformat(timespec="seconds")
        with self.historico.transacao():
            self.historico._executar("DELETE FROM cache WHERE criado_em < ?", (limite,))
            self.historico._executar(
                "INSERT INTO cache (chave, criado_em, resposta) VALUES (?, ?, ?)"
                " ON CONFLICT (chave) DO UPDATE SET criado_em = excluded.criado_em, resposta = excluded.resposta",
                (chave, _agora(), json.dumps(valor)))
