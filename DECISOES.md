# Decisões de projeto

## 1. Fonte de preços (registrado em 16/11/2026, antes de escrever o código)

A API Self-Service da Amadeus foi descartada de saída: o portal foi desativado em
17/07/2026 e o acesso agora é só pelo canal Enterprise, com contrato comercial.

### Fontes comparadas

| Critério | **SerpApi – Google Flights** | **Duffel** | **Travelpayouts – Aviasales Data API** | Kiwi Tequila |
|---|---|---|---|---|
| Tipo de dado | Preços em tempo real exibidos pelo Google Flights | Ofertas em tempo real direto das companhias / NDC (API de reserva) | Preços **em cache** (2 a 7 dias) vindos de buscas de outros usuários | Busca em tempo real |
| Custo | Grátis até o limite; planos pagos a partir de ~US$ 25/mês (1.000 buscas) | Sem mensalidade no modo pago‑por‑uso; US$ 3 por pedido emitido + US$ 0,005 por busca acima da razão 1.500:1 busca/pedido | Grátis (programa de afiliados) | Fechado ao público desde 2024 (só parceiros B2B) |
| Limite gratuito | 250 buscas/mês, 50/hora | Modo de teste ilimitado, mas com **dados fictícios** ("Duffel Airways"); dados reais exigem conta ativada para venda | ~600 req/min em `/v3/prices_for_dates`, sem cota mensal conhecida | — |
| Cobertura saindo do Brasil | Muito boa: tudo o que o Google Flights mostra (LATAM, GOL, Azul, TAP, low-costs, combinações) ; aceita `currency=BRL`, `gl=br` | Boa em companhias NDC (LATAM, TAP, Iberia, AF/KLM…), mas **GOL e Azul não estão** no catálogo padrão | Depende do histórico de buscas no Aviasales; rotas como CWB→LIS em datas específicas de 2027 frequentemente não têm cache | — |
| Datas exatas + horários + escalas | Sim (horários, escalas, duração, companhia) | Sim | Parcial: cache por data, sem horário confiável | — |
| Link de reserva | URL do Google Flights da busca (com opções de reserva) | Não há link: a reserva é feita via API (exige ser agência/vendedor) | Link de afiliado Aviasales | — |
| Termos de uso | API oficial paga/gratuita; a SerpApi assume a coleta, uso por chave pessoal é permitido | Feita para agências que vendem passagens; não é para "só consultar preço" | Exige cadastro como afiliado e uso com finalidade de afiliado | — |

Fontes consultadas: páginas de preço e documentação agregadas em
scrapegraphai.com/blog/serpapi-pricing, costbench.com (SerpApi free plan),
geekflare.com/guides/flight-booking-api, xpay.sh (Duffel),
support.travelpayouts.com (Aviasales Data API, limites de API),
ignav.com/docs/kiwi-tequila-alternative. Os sites oficiais (serpapi.com,
duffel.com) estavam bloqueados pela rede do ambiente de desenvolvimento;
**confira os valores atuais antes de contratar qualquer plano.**

### Escolha: **SerpApi – Google Flights** como provedor real

Motivos:
1. É a única das três que entrega **preço real e atual + horários + escalas +
   duração + link** para uma pessoa física, sem precisar ser agência.
2. Cobertura completa de voos saindo de CWB (inclui GOL/Azul nos trechos domésticos
   de conexão), preços já em BRL.
3. Plano gratuito sem cartão de crédito, suficiente para uso pessoal moderado.

Custos e cuidados:
- `--flex 3` testa 7 × 7 = **49 combinações de datas = 49 buscas**. Com o plano
  gratuito (250/mês, 50/hora) isso cabe ~5 vezes por mês. Por isso:
  - o provedor real mantém um **cache em SQLite** (padrão 6 h) e não repete a mesma
    consulta nesse intervalo;
  - a CLI mostra quantas consultas fará e aceita `--max-consultas` como teto;
  - o monitoramento diário deve usar `flex` pequeno (0 ou 1) no `rotas.yaml`.
- O preço retornado em ida‑e‑volta já é o **total da viagem** (por adulto).
  Os horários do voo de volta exigiriam uma busca extra por oferta
  (`departure_token`), então só são buscados com `--detalhar-volta`.
- Nenhum plano pago foi contratado.

Descartadas: Duffel (sem dados reais sem ativar conta de vendedor, sem GOL/Azul,
sem link de reserva); Travelpayouts (cache incompleto e sem horários, ótima
como *fallback* futuro de baixo custo); Kiwi (fechada); Amadeus Self‑Service
(desativada).

## 2. Arquitetura

- `ProvedorDePrecos` (classe abstrata) com `buscar(origem, destino, ida, volta) -> list[Oferta]`.
  - `ProvedorSerpApi`: implementação real; chave lida de `SERPAPI_API_KEY`.
  - `ProvedorFalso`: dados fixos e determinísticos, usado nos testes e com `--provedor falso`.
- `historico.py`: SQLite (padrão `~/.passagens/historico.db`, ou `PASSAGENS_DB`).
- `alertas.py`: regra pura (abaixo do alvo **ou** > 15% abaixo da média histórica).
- A chave nunca está no código: `.env` está no `.gitignore` e a CLI falha com
  mensagem clara se a variável não existir.

