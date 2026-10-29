# Resumo de Mercado — notícias, radar e chat no Telegram, 100% de graça

Todo dia útil de manhã, esse projeto manda pro Telegram:

1. **📊 Radar do Mercado** — maiores altas e baixas do último pregão entre as
   ações e FIIs mais líquidos da B3, índices internacionais (S&P 500, Nasdaq,
   Dow, Euro Stoxx, Nikkei), Bitcoin/Ethereum e os últimos proventos pagos
   pelos ativos de bolsa da carteira.
2. **Digest de notícias em 6 categorias** — Renda Fixa, Renda Variável/Ações,
   Criptomoedas, Debêntures e Crédito Privado, Mercado Internacional e
   Mercado Imobiliário. Cada categoria vira uma mensagem com análise de
   impacto (mecanismo causal, implicação por setor/classe, risco da tese e
   próximo evento a acompanhar), não só um resumo do que aconteceu.

E durante o dia dá pra **conversar com o bot**: qualquer mensagem no Telegram
é respondida em ~2–5 minutos, com base no digest do dia, em indicadores
oficiais do Banco Central e na carteira descrita em `carteira.json` — então
"isso afeta minha debênture IPCA+?" recebe resposta sobre a SUA posição, não
sobre o mercado em abstrato.

Custo total: **R$ 0**, sem nenhum servidor ligado.

## Arquitetura — quem faz o quê

| Arquivo | Papel |
|---|---|
| `market_digest.py` | A rodada da manhã: busca notícias (RSS + sites sem RSS), triagem via Groq, análise profunda das principais + menção rápida das demais, envia as mensagens e salva `ultimo_digest.json` |
| `radar_mercado.py` | Monta a mensagem do Radar (rankings B3 via brapi, mundo/cripto via yfinance, proventos da carteira). Cada seção falha isolada — se uma fonte cair, as outras saem mesmo assim |
| `chat_bot.py` | Checa mensagens novas no Telegram e responde usando o digest do dia + indicadores do BCB + carteira com cotação real |
| `carteira.json` | Sua carteira (só **percentuais**, nunca valores em R$ — o repositório é público) |
| `ultimo_digest.json` | Gerado a cada rodada e commitado de volta — é a memória que o chat consulta (inclui o texto do radar) |
| `noticias_enviadas.json` | Memória de dedup: links e títulos já enviados nos últimos 7 dias, pra mesma notícia não repetir em digests seguidos |
| `.github/workflows/main.yml` | Workflow do digest (roda o `market_digest.py` e commita os JSONs) |
| `.github/workflows/chat.yml` | Workflow do chat (roda o `chat_bot.py`) |

### O disparo de verdade vem do cron-job.org, não do GitHub

