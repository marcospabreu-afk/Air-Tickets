# passagens

Ferramenta de linha de comando em Python para **buscar, comparar e monitorar
preços de passagens aéreas** de ida e volta, em BRL.

- `passagens buscar` – consulta preços reais (Google Flights via SerpApi), testa
  datas flexíveis (±N dias na ida e na volta) e imprime uma tabela do menor para
  o maior preço total, com companhia, datas, horários, escalas, duração e link.
- `passagens historico` – menor, maior e preço médio por rota, a partir do
  histórico em SQLite (cada preço consultado é gravado com data e hora).
- `passagens monitorar` – verifica as rotas monitoradas (as mesmas que você cria e
  edita na página **Monitorar** do site; na primeira vez importa o `rotas.yaml`),
  imprime **ALERTA** quando o menor preço fica abaixo do seu alvo ou cai mais de
  15% abaixo da média histórica da rota, e avisa **por e-mail** quando o preço cai.

A escolha da fonte de dados (e por que não Amadeus, Duffel ou Travelpayouts)
está em [DECISOES.md](DECISOES.md).

## Interface web

Além da linha de comando, há um site com as mesmas funções: **Buscar**,
**Histórico** e **Monitorar**.

```bash
pip install -e '.[web]'
export SERPAPI_API_KEY="sua_chave"
export PASSAGENS_SENHA="uma_senha"     # opcional, mas recomendado se o site for público
flask --app 'passagens.web:criar_app()' run
# abra http://127.0.0.1:5000  (usuário: qualquer; senha: a de PASSAGENS_SENHA)
```

Nos campos **Origem** e **Destino** não é preciso saber o código IATA: digite a
cidade, o nome do aeroporto, o país ou o código ("lisboa", "sao paulo", "portugal",
"GRU") e escolha na lista. A base tem ~7.950 aeroportos do mundo inteiro, com
nomes de países em português e apelidos em português das cidades mais buscadas
(Londres, Nova York, Munique…). Se você digitar só a cidade e buscar, o site usa o
aeroporto principal dela.

Na página de resultados:

- **Horário da volta**: buscado automaticamente para as 5 ofertas mais baratas
  (opção "Horário da volta": nenhuma, 5, 10 ou 20 — cada uma custa 1 consulta extra);
  nas demais, o botão **ver volta** busca só aquela linha. Quando a combinação real
  com a volta custa diferente do preço anunciado na lista, o novo preço aparece com
  "anunciado R$ …" embaixo; quando a fonte não informa a volta, aparece "não informado".
- **Filtros** (instantâneos, sem gastar consulta): ordenar por preço, duração ou
  horário; escalas na ida; preço máximo; duração máxima da ida; data de ida; data de
  volta; período da partida (madrugada, manhã, tarde, noite) e companhias. No
  celular o painel começa recolhido.

As consultas de datas flexíveis são feitas em paralelo (6 por vez): ±1 dia leva
~10 s e ±3 dias ~1 min.

### Monitorar rotas e receber alertas (página Monitorar)

Na página **Monitorar** você:

- **cria, edita, pausa e exclui rotas**: origem, destino, datas, flexibilidade,
  preço-alvo (total do grupo) e passageiros;
- monitora uma **região**: o destino aceita vários aeroportos. Digite um país
  ("portugal") e escolha *Região* para adicionar os principais aeroportos dele de
  uma vez (dá para remover os que não quiser). Vários destinos custam **1 consulta
  só**, e o alerta informa o aeroporto de chegada;
- vê os **alertas recebidos** e roda **Verificar agora**.

Quando o aviso é enviado: o alerta vale se o menor preço ficar abaixo do alvo ou
mais de 15% abaixo da média histórica; o **e-mail** sai só quando esse preço é
**menor que o do último alerta** da rota (editar a rota reinicia esse controle) —
assim você é avisado quando o preço cai, e não todo dia pelo mesmo valor. Sem
e-mail configurado, os alertas ficam só na página.

### Publicar na internet (Render + Neon, grátis)

O Render grátis apaga o disco quando o site "dorme". Para as rotas, os alertas e o
histórico não sumirem, eles ficam num **Postgres gratuito do Neon**.

1. **Banco (Neon):** crie uma conta em <https://neon.com> (plano Free: 1 GB por
   projeto, sem cartão — confira na hora), crie um projeto e copie a *connection
   string* (`postgresql://...?sslmode=require`).
