# passagens

Ferramenta de linha de comando em Python para **buscar, comparar e monitorar
preços de passagens aéreas** de ida e volta, em BRL.

- `passagens buscar` – consulta preços reais (Google Flights via SerpApi), testa
  datas flexíveis (±N dias na ida e na volta) e imprime uma tabela do menor para
  o maior preço total, com companhia, datas, horários, escalas, duração e link.
- `passagens historico` – menor, maior e preço médio por rota, a partir do
  histórico em SQLite (cada preço consultado é gravado com data e hora).
- `passagens monitorar` – lê `rotas.yaml`, refaz as buscas e imprime **ALERTA**
  quando o menor preço fica abaixo do seu alvo ou cai mais de 15% abaixo da
  média histórica da rota.

A escolha da fonte de dados (e por que não Amadeus, Duffel ou Travelpayouts)
está em [DECISOES.md](DECISOES.md).

## Instalação

Requer Python 3.10+.

```bash
git clone https://github.com/marcospabreu-afk/Air-Tickets.git
cd Air-Tickets
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
passagens --help
```

## Configuração da chave de API

1. Crie uma conta gratuita em <https://serpapi.com/users/sign_up>
   (plano grátis: 250 buscas/mês, 50/hora, sem cartão).
2. Copie a chave em <https://serpapi.com/manage-api-key>.
3. Defina a variável de ambiente — **nunca** coloque a chave no código ou no git:

```bash
export SERPAPI_API_KEY="sua_chave"          # Linux/macOS (adicione ao ~/.bashrc para persistir)
setx SERPAPI_API_KEY "sua_chave"            # Windows (abra um terminal novo depois)
```

Há um `.env.example` como referência; o arquivo `.env` está no `.gitignore`.
Se quiser usá-lo: `set -a; source .env; set +a`.

Outras variáveis opcionais:

| Variável | Para quê | Padrão |
|---|---|---|
| `PASSAGENS_DB` | arquivo SQLite do histórico | `~/.passagens/historico.db` |
| `PASSAGENS_PROVEDOR` | `serpapi` ou `falso` | `serpapi` |

### Custo de cota

Cada combinação de datas é **uma busca** na SerpApi. `--flex 3` = 7 datas de ida
× 7 de volta = **49 buscas**. Para economizar:

- respostas ficam em cache por 6 h no SQLite (repetir o comando não gasta cota;
  `--sem-cache` força nova consulta);
- `--max-consultas N` limita o número de combinações (começa pelas mais próximas
  das datas pedidas);
- `--detalhar-volta` busca também os horários do voo de volta, mas gasta 1 busca
  extra por oferta — use com `--flex 0`.

## Exemplos de uso

```bash
# Busca com ±3 dias na ida e na volta (49 buscas)
passagens buscar --origem CWB --destino LIS --ida 2027-03-10 --volta 2027-03-24 --flex 3

# Mostrar todas as ofertas, não só as 30 primeiras
passagens buscar --origem GRU --destino MAD --ida 2027-05-02 --volta 2027-05-16 --limite 0

# Datas exatas, com horários da volta
passagens buscar --origem CWB --destino LIS --ida 2027-03-10 --volta 2027-03-24 --detalhar-volta

# Histórico (todas as rotas, ou filtrando)
passagens historico
passagens historico --origem CWB --destino LIS

# Monitorar as rotas do rotas.yaml
passagens monitorar
passagens monitorar --rotas /caminho/para/rotas.yaml

# Testar tudo sem chave e sem rede (dados fictícios e fixos)
passagens buscar --provedor falso --origem CWB --destino LIS --ida 2027-03-10 --volta 2027-03-24 --flex 3
```

Exemplo de saída (provedor falso):

```
 # | Preço total | Companhia | Ida        | Horário ida   | Volta      | Horário volta | Escalas | Duração ida | Link de reserva
---+-------------+-----------+------------+---------------+------------+---------------+---------+-------------+----------------
 1 | R$ 4.101,30 | Azul      | 10/03/2027 | 06:10→05:55+1 | 24/03/2027 | 10:20→05:40+1 | 2       | 17h45       | https://www.google.com/travel/flights?...
```

O preço é o **total ida e volta para 1 adulto**, em BRL. O link abre a busca no
Google Flights, onde aparecem as opções de reserva (companhia/agências).

### rotas.yaml

```yaml
rotas:
  - origem: CWB
    destino: LIS
    ida: 2027-03-10
    volta: 2027-03-24
    flex: 1            # 9 buscas por execução
    preco_alvo: 4500   # BRL, total ida e volta
```

Regras de alerta (`ALERTA` é impresso se qualquer uma valer para o menor preço
encontrado):

1. preço < `preco_alvo`;
2. preço mais de 15% abaixo da média histórica da rota (média calculada
   **antes** de gravar os preços da execução atual).

## Agendar o monitoramento diário

Com o plano gratuito, mantenha o total diário baixo: ex. 2 rotas com `flex: 1`
= 18 buscas/dia ≈ 540/mês — **acima** das 250 grátis. Uma rota com `flex: 0`
(30/mês) ou `flex: 1` a cada 2–3 dias cabe no limite.

### Linux / macOS (cron)

```bash
crontab -e
```

```cron
# todo dia às 08:17
17 8 * * * cd /caminho/Air-Tickets && SERPAPI_API_KEY=sua_chave .venv/bin/passagens monitorar >> ~/.passagens/monitor.log 2>&1
```

Para não deixar a chave no crontab, guarde-a em `~/.passagens/env` (com
`chmod 600`) e use
`set -a; . ~/.passagens/env; set +a; .venv/bin/passagens monitorar`.

Para receber os alertas por e-mail/notificação, filtre o log, ex.:
`... | grep ALERTA | mail -s "Passagens" voce@exemplo.com` ou
`notify-send "Passagens" "$(grep ALERTA ~/.passagens/monitor.log | tail -1)"`.

### Windows (Agendador de Tarefas)

```powershell
schtasks /Create /SC DAILY /ST 08:17 /TN "Monitor de passagens" ^
  /TR "cmd /c cd /d C:\caminho\Air-Tickets && .venv\Scripts\passagens.exe monitorar >> monitor.log 2>&1"
```

(a variável `SERPAPI_API_KEY` deve ter sido definida com `setx`).

## Testes

```bash
pytest -v
```

Os testes usam apenas o provedor falso e respostas simuladas da SerpApi — não
gastam cota nem precisam de rede. Cobrem ordenação, datas flexíveis, gravação e
resumo do histórico, regra de alerta, conversão da resposta da SerpApi e a CLI
ponta a ponta.

## Estrutura

```
src/passagens/
  provedores.py   # ProvedorDePrecos (interface), ProvedorSerpApi (real), ProvedorFalso
  busca.py        # combinações de datas flexíveis, ordenação
  historico.py    # SQLite: histórico de preços e cache de respostas
  alertas.py      # regra de alerta
  cli.py          # comandos buscar / historico / monitorar
tests/            # pytest
rotas.yaml        # rotas monitoradas
DECISOES.md       # comparação de fontes e decisões de arquitetura
```