O agendamento nativo do GitHub Actions (`schedule:`) atrasa — o próprio
GitHub documenta que em horário de pico os jobs agendados podem demorar (já
vimos 3 horas de atraso no chat). Por isso o disparo real é feito pelo
[cron-job.org](https://cron-job.org) (gratuito), chamando a API do GitHub:

- **URL**: `https://api.github.com/repos/<SEU_USUARIO>/<SEU_REPO>/actions/workflows/main.yml/dispatches`
  (e outro job igual pra `chat.yml`, a cada ~2 min)
- **Método**: POST
- **Headers**: `Authorization: Bearer <SEU_TOKEN>`,
  `Accept: application/vnd.github+json`, `Content-Type: application/json`
- **Body**: `{"ref":"main"}`
- O token é um **fine-grained personal access token** do GitHub com permissão
  `Actions: write` só nesse repositório.

Os `schedule:` dos workflows continuam lá como backup (o do digest de
propósito às xx:07, fora da hora cheia, que é quando a fila congestiona).

**O repositório precisa ser público** pra ter minutos ilimitados de Actions —
com o chat rodando a cada poucos minutos, um repositório privado estouraria o
limite gratuito de 2.000 min/mês. É por isso que nada sensível entra em
arquivo: chaves ficam em Secrets e a carteira só tem percentuais.

## A pilha 100% gratuita

1. **RSS + extração local** — os feeds das fontes (InfoMoney, Money Times,
   Cointelegraph, Investing.com, Seeking Alpha, Agência Brasil, Fed de St.
   Louis…) são gratuitos. Pra sites sem RSS (Bora Investir/B3, Clube FII,
   Livecoins, IBGE…), o script descobre os links pelo sitemap com
   `trafilatura` e extrai a data de cada artigo (notícia sem data ou fora da
   janela de 36h é descartada). O texto COMPLETO das notícias escolhidas é
   baixado e extraído localmente — sem API, sem custo.
2. **Groq na camada gratuita** — modelos open-source rápidos, sem cartão de
   crédito. ~8 chamadas por rodada do digest + 1 por mensagem de chat, muito
   abaixo do limite gratuito.
3. **Telegram Bot API, GitHub Actions e cron-job.org** — gratuitos.
4. **brapi.dev** (plano gratuito, 15k req/mês) — cotações da B3: as posições
   da carteira no chat e os rankings de altas/baixas do Radar.
5. **APIs do Banco Central** (públicas, sem chave) — SGS pra Dólar PTAX e
   Meta Selic (ancoram as respostas do chat em número oficial) e Olinda pro
   Boletim Focus (projeções Selic/IPCA/câmbio/PIB como contexto do digest).
6. **yfinance** (biblioteca, sem chave) — índices internacionais, cripto e
   histórico de proventos no Radar. É fonte NÃO oficial (lê o Yahoo Finance)
   e às vezes limita requisições vindas de datacenter; por isso tudo que
   depende dela degrada graciosamente.

### Modelos da Groq: lista com fallback automático

A Groq já descontinuou **três** modelos que usávamos, um atrás do outro
(`openai/gpt-oss-120b` por bug de JSON, `moonshotai/kimi-k2-instruct` e
`qwen/qwen3.6-27b` por descontinuação) — e cada vez o bot ficou mudo até
alguém perceber (a última custou 7 dias de silêncio). Por isso o modelo não é
mais um nome fixo: `GROQ_MODELS` é uma **lista ordenada de candidatos**
(`qwen/qwen3.8-27b` → `llama-3.3-70b-versatile` → `openai/gpt-oss-120b`), e
se um deixar de existir o código pula sozinho pro próximo. Se um dia TODOS
falharem, confira os ativos em <https://console.groq.com/docs/models> e
atualize a lista nos dois arquivos (`market_digest.py` e `chat_bot.py`).

### Medidas anti-alucinação (aprendidas com erros reais)

- **Data de hoje em todo prompt** — sem isso, o modelo tratava notícia de
  2026 como "futuro hipotético", porque o treinamento dele tem corte anterior.
- **Proibido afirmar direção de indicador** (juros americanos, petróleo,
  dólar, Wall Street) que não esteja explícita no material — o modelo já
  inverteu a direção dos Treasuries e do petróleo completando com o padrão
  "clássico" que aprendeu. Agora, se o dado não está no material, a resposta
  diz que não tem o dado.
- **Números oficiais na frente do modelo** — BCB (SGS/Focus) e cotações
  reais da brapi entram no prompt pra ele não "lembrar" números.
- **Menções rasas presas ao resumo original** — notícia sem análise profunda
  vira UMA frase baseada só no texto da fonte, nunca completada com suposição.
- **`json_repair` + sem `response_format`** — o JSON do modelo é extraído e
  reparado manualmente (o modo "JSON garantido" da Groq tem bug documentado
  nos modelos gpt-oss).
- **Honestidade sobre horário no Radar** — às 8h a B3 não abriu; a mensagem
  diz explicitamente que as variações são do fechamento do último pregão.

### Outras proteções

- **Mensagens longas são divididas** em partes no limite de 4096 caracteres
  do Telegram, quebrando em fronteira de parágrafo (nunca no meio de uma
  linha/tag HTML), com marcador "(continuação N/M)".
- **Dedup de notícias**: tudo que entra num digest fica registrado por link
  E título normalizado em `noticias_enviadas.json` (retenção de 7 dias) — a
  janela de 36h continua pegando notícia de fim de tarde sem repetir a de
  ontem.
- **Timeout em tudo** (feeds, APIs, Telegram) e `timeout-minutes` nos
  workflows — nada trava pra sempre.

## Configuração do zero

### 1. Bot do Telegram
No Telegram, fale com **@BotFather** → `/newbot` → guarde o token.

### 2. Chat ID
Mande "oi" pro seu bot e abra
`https://api.telegram.org/bot<TOKEN>/getUpdates` no navegador — o número em
`"chat":{"id":...}` é o seu chat ID. (Grupo tem ID negativo `-100...`; pra
separar categorias por grupo, monte o JSON opcional do secret
`TELEGRAM_CATEGORY_CHAT_IDS` mapeando nome da categoria → chat_id.)

### 3. Chaves
- **Groq**: [console.groq.com](https://console.groq.com) → API Keys (sem cartão).
- **brapi**: [brapi.dev](https://brapi.dev) → cadastro gratuito → token.

### 4. Secrets no GitHub
**Settings → Secrets and variables → Actions**. Os nomes abaixo são os que
os workflows deste repositório usam (⚠️ são nomes customizados — o `env:` dos
workflows faz o mapeamento pros nomes que os scripts esperam):

| Secret | O que é |
|---|---|
| `GROQ_API` | chave da Groq |
| `API_BOT_TELEGRAM` | token do bot (@BotFather) |
| `TELEGRAM_ID` | chat ID padrão (só o número) |
| `BRAPI_TOKEN` | token da brapi (carteira no chat + rankings do Radar) |
| `TELEGRAM_CATEGORY_CHAT_IDS` | opcional, JSON categoria → chat_id de grupo |

### 5. cron-job.org
Crie dois cronjobs com a configuração da seção de arquitetura acima: um pro
`main.yml` (dias úteis de manhã) e um pro `chat.yml` (a cada ~2 min).

### 6. Carteira
Edite `carteira.json`: `posicoes_b3` (ativos com ticker — ganham cotação
real), `outras_posicoes` (LCA, debênture, fundo — sem cotação pública, mas o
bot sabe que existem e conecta notícia relevante a eles, ex: IPCA → debênture
IPCA+), `acompanhando` e `perfil`. **Só percentuais, nunca valores em R$** —
o repositório é público.

### 7. Testar
Aba **Actions** → "Resumo de Mercado" → **Run workflow**. Radar + categorias
devem chegar no Telegram em 1–3 minutos. Pra testar local: copie suas chaves
pra um `.env` (NUNCA commitá-lo) e rode `pip install -r requirements.txt &&
python3 market_digest.py`.

## Ajustes comuns

- **Fontes**: `RSS_FEEDS` (feeds confirmados) e `HTML_SOURCES` (página de
  listagem de sites sem RSS — o sitemap faz o resto) em `market_digest.py`.
  Antes de adicionar um feed, confirme que funciona:
  ```bash
  python3 -c "import feedparser; f=feedparser.parse('URL'); print(len(f.entries), 'itens')"
  ```
- **Categorias**: lista `CATEGORIES` — o `hint` é o que orienta a triagem.
- **Profundidade**: `MAX_ITENS_PROFUNDOS_POR_CATEGORIA` (análise completa) e
  `MAX_ITENS_RASOS_POR_CATEGORIA` (menção de uma frase).
- **Radar**: `TOP_N`, `UNIVERSO_ACOES`/`UNIVERSO_FIIS` (filtro de liquidez —
  sem ele o ranking vira micro-cap ilíquida que "subiu 40%" com três
  negócios) e as listas `INDICES_MUNDO`/`CRIPTO` em `radar_mercado.py`.
- **Horários**: nos cronjobs do cron-job.org (e nos `cron:` de backup dos
  workflows, sempre em UTC).

## Problemas conhecidos

- **Boletim Focus retornando 400** — a consulta OData na API Olinda do BCB
  parou de aceitar nosso filtro; o digest segue sem esse contexto (degrada
  graciosamente). Pendente de investigação.
- **Fontes intermitentes** — livecoins.com.br e guiadobitcoin.com.br às vezes
  dão timeout/526, e os sitemaps do Bora Investir/IBGE nem sempre trazem
  conteúdo recente. Não derrubam a rodada, só reduzem o pool do dia.
- **yfinance** pode ser limitado pelo Yahoo em IPs de datacenter — as seções
  do Radar que dependem dele somem nesses dias e voltam sozinhas.