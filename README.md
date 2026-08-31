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
3. **Groq na camada gratuita** — a Groq dá acesso de graça, sem cartão de
   crédito, a modelos open-source (Llama, GPT-OSS, Qwen) rodando no
   hardware próprio dela, que é muito rápido. O script usa 2 etapas: uma
   chamada pra triagem (decide quais notícias do lote servem pra cada
   categoria) e uma chamada por categoria pra síntese (resume o texto
   completo das notícias escolhidas). No total, 7 chamadas por dia — bem
   dentro do limite gratuito da Groq, que é de 14.400 requisições por dia.
4. **Telegram Bot API e GitHub Actions** — já eram gratuitos, sem mudança.

**Por que Groq e não Gemini:** começamos com o Gemini, mas esbarramos em
dois problemas reais no meio do caminho — nome de modelo que mudou de uma
hora pra outra, e uma mudança recente no formato das chaves de API do
Google que quebrou chamada HTTP direta pra várias contas (inclusive a
nossa). A Groq usa autenticação padrão do mercado (token Bearer simples,
igual a praticamente todo mundo usa) e tem tier gratuito estável desde o
lançamento, sem sinal de mudança. Nenhuma garantia é eterna, mas essa tem
o histórico mais limpo.

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

### 3. Pegar sua chave gratuita da Groq

1. Entre em [console.groq.com](https://console.groq.com) e faça login com
   e-mail ou conta Google/GitHub.
2. Vá em **API Keys** e crie uma nova chave — não pede cartão de crédito.
3. Guarde esse valor — é o seu `GROQ_API_KEY`.

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
| `GROQ_API_KEY` | a chave do passo 3 |
| `TELEGRAM_BOT_TOKEN` | o token do passo 1 |
| `TELEGRAM_CHAT_ID` | o número do passo 2 |
| `TELEGRAM_CATEGORY_CHAT_IDS` | o JSON do passo 2b — opcional |

### 6. Testar

Você tem duas formas de testar — local (mais rápido pra depurar) ou direto
no GitHub Actions (mais fiel ao que vai rodar de verdade todo dia).

**Testar local, com `.env`:**

1. Copie `.env.example` pra um arquivo novo chamado `.env` (mesma pasta do
   `market_digest.py`).
2. Preencha os valores reais nesse `.env` (chave da Groq, token do bot,
   etc — os mesmos do passo 3, 1 e 2 acima).
3. Rode:
   ```bash
   pip install -r requirements.txt
   python3 market_digest.py
   ```
4. As mensagens já devem chegar no seu Telegram.

**⚠️ Segurança:** o `.env` tem suas chaves reais em texto puro — nunca
suba ele pro GitHub. Já deixei um `.gitignore` no projeto com `.env`
listado nele, então o Git já ignora esse arquivo sozinho por padrão; mesmo
assim, antes do primeiro `git push`, vale conferir com `git status` que o
`.env` não aparece na lista de arquivos a serem enviados.

**Testar no GitHub Actions (depois de configurar os secrets):**

Aba **Actions** → workflow "Resumo de Mercado" → **Run workflow**. Se
estiver tudo certo, as mensagens chegam no Telegram em menos de um minuto.

Depois desse teste, ele roda sozinho todo dia útil às 8h (horário de
Brasília) — pra isso, precisa mesmo dos secrets configurados no GitHub (o
`.env` só serve pra teste no seu computador; a nuvem não tem acesso a ele).
Pra mudar o horário, edite a linha `cron` em
`.github/workflows/market-digest.yml` (o horário do cron é sempre em UTC).

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

  **Sobre B3, BTG e XP, que você perguntou:** pesquisei os três.
  - **BTG Pactual**: não achei nenhum RSS público pro conteúdo de research
    deles — bancos grandes geralmente não publicam feed aberto disso.
  - **XP**: o hub de conteúdo (`conteudos.xpi.com.br`) não expõe RSS que eu
    conseguisse confirmar, mas o podcast diário "Morning Call" tem feed
    confirmado (`https://www.spreaker.com/show/3668124/episodes/feed`) — só
    que é conteúdo de áudio, então a extração de texto completo do script
    não rende muito nele (a descrição do episódio é curta). Não incluí por
    esse motivo, mas fica a opção se você quiser testar.
  - **B3**: tem um portal educacional próprio, o "Bora Investir"
    (`borainvestir.b3.com.br`), que aparentemente tem RSS — mas a URL que
    encontrei estava cortada demais pra eu confiar sem testar.

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
  orienta a Groq a filtrar o que é relevante pra cada uma.
- **Janela de tempo**: `JANELA_HORAS` controla quantas horas pra trás contam
  como "recente" no pool de notícias (36h por padrão).
- **Quantidade de notícias por categoria**: `MAX_ITENS_POR_CATEGORIA` (4 por
  padrão) e `MAX_CHARS_POR_ARTIGO` (4000 por padrão, o quanto de cada artigo
  entra no resumo) — suba esses números se quiser resumos mais completos,
  ao custo de prompts maiores.
- **Horário**: edite o `cron` em `.github/workflows/market-digest.yml`
  (sempre em UTC).

## Por que agora são 2 chamadas de Groq por categoria (e não 1)

Antes, uma única chamada tentava filtrar E resumir ao mesmo tempo, só com o
resuminho do RSS. Agora o processo é em duas etapas: uma triagem rápida
(barata, só título e resuminho) decide o que é relevante, e só depois disso
o script busca o artigo inteiro e manda pra Groq resumir com o texto
completo. Isso evita baixar o texto de dezenas de notícias que nem vão ser
usadas, e dá um resumo final bem mais rico do que o "clique aqui" de duas
linhas que vem no RSS.