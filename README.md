# Resumo de Mercado — digest diário no Telegram, de graça

Todo dia útil, esse bot busca notícias de 6 categorias (renda fixa, ações,
cripto, debêntures, exterior, imobiliário), resume com impacto no mercado, e
manda pro seu Telegram — uma mensagem por categoria, sem custar nada.

## Como fica 100% gratuito

Trocamos a peça que custava dinheiro (API paga da Anthropic com busca na
web) por peças gratuitas:

1. **RSS em vez de busca na web** — os feeds RSS das próprias fontes de
   notícia (InfoMoney, Money Times, Cointelegraph, e outras) são gratuitos e
   sem limite, sempre foram. Não precisa de chave nem cadastro.
2. **Texto completo do artigo, extraído localmente** — em vez de se
   contentar com o resuminho de 1-2 frases que o RSS traz, o script baixa a
   página de cada notícia selecionada e extrai o texto completo com a
   biblioteca `trafilatura` (roda no seu próprio script, sem API, sem
   custo). Isso dá ao resumo final muito mais conteúdo pra trabalhar.
3. **Gemini (Google) na camada gratuita** em vez da API paga da Anthropic —
   o Google AI Studio dá acesso de graça, sem cartão de crédito, aos modelos
   Flash e Flash-Lite. O script usa 2 etapas: uma chamada pra triagem (decide
   quais notícias do lote servem pra cada categoria) e uma chamada por
   categoria pra síntese (resume o texto completo das notícias escolhidas).
   No total, 7 chamadas por dia — bem dentro de qualquer limite gratuito
   atual do Gemini.
4. **Telegram Bot API e GitHub Actions** — já eram gratuitos, sem mudança.

**Sendo honesto sobre o único risco:** a camada gratuita do Gemini já teve
os limites reduzidos pelo Google mais de uma vez em 2025/2026. No volume que
esse script usa (6 chamadas por dia), está bem dentro de qualquer limite
gratuito atual — mas se um dia o script começar a falhar com erro de "limite
excedido", é sinal de que o Google apertou de novo, e vale checar
[ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)
pra ver o estado atual.

## Passo a passo

### 1. Criar o bot no Telegram

1. Abra o Telegram e procure por **@BotFather**.
2. Envie `/newbot` e siga as instruções (nome e username do bot).
3. O BotFather te dá um **token** — algo como `123456789:ABCdefGhIJKlmNoPQRstuVwxyZ`.
   Guarde esse valor, é o seu `TELEGRAM_BOT_TOKEN`.

### 2. Pegar seu chat_id (chat padrão / fallback)

1. No Telegram, procure o bot que você criou e envie qualquer mensagem pra
   ele (ex: "oi").
2. No navegador, acesse (trocando `<TOKEN>` pelo seu token):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Procure por `"chat":{"id":` — o número ali é o seu `TELEGRAM_CHAT_ID`.

Categoria sem grupo próprio configurado cai aqui.

### 2b. (Opcional) Criar um grupo por categoria

1. Crie o grupo no Telegram (ex: "Renda Fixa - Digest").
2. Adicione o bot como membro do grupo, normalmente.
3. Mande qualquer mensagem no grupo.
4. Acesse de novo o `getUpdates` do passo 2 — vai aparecer uma entrada com
   `"chat":{"id": -100xxxxxxxxxx, ...}` (grupo tem id negativo, é normal).
5. Repita pra cada categoria que quiser separar, e junte tudo num JSON:

   ```json
   {
     "Renda Fixa": "-1001111111111",
     "Renda Variável / Ações": "-1002222222222",
     "Criptomoedas": "-1003333333333",
     "Debêntures e Crédito Privado": "-1004444444444",
     "Mercado Internacional": "-1005555555555",
     "Mercado Imobiliário": "-1006666666666"
   }
   ```

   Categoria que não estiver no JSON cai no `TELEGRAM_CHAT_ID` padrão — não
   precisa preencher tudo de uma vez.

### 3. Pegar sua chave gratuita do Gemini

1. Entre em [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
   com uma conta Google.
2. Clique em **Create API key** — não pede cartão de crédito.
3. Guarde esse valor — é o seu `GEMINI_API_KEY`.

### 4. Subir esse projeto pro GitHub

Crie um repositório (pode ser privado) e suba estes 4 arquivos mantendo a
mesma estrutura de pastas:

```
market_digest.py
requirements.txt
README.md
.github/workflows/market-digest.yml
```

### 5. Configurar os secrets

No repositório: **Settings → Secrets and variables → Actions → New
repository secret**.

| Nome | Valor |
|---|---|
| `GEMINI_API_KEY` | a chave do passo 3 |
| `TELEGRAM_BOT_TOKEN` | o token do passo 1 |
| `TELEGRAM_CHAT_ID` | o número do passo 2 |
| `TELEGRAM_CATEGORY_CHAT_IDS` | o JSON do passo 2b — opcional |

### 6. Testar

Aba **Actions** → workflow "Resumo de Mercado" → **Run workflow**. Se
estiver tudo certo, as mensagens chegam no Telegram em menos de um minuto.
Depois disso, roda sozinho todo dia útil às 8h de Brasília — não precisa
fazer mais nada.

## Ajustes que você provavelmente vai querer fazer

- **Sites sem RSS**: a lista `HTML_SOURCES` resolve o problema que apareceu
  com o Bora Investir (a URL do "feed" baixava HTML, não XML — ou seja, o
  site não expõe RSS de verdade). Em vez de precisar achar o link certo do
  feed, o script usa duas técnicas do `trafilatura` pra descobrir notícias
  recentes direto de uma página de listagem comum:
  1. **Sitemap** — quase todo site tem um `sitemap.xml` (índice de páginas
     pro Google indexar), mesmo sem ter RSS. O script procura esse sitemap
     sozinho e tira os links de lá.
  2. **Lista de links** — se não achar sitemap, trata a própria página como
     uma lista de links e extrai as URLs de notícia dela.

  Adicionei o Bora Investir (`https://borainvestir.b3.com.br/noticias/`)
  como primeiro exemplo. Pra adicionar outro site sem RSS (tipo o que você
  achar do BTG ou da XP), basta colocar a URL da página de listagem de
  notícias dele em `HTML_SOURCES` — não precisa achar o link exato do feed,
  só a página onde as notícias aparecem listadas.

- **Feeds RSS**: a lista `RSS_FEEDS` em `market_digest.py` tem 8 fontes,
  todas com URL completa e confirmada — nada truncado ou adivinhado por
  padrão de site (foi assim que errei da última vez). Incluí duas
  internacionais de peso (Investing.com e Seeking Alpha) pra reforçar a
  categoria "Mercado Internacional".

  Candidatos que valem seu teste (achei referência de que existem, mas a
  URL completa não veio clara o suficiente pra eu confiar sem verificar):
  `borainvestir.b3.com.br` (B3), `braziljournal.com` (jornalismo de
  negócios bem respeitado), `warren.com.br/magazine`,
  `blog.toroinvestimentos.com.br`, `blog.genialinvestimentos.com.br`. Use o
  teste de uma linha abaixo pra confirmar antes de adicionar.

  Pra adicionar qualquer feed novo com confiança (sem depender de eu
  adivinhar), teste assim antes de colocar na lista:

  ```bash
  pip install feedparser
  python3 -c "import feedparser; f = feedparser.parse('URL_AQUI'); print(len(f.entries), 'itens' if f.entries else f.get('bozo_exception', 'falhou'))"
  ```

  Se aparecer um número de itens maior que zero, o feed funciona. Muitos
  sites de notícia expõem o link do RSS no `<head>` da página com
  `rel="alternate" type="application/rss+xml"` — dá pra achar isso vendo o
  código-fonte da página no navegador (Ctrl+U) e procurando por "rss" ou
  "feed".
- **Categorias**: edite a lista `CATEGORIES` — o campo `hint` é o que
  orienta o Gemini a filtrar o que é relevante pra cada uma.
- **Janela de tempo**: `JANELA_HORAS` controla quantas horas pra trás contam
  como "recente" no pool de notícias (36h por padrão).
- **Quantidade de notícias por categoria**: `MAX_ITENS_POR_CATEGORIA` (4 por
  padrão) e `MAX_CHARS_POR_ARTIGO` (4000 por padrão, o quanto de cada artigo
  entra no resumo) — suba esses números se quiser resumos mais completos,
  ao custo de prompts maiores.
- **Horário**: edite o `cron` em `.github/workflows/market-digest.yml`
  (sempre em UTC).

## Por que agora são 2 chamadas de Gemini por categoria (e não 1)

Antes, uma única chamada tentava filtrar E resumir ao mesmo tempo, só com o
resuminho do RSS. Agora o processo é em duas etapas: uma triagem rápida
(barata, só título e resuminho) decide o que é relevante, e só depois disso
o script busca o artigo inteiro e manda pro Gemini resumir com o texto
completo. Isso evita baixar o texto de dezenas de notícias que nem vão ser
usadas, e dá um resumo final bem mais rico do que o "clique aqui" de duas
linhas que vem no RSS.