"""Interface de linha de comando: `passagens buscar | historico | monitorar`."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from typing import Sequence


from .alertas import formatar_brl
from .busca import buscar_flexivel, detalhar_voltas, pares_de_datas
from .monitor import carregar_rotas, executar_monitoramento, rotas_do_banco
from .notificacao import Notificador
from .historico import CacheSQLite, Historico
from .modelos import Oferta, Passageiros
from .provedores import ErroDeProvedor, ProvedorDePrecos, ProvedorFalso, ProvedorSerpApi


# ----------------------------------------------------------------- utilidades


def _data(texto: str) -> date:
    try:
        return date.fromisoformat(str(texto))
    except ValueError:
        raise argparse.ArgumentTypeError(f"data inválida: {texto!r} (use AAAA-MM-DD)")


def _iata(texto: str) -> str:
    codigo = texto.strip().upper()
    if len(codigo) != 3 or not codigo.isalpha():
        raise argparse.ArgumentTypeError(f"código IATA inválido: {texto!r}")
    return codigo


def _duracao(minutos: int) -> str:
    return f"{minutos // 60}h{minutos % 60:02d}"


def _criar_provedor(args: argparse.Namespace, historico: Historico) -> ProvedorDePrecos:
    if args.provedor == "falso":
        return ProvedorFalso(fator=float(os.environ.get("PASSAGENS_FALSO_FATOR", "1")))
    return ProvedorSerpApi(
        cache=CacheSQLite(historico) if not args.sem_cache else None,
    )


def _escalas(o: Oferta) -> str:
    def texto(n: int | None) -> str:
        return "?" if n is None else ("direto" if n == 0 else str(n))
    return f"{texto(o.escalas)} / {texto(o.escalas_volta)}" if o.tem_volta else texto(o.escalas)


def _aviso_dados(provedor: ProvedorDePrecos) -> None:
    if not provedor.dados_reais:
        print("*** PROVEDOR FALSO: preços fictícios, apenas para teste/demonstração ***\n")


def tabela(ofertas: Sequence[Oferta], limite: int | None = None) -> str:
    por_pessoa = bool(ofertas) and ofertas[0].passageiros.total > 1
    cabecalho = ["#", "Preço total"] + (["Por pessoa"] if por_pessoa else []) + ["Companhia", "Ida", "Horário ida", "Volta",
                 "Horário volta", "Escalas ida/volta", "Duração ida", "Link de reserva"]
    linhas = []
    for i, o in enumerate(ofertas[:limite] if limite else ofertas, 1):
        horario_volta = (f"{o.partida_volta}→{o.chegada_volta}" if o.partida_volta
                         else "não informado" if o.volta_indisponivel else "—")
        linhas.append([
            str(i), formatar_brl(o.preco) + (f" (anunc. {formatar_brl(o.preco_anunciado)})"
                                             if o.preco_anunciado else "")]
            + ([formatar_brl(o.preco_por_pessoa)] if por_pessoa else []) + [o.companhia,
            o.data_ida.strftime("%d/%m/%Y"), f"{o.partida_ida}→{o.chegada_ida}",
            o.data_volta.strftime("%d/%m/%Y"), horario_volta,
            _escalas(o), _duracao(o.duracao_min), o.link,
        ])
    larguras = [max(len(c), *(len(l[j]) for l in linhas)) if linhas else len(c)
                for j, c in enumerate(cabecalho)]
    alinhar_direita = {0, 1, 2} if por_pessoa else {0, 1}

    def fmt(celulas: list[str]) -> str:
        partes = [c.rjust(w) if j in alinhar_direita else c.ljust(w)
                  for j, (c, w) in enumerate(zip(celulas, larguras))]
        partes[-1] = partes[-1].rstrip()
        return " | ".join(partes)

    saida = [fmt(cabecalho), "-+-".join("-" * w for w in larguras)]
    saida += [fmt(l) for l in linhas]
    return "\n".join(saida)


# ----------------------------------------------------------------- comandos


def cmd_buscar(args: argparse.Namespace) -> int:
    passageiros = Passageiros(args.adultos, args.criancas, args.bebes)
    historico = Historico(args.db)
    provedor = _criar_provedor(args, historico)
    _aviso_dados(provedor)
    pares = pares_de_datas(args.ida, args.volta, args.flex)
    total = min(len(pares), args.max_consultas) if args.max_consultas else len(pares)
    print(f"Buscando {args.origem} → {args.destino} | ida {args.ida} ± {args.flex} dia(s), "
          f"volta {args.volta} ± {args.flex} dia(s) | {passageiros.descricao} | "
          f"{total} combinação(ões) de datas via '{provedor.nome}'")
    resultado = buscar_flexivel(provedor, args.origem, args.destino, args.ida, args.volta,
                                args.flex, args.max_consultas, paralelo=args.paralelo,
                                passageiros=passageiros)
    # Grava os preços da busca antes de detalhar a volta (que pode ajustar alguns preços).
    gravados = historico.gravar(resultado.ofertas)
    n_voltas = 0
    if args.detalhar_volta and resultado.ofertas:
        resultado.ofertas, erros_volta = detalhar_voltas(provedor, resultado.ofertas,
                                                         args.detalhar_volta, args.paralelo)
        resultado.erros.extend(erros_volta)
        n_voltas = min(args.detalhar_volta, len(resultado.ofertas))
    historico.fechar()
    for erro in resultado.erros:
        print(f"  aviso: {erro}", file=sys.stderr)
    if not resultado.ofertas:
        print("Nenhuma oferta encontrada.")
        return 1
    print(f"{len(resultado.ofertas)} ofertas em {len(resultado.pares_consultados)} combinações; "
          f"preço total em BRL, ida e volta, para {passageiros.descricao}. "
          f"Ordenado do menor para o maior.\n")
    print(tabela(resultado.ofertas, args.limite))
    if n_voltas:
        print(f"Horários da volta buscados para as {n_voltas} ofertas mais baratas "
              f"(--detalhar-volta N para mudar).")
    if args.limite and len(resultado.ofertas) > args.limite:
        print(f"... mais {len(resultado.ofertas) - args.limite} ofertas (use --limite 0 para todas)")
    novas = getattr(provedor, "consultas_feitas", None)
    if novas is not None:
        print(f"\nConsultas novas à API: {novas} (as demais vieram do cache)")
    print(f"{gravados} preços gravados no histórico.")
    return 0


def cmd_historico(args: argparse.Namespace) -> int:
    historico = Historico(args.db)
    resumos = historico.resumo(args.origem, args.destino)
    historico.fechar()
    if not resumos:
        print("Histórico vazio. Rode `passagens buscar ...` primeiro.")
        return 1
    cab = ["Rota", "Passageiros", "Menor", "Maior", "Média", "Registros", "Primeira consulta",
           "Última consulta"]
    linhas = [[f"{r.origem}→{r.destino}", r.grupo.descricao, formatar_brl(r.menor), formatar_brl(r.maior),
               formatar_brl(r.media), str(r.registros), r.primeira.replace("T", " "),
               r.ultima.replace("T", " ")] for r in resumos]
    larg = [max(len(c), *(len(l[j]) for l in linhas)) for j, c in enumerate(cab)]
    print(" | ".join(c.ljust(w) for c, w in zip(cab, larg)))
    print("-+-".join("-" * w for w in larg))
    for l in linhas:
        print(" | ".join(c.ljust(w) for c, w in zip(l, larg)))
    return 0


def cmd_monitorar(args: argparse.Namespace) -> int:
    historico = Historico(args.db)
    # Sem --rotas: usa as rotas do banco (as mesmas da página Monitorar do site);
    # na primeira vez, importa as do rotas.yaml.
    rotas = carregar_rotas(args.rotas) if args.rotas else rotas_do_banco(historico, "rotas.yaml")
    provedor = _criar_provedor(args, historico)
    _aviso_dados(provedor)
    notificador = Notificador()
    relatorio = executar_monitoramento(historico, provedor, rotas, notificador, paralelo=args.paralelo,
                                       url_site=os.environ.get("PASSAGENS_URL_SITE", ""))
    historico.fechar()
    precos_novos = {(a.rota_rotulo, a.preco) for a in relatorio.novos_alertas}
    alertas = 0
    for r in relatorio.resultados:
        rota = r.rota
        for erro in r.erros:
            print(f"  aviso: {erro}", file=sys.stderr)
        if r.melhor is None:
            print(f"SEM DADOS {rota.rotulo}: nenhuma oferta encontrada")
            continue
        m = r.melhor
        alvo = formatar_brl(rota.preco_alvo) if rota.preco_alvo else "—"
        media_txt = formatar_brl(r.media_dos_menores) if r.media_dos_menores else "sem histórico"
        chegada = f" (chegada {m.aeroporto_destino})" if "," in rota.destino and m.aeroporto_destino else ""
        detalhe = (f"{formatar_brl(m.preco)} {m.companhia}{chegada} {m.data_ida:%d/%m}→{m.data_volta:%d/%m}"
                   f" | alvo {alvo} | média dos menores {media_txt}")
        if r.alerta:
            alertas += 1
            novo = " [NOVO]" if (rota.rotulo, m.preco) in precos_novos else " [já avisado por este preço ou menor]"
            print(f"ALERTA {rota.rotulo}: {detalhe} | " + "; ".join(r.motivos) + novo)
            print(f"       {m.link}")
        else:
            print(f"ok     {rota.rotulo}: {detalhe}")
    print(f"\n{len(relatorio.resultados)} rota(s) verificada(s), {alertas} alerta(s), "
          f"{len(relatorio.novos_alertas)} novo(s). E-mail: {relatorio.email} ({notificador.descricao()}).")
    return 0


def cmd_rotas(args: argparse.Namespace) -> int:
    historico = Historico(args.db)
    rotas = historico.listar_rotas()
    historico.fechar()
    if not rotas:
        print("Nenhuma rota no banco. Crie pela página Monitorar do site ou rode `passagens monitorar` "
              "(importa o rotas.yaml).")
        return 1
    for r in rotas:
        alvo = formatar_brl(r.preco_alvo) if r.preco_alvo else "sem alvo"
        print(f"#{r.id:<3} {'ativa ' if r.ativa else 'pausada'} {r.rotulo} | alvo {alvo}")
    return 0


# ----------------------------------------------------------------- argparse


def criar_parser() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--db", help="arquivo SQLite (padrão: $PASSAGENS_DB ou ~/.passagens/historico.db)")
    comum.add_argument("--provedor", choices=["serpapi", "falso"],
                       default=os.environ.get("PASSAGENS_PROVEDOR", "serpapi"),
                       help="fonte de preços (padrão: serpapi; 'falso' usa dados fixos)")
    comum.add_argument("--sem-cache", action="store_true", help="ignora o cache de 6 h da API")
    comum.add_argument("--paralelo", type=int, default=4,
                       help="consultas simultâneas à fonte (padrão: 4)")

    parser = argparse.ArgumentParser(prog="passagens", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    b = sub.add_parser("buscar", parents=[comum], help="busca e compara preços")
    b.add_argument("--origem", type=_iata, required=True)
    b.add_argument("--destino", type=_iata, required=True)
    b.add_argument("--ida", type=_data, required=True)
    b.add_argument("--volta", type=_data, required=True)
    b.add_argument("--flex", type=int, default=0, help="testa ida e volta ± N dias")
    b.add_argument("--adultos", type=int, default=1, help="adultos, 12 anos ou mais (padrão 1)")
    b.add_argument("--criancas", type=int, default=0, help="crianças de 2 a 11 anos (padrão 0)")
    b.add_argument("--bebes", type=int, default=0, help="bebês de colo, até 2 anos (padrão 0)")
    b.add_argument("--max-consultas", type=int, default=None,
                   help="teto de combinações de datas (economiza cota da API)")
    b.add_argument("--limite", type=int, default=30, help="linhas na tabela (0 = todas)")
    b.add_argument("--detalhar-volta", type=int, nargs="?", const=5, default=5, metavar="N",
                   help="busca os horários da volta das N ofertas mais baratas "
                        "(1 consulta extra por oferta; padrão 5; 0 desliga)")
    b.set_defaults(func=cmd_buscar)

    h = sub.add_parser("historico", parents=[comum], help="menor, maior e média por rota")
    h.add_argument("--origem", type=_iata)
    h.add_argument("--destino", type=_iata)
    h.set_defaults(func=cmd_historico)

    m = sub.add_parser("monitorar", parents=[comum],
                       help="verifica as rotas monitoradas e avisa por e-mail quando o preço cai")
    m.add_argument("--rotas", default=None,
                   help="usar este arquivo YAML em vez das rotas do banco (padrão: rotas do banco; "
                        "na primeira vez importa o rotas.yaml)")
    m.set_defaults(func=cmd_monitorar)

    r = sub.add_parser("rotas", parents=[comum], help="lista as rotas monitoradas do banco")
    r.set_defaults(func=cmd_rotas)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = criar_parser().parse_args(argv)
    if getattr(args, "limite", None) == 0:
        args.limite = None
    try:
        return args.func(args)
    except (ErroDeProvedor, ValueError, FileNotFoundError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