## 3. Regra de alerta — referência da média (19/11/2026)

O primeiro teste com dados reais (CWB→LIS) mostrou que comparar o menor preço do
dia com a média de **todas** as ofertas gravadas (R$ 4.532 a R$ 24.087, média
~R$ 6.146) disparava "26% abaixo da média" sem nenhuma queda real. A regra passou a
usar a **média do menor preço de cada consulta** (`Historico.media_dos_menores`),
que mede de fato se o preço mais barato disponível caiu.

## 4. Validação com a fonte real (19/11/2026)

`passagens buscar --origem CWB --destino LIS --ida 2027-03-10 --volta 2027-03-24 --flex 3`
fez 49 consultas à SerpApi (1 + 48; a primeira já estava em cache), retornou 594
ofertas únicas e levou ~4 min 20 s. Menor preço: R$ 4.532 (LATAM, 08/03 → 22/03).
Isso consome ~20% da cota gratuita mensal — use `--flex` menor no dia a dia.

## 5. Interface web e hospedagem (19/11/2026)

- **Flask** + **gunicorn**, reaproveitando o mesmo pacote da CLI (a lógica de
  monitoramento foi movida para `monitor.py` para servir aos dois).
- As combinações de datas passaram a ser consultadas **em paralelo** (CLI: 4, web: 6),
  porque ±3 dias em sequência levava ~4 min — inviável numa página. Teste real:
  9 consultas em 9 s.
- **Senha opcional** (`PASSAGENS_SENHA`, HTTP Basic): o site gasta a cota da SerpApi
  de quem o publica, então não deve ficar aberto.
- **Hospedagem: Render, plano gratuito** — roda Python e acessa APIs externas sem
  lista de bloqueio. Custos/limites: dorme após 15 min sem uso (~1 min para
  acordar), disco efêmero (histórico se perde ao reiniciar). Descartados: GitHub
  Pages (só páginas estáticas, não roda Python nem guarda a chave);
  PythonAnywhere grátis (internet restrita a uma lista de sites; não confirmei a
  SerpApi nela). Nenhum plano pago foi contratado.

## 6. Seleção de aeroportos na interface web (20/11/2026)

- Campos de origem/destino com **sugestões enquanto se digita** (código, cidade,
  aeroporto ou país, sem depender de acentos), via `/api/aeroportos`.
- Base: **airportsdata** (MIT), 7.950 aeroportos com código IATA, embutida no pacote
  como JSON (~500 KB) para não depender de rede nem de dependência extra em produção.
  Nomes de países em português gerados uma vez com pycountry (só no script).
- A base não informa o porte do aeroporto: uma lista curta de **aeroportos
  principais** e **apelidos em português** (ex.: "londres" → LHR, LGW…) define a
  ordem das sugestões, para que CWB venha antes de Bacacheri e LHR antes de LCY.
- Não foi usada uma lista `<datalist>` com todos os aeroportos na página (pesaria
  ~500 KB em cada acesso e não permite ordenar por relevância).

## 7. Horário da volta e filtros (20/11/2026)

- A SerpApi só devolve a volta numa consulta extra por oferta (`departure_token`).
  Buscar para todas as ofertas de um ±3 dias (~600) esgotaria a cota grátis, então:
  automático para as N mais baratas (padrão 5) + botão "ver volta" por linha.
- A volta escolhida é a opção com o **mesmo preço total** anunciado (é a combinação
  que o Google Flights mostra). Teste real: às vezes ela não existe — a oferta de
  R$ 5.109 só tinha volta a R$ 5.513, e outra de R$ 6.251 tinha a R$ 5.921. Nesses
  casos o preço é atualizado e o anunciado fica visível; quando a fonte não traz
  nenhuma volta, a linha mostra "não informado" (e não consulta de novo).
- Filtros feitos no navegador, sobre todas as ofertas já carregadas: instantâneos e
  sem custo de cota. O filtro de escalas usa a ida, que é conhecida para todas.

## 8. Vários passageiros e crianças (20/11/2026)

- Teste real na SerpApi (CWB→LIS): o preço devolvido é o **total do grupo**. 2 adultos =
  exatamente 2×; 1 adulto + 1 criança = 2× na LATAM (criança paga cheio) e 1,78× numa
  combinação com Lufthansa; bebê de colo = +9% a +15%.
- Como o desconto de criança varia por companhia, **não** foi usada uma fórmula fixa
  (ex.: criança = 75%): o sistema pede o preço do grupo à fonte (`adults`, `children`,
  `infants_on_lap`) e mostra o total e a média por pessoa. A fórmula fixa existe só no
  provedor falso, para testes (criança 75%, bebê 10%).
- Bebê com assento próprio não foi incluído (raro; pode ser somado depois via
  `infants_in_seat`).
- O histórico ganhou a coluna `passageiros` ('1A', '2A1C', '2A1C1B'); bancos antigos são
  migrados automaticamente (registros existentes = 1 adulto). Resumo, média e alerta
  são calculados por composição de grupo.
- Para não invalidar o cache, `children`/`infants_on_lap` só são enviados quando > 0.