2. **E-mail (Resend):** o Render grátis bloqueia SMTP (portas 25/465/587), então o
   envio é por HTTPS pelo [Resend](https://resend.com) (grátis: 100 e-mails/dia).
   Crie a conta **com o e-mail que vai receber os alertas** e gere uma API key.
   Sem domínio próprio, o remetente `onboarding@resend.dev` só entrega para o
   e-mail da sua conta Resend — é o suficiente para alertas pessoais.
3. **Site (Render):** crie uma conta em <https://render.com>, **New → Blueprint**,
   escolha o repositório `Air-Tickets` e preencha quando ele pedir:
   | Variável | Valor |
   |---|---|
   | `SERPAPI_API_KEY` | sua chave da SerpApi |
   | `PASSAGENS_SENHA` | senha para abrir o site (sem ela, qualquer um com o link gasta sua cota) |
   | `DATABASE_URL` | a connection string do Neon |
   | `RESEND_API_KEY` | a API key do Resend |
   | `PASSAGENS_EMAIL_PARA` | o e-mail da sua conta Resend |
   | `PASSAGENS_URL_SITE` | o endereço do site (preencha depois do 1º deploy) |

   `PASSAGENS_TOKEN_MONITOR` é gerado automaticamente pelo Render.
4. **Verificação diária (GitHub Actions):** o arquivo
   `.github/workflows/monitorar.yml` chama o site todo dia às 07:17 (Brasília). No
   GitHub: *Settings → Secrets and variables → Actions → New repository secret*:
   - `PASSAGENS_URL_SITE` = endereço do site no Render;
   - `PASSAGENS_TOKEN_MONITOR` = copie o valor gerado no Render (*Environment*).

   Teste em *Actions → Monitorar passagens → Run workflow*. O agendamento usa o
   token, então funciona mesmo com a senha do site ativada.

Limitações do plano gratuito:
- o site "dorme" após 15 min sem acesso; o primeiro acesso (ou a verificação
  diária) leva ~1 min para acordá-lo;
- cota da SerpApi: cada verificação gasta (2·flex+1)² consultas por rota ativa — a
  página Monitorar mostra o total por verificação e por mês (plano grátis: 250/mês);
- sem `DATABASE_URL`, o site usa SQLite em `/tmp` e perde rotas e histórico ao reiniciar.

O endereço `marcospabreu-afk.github.io/Air-Tickets` (GitHub Pages) mostra apenas
este README: o GitHub Pages não roda Python, então não serve para o site.

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
- `--detalhar-volta N` busca os horários do voo de volta das N ofertas mais baratas
  (padrão 5), gastando 1 busca extra por oferta; `--detalhar-volta 0` desliga.

## Exemplos de uso

```bash
# Busca com ±3 dias na ida e na volta (49 buscas)
passagens buscar --origem CWB --destino LIS --ida 2027-03-10 --volta 2027-03-24 --flex 3

# Mostrar todas as ofertas, não só as 30 primeiras
passagens buscar --origem GRU --destino MAD --ida 2027-05-02 --volta 2027-05-16 --limite 0

# Horários da volta das 10 ofertas mais baratas (padrão: 5; 0 desliga)
passagens buscar --origem CWB --destino LIS --ida 2027-03-10 --volta 2027-03-24 --detalhar-volta 10

# Família: 2 adultos, 1 criança (2–11 anos) e 1 bebê de colo (até 2 anos)
passagens buscar --origem CWB --destino LIS --ida 2027-03-10 --volta 2027-03-24 \
  --adultos 2 --criancas 1 --bebes 1

# Histórico (todas as rotas, ou filtrando)
passagens historico
passagens historico --origem CWB --destino LIS

# Monitorar as rotas do banco (na 1ª vez importa o rotas.yaml) e avisar por e-mail
passagens monitorar
passagens rotas                                   # lista as rotas monitoradas
passagens monitorar --rotas /caminho/para/rotas.yaml   # usar um arquivo em vez do banco

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

### Mais de um passageiro (crianças e bebês)

Informe `--adultos`, `--criancas` (2 a 11 anos) e `--bebes` (de colo, até 2 anos) na
linha de comando, ou os campos **Passageiros** no site. O preço mostrado passa a ser o
**total do grupo**, com uma coluna **Por pessoa** (total ÷ número de passageiros).

Não há uma fórmula fixa para criança: o total é calculado pela própria companhia e
devolvido pela fonte. Em testes reais (CWB→LIS, mar/2027):

| Grupo | Mesmo voo | Em relação a 1 adulto |
|---|---|---|
| 2 adultos | R$ 9.290 | 2,0× |
| 1 adulto + 1 criança (LATAM) | R$ 9.290 | 2,0× — criança pagou tarifa cheia |
| 1 adulto + 1 criança (outra cia.) | R$ 11.315 | 1,78× — criança com ~22% de desconto |
| 1 adulto + 1 bebê de colo | R$ 5.323 | ~1,1× — bebê paga ~9–15% |

Regras: de 1 a 9 adultos, no máximo 9 passageiros e 1 bebê de colo por adulto.
O histórico e o alerta de queda são calculados **separadamente para cada composição
de grupo** (o total de uma família não é comparado com o de 1 adulto).

### rotas.yaml

As rotas monitoradas ficam no banco e são editadas pela página **Monitorar** do
site. O `rotas.yaml` serve como ponto de partida: é importado uma única vez, quando
o banco ainda não tem rotas (ou é usado diretamente com `--rotas`).

```yaml
rotas:
  - nome: Portugal     # opcional
    origem: CWB
    destino: LIS,OPO,FAO   # um ou vários aeroportos (região)
    ida: 2027-03-10
    volta: 2027-03-24
    flex: 1            # 9 buscas por execução
    preco_alvo: 4500   # BRL, total ida e volta (do grupo todo)
    # opcionais (padrão: 1 adulto)
    adultos: 2
    criancas: 1        # 2 a 11 anos
    bebes: 0           # de colo, até 2 anos
```

Regras de alerta (`ALERTA` é impresso se qualquer uma valer para o menor preço
encontrado):

1. preço < `preco_alvo`;
2. preço mais de 15% abaixo da média histórica da rota. A referência é a
   **média do menor preço de cada consulta anterior** (calculada antes de gravar a
   execução atual), e não a média de todas as ofertas: esta inclui executiva e
   itinerários longos e faria o menor preço parecer "barato" sempre.

`passagens historico` mostra o menor, o maior e a média de **todos** os preços
gravados por rota. Ofertas repetidas pela fonte (mesmo voo, horário e preço) são
descartadas antes de ordenar e gravar.

## Agendar o monitoramento diário

Publicado no Render, use o GitHub Actions (seção *Publicar na internet*). No seu
computador, use o agendador do sistema, como abaixo. Para receber e-mail rodando
localmente, dá para usar o Gmail (com [senha de app](https://myaccount.google.com/apppasswords)):

```bash
export PASSAGENS_EMAIL_PARA="voce@gmail.com"
export SMTP_USUARIO="voce@gmail.com"
export SMTP_SENHA="senha-de-app-de-16-letras"   # nunca no código nem no git
```

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

Com as variáveis de e-mail definidas (acima), o próprio `passagens monitorar` envia
o aviso quando o preço cai.

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
resumo do histórico, regra de alerta e de aviso, rotas e alertas no banco, e-mail
(Resend simulado e SMTP num servidor local), o site e a CLI ponta a ponta.

Para rodar também contra um Postgres real:

```bash
PASSAGENS_TESTE_PG=postgresql://usuario@localhost/banco_de_teste pytest
```

## Base de aeroportos

`src/passagens/dados/aeroportos.json` é gerado a partir do pacote
[airportsdata](https://github.com/mborsetti/airportsdata) (licença MIT), com os
nomes dos países traduzidos pelo [pycountry](https://github.com/pycountry/pycountry).
Para atualizar:

```bash
pip install airportsdata pycountry
python scripts/gerar_aeroportos.py
```

Os apelidos em português e a lista de aeroportos principais (que aparecem primeiro
nas sugestões) ficam em `src/passagens/aeroportos.py`.

## Estrutura

```
src/passagens/
  provedores.py   # ProvedorDePrecos (interface), ProvedorSerpApi (real), ProvedorFalso
  busca.py        # combinações de datas flexíveis, ordenação
  alertas.py      # regra de alerta
  cli.py          # comandos buscar / historico / monitorar
  monitor.py      # verificação das rotas, regra de aviso e execução do monitoramento
  notificacao.py  # e-mail dos alertas (Resend por HTTPS ou SMTP)
  historico.py    # banco (SQLite ou Postgres): preços, cache, rotas, alertas
  web.py          # interface web (Flask) + templates/
  aeroportos.py   # busca de aeroportos por código, cidade, nome ou país
  dados/          # aeroportos.json
tests/            # pytest
rotas.yaml        # rotas monitoradas
DECISOES.md       # comparação de fontes e decisões de arquitetura
```
