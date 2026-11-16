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
