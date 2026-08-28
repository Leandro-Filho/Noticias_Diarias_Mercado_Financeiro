"""
Resumo de Mercado — bot de digest diário no Telegram, 100% gratuito.

Como funciona agora (com texto completo, não só resuminho de RSS):
  1. Busca notícias recentes em vários feeds RSS (gratuito, sem limite).
  2. TRIAGEM (1 chamada ao Gemini): olha o pool inteiro (títulos + resumo
     curto) e decide quais notícias são relevantes pra cada categoria.
  3. TEXTO COMPLETO: baixa e extrai o artigo inteiro (não só o resuminho do
     RSS) de cada notícia selecionada, usando a biblioteca trafilatura.
  4. SÍNTESE (1 chamada ao Gemini por categoria): junta o texto completo de
     todas as notícias selecionadas daquela categoria e produz um resumo
     rico, com números e impacto no mercado — usando o máximo de conteúdo
     disponível, não só a linha do RSS.
  5. Envia uma mensagem por categoria pro Telegram.

Custo: R$ 0. RSS, extração de texto (trafilatura roda local, sem API), a
camada gratuita do Gemini (7 chamadas por dia: 1 triagem + 6 síntese) e o
Telegram Bot API não cobram nada nesse volume. Ver README para o único
cuidado real (limite gratuito do Gemini pode mudar).

Variáveis de ambiente necessárias (ver README.md):
  GEMINI_API_KEY              - chave gratuita do Google AI Studio
  TELEGRAM_BOT_TOKEN          - token do bot, gerado pelo @BotFather
  TELEGRAM_CHAT_ID            - chat padrão (fallback)
  TELEGRAM_CATEGORY_CHAT_IDS  - JSON opcional mapeando categoria -> chat_id
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import trafilatura
from trafilatura import sitemaps as trafilatura_sitemaps
from trafilatura import feeds as trafilatura_feeds

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CATEGORY_CHAT_IDS = json.loads(os.environ.get("TELEGRAM_CATEGORY_CHAT_IDS", "{}"))

# Nomes de modelo mudam com o tempo — se parar de funcionar, confira
# https://ai.google.dev/gemini-api/docs/models
GEMINI_MODEL = "gemini-2.5-flash-lite"

# Pool de feeds RSS — gratuitos, sem chave. Mantive só os que dá pra
# confirmar de forma completa e sem ambiguidade (ver README pra por que
# fui mais conservador dessa vez, e uma lista de candidatos pra você testar
# e adicionar).
RSS_FEEDS = [
    "https://www.infomoney.com.br/mercados/feed/",
    "https://www.infomoney.com.br/ultimas-noticias/feed/",
    "https://www.infomoney.com.br/onde-investir/feed/",
    "https://www.moneytimes.com.br/mercados/feed/",
    "https://cointelegraph.com/rss",
    "https://cma.com.br/feed",
    "https://www.investing.com/rss/news_25.rss",
    "https://seekingalpha.com/feed.xml",
]

# Sites SEM RSS que funcionam: entramos pelo sitemap (ou link list) em vez
# do feed. Cada entrada é só a página de listagem/seção do site — a função
# discover_article_links() cuida do resto. Bom pra sites que não expõem
# feed de jeito nenhum (caso do Bora Investir, da B3).
HTML_SOURCES = [
    "https://borainvestir.b3.com.br/noticias/",
]

# Quantos links puxar de cada fonte sem RSS (a lista costuma vir sem
# garantia de ordem cronológica, então nem tudo aqui vai ser recentíssimo).
MAX_LINKS_POR_HTML_SOURCE = 8

# Quantas horas pra trás considerar uma notícia "recente" no pool.
JANELA_HORAS = 36

# Quantos itens no máximo por categoria vão pro texto completo (controla
# custo de banda/tempo e tamanho do prompt de síntese).
MAX_ITENS_POR_CATEGORIA = 4

# Quantos caracteres do texto completo de cada artigo entram no prompt de
# síntese (o suficiente pra uma notícia inteira, sem estourar o limite de
# tokens da camada gratuita).
MAX_CHARS_POR_ARTIGO = 4000

CATEGORIES = [
    {"name": "Renda Fixa", "emoji": "🏦", "hint": "Selic, CDI, IPCA, Tesouro Direto, CDBs, LCI, LCA"},
    {"name": "Renda Variável / Ações", "emoji": "📈", "hint": "Ibovespa, ações e empresas listadas na bolsa brasileira"},
    {"name": "Criptomoedas", "emoji": "🪙", "hint": "Bitcoin, Ethereum, criptomoedas, regulação cripto"},
    {"name": "Debêntures e Crédito Privado", "emoji": "📄", "hint": "debêntures, emissões, crédito privado, spread de crédito"},
    {"name": "Mercado Internacional", "emoji": "🌎", "hint": "Federal Reserve, juros nos EUA, S&P 500, Nasdaq, mercados globais"},
    {"name": "Mercado Imobiliário", "emoji": "🏗️", "hint": "fundos imobiliários (FIIs), CRI, financiamento imobiliário"},
]


def discover_article_links(listing_url: str) -> list[str]:
    """Pra sites sem RSS: acha links de notícia via sitemap, com fallback pra
    heurística de 'página é uma lista de links' do trafilatura."""
    try:
        links = trafilatura_sitemaps.sitemap_search(listing_url)
    except Exception as exc:
        print(f"Sitemap falhou pra {listing_url}: {exc}")
        links = []

    if not links:
        try:
            links = trafilatura_feeds.find_feed_urls(listing_url)
        except Exception as exc:
            print(f"find_feed_urls falhou pra {listing_url}: {exc}")
            links = []

    return links[:MAX_LINKS_POR_HTML_SOURCE]


def fetch_pool_from_html_sources(full_text_cache: dict) -> list[dict]:
    """Monta itens de pool a partir de sites sem RSS, já aproveitando o
    texto completo baixado (evita baixar a mesma página duas vezes)."""
    pool = []
    for listing_url in HTML_SOURCES:
        links = discover_article_links(listing_url)
        print(f"{len(links)} links encontrados em {listing_url} (sem RSS)")
        for link in links:
            downloaded = trafilatura.fetch_url(link)
            if not downloaded:
                continue
            text = trafilatura.extract(downloaded)
            if not text:
                continue
            metadata = trafilatura.extract_metadata(downloaded)
            title = (metadata.title if metadata and metadata.title else link)
            full_text_cache[link] = text[:MAX_CHARS_POR_ARTIGO]
            pool.append(
                {
                    "title": title,
                    "summary": text[:300],
                    "link": link,
                    "source": (metadata.sitename if metadata and metadata.sitename else listing_url),
                }
            )
    return pool


# ---------------------------------------------------------------- RSS pool

def fetch_pool() -> list[dict]:
    """Baixa os feeds RSS e devolve uma lista de itens recentes e únicos."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=JANELA_HORAS)
    pool, seen_links = [], set()

    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
            if parsed.bozo and not parsed.entries:
                print(f"Feed vazio/inválido, pulando: {feed_url}")
                continue
        except Exception as exc:
            print(f"Falha lendo feed {feed_url}: {exc}")
            continue

        for entry in parsed.entries:
            link = entry.get("link")
            if not link or link in seen_links:
                continue

            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue

            seen_links.add(link)
            pool.append(
                {
                    "title": entry.get("title", ""),
                    "summary": (entry.get("summary", "") or "")[:300],
                    "link": link,
                    "source": parsed.feed.get("title", feed_url),
                }
            )

    return pool


# ------------------------------------------------------------------ Gemini

def call_gemini_json(prompt: str) -> dict:
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def triagem(pool: list[dict]) -> dict:
    """1 chamada só: decide quais itens do pool servem pra cada categoria."""
    pool_text = "\n".join(
        f"{i}. [{item['source']}] {item['title']} — {item['summary']} ({item['link']})"
        for i, item in enumerate(pool)
    )
    categorias_text = "\n".join(f"- {c['name']}: {c['hint']}" for c in CATEGORIES)

    prompt = f"""Abaixo está uma lista numerada de notícias recentes de mercado
financeiro, sem categoria definida, e a lista de categorias que existem.

Categorias:
{categorias_text}

Notícias:
{pool_text}

Pra cada categoria, escolha até {MAX_ITENS_POR_CATEGORIA} notícias da lista
que sejam relevantes pra ela (pode ser nenhuma). Uma mesma notícia pode
servir pra mais de uma categoria se fizer sentido. Responda SOMENTE com um
JSON no formato exato, usando a URL exata de cada notícia escolhida:

{{"Renda Fixa": ["url1", "url2"], "Renda Variável / Ações": [], "Criptomoedas": [], "Debêntures e Crédito Privado": [], "Mercado Internacional": [], "Mercado Imobiliário": []}}"""

    return call_gemini_json(prompt)


def sintese(cat: dict, articles: list[dict]) -> dict:
    """1 chamada por categoria: resume o texto completo dos artigos escolhidos."""
    if not articles:
        return {"items": []}

    corpo = "\n\n---\n\n".join(
        f"Fonte: {a['source']}\nTítulo: {a['title']}\nURL: {a['link']}\nTexto:\n{a['text']}"
        for a in articles
    )

    prompt = f"""Você é um analista de mercado preparando um resumo diário curto para
um profissional que está se preparando para atuar com wealth management em
um banco. Responda sempre em português do Brasil.

Categoria: {cat['name']} ({cat['hint']})

Abaixo está o texto completo de {len(articles)} notícia(s) já selecionadas
como relevantes pra essa categoria. Use o máximo de conteúdo desses textos
pra produzir um resumo rico e preciso — não fique só na manchete.

{corpo}

Responda SOMENTE com um JSON no formato exato:

{{"items":[{{"title":"...","source":"...","url":"...","summary":"...","key_numbers":["..."],"market_impact":"...","sentiment":"up"}}]}}

Regras:
- Um item por notícia recebida (não invente notícia que não está no texto acima).
- "summary": 2 a 4 frases, com suas PRÓPRIAS palavras, aproveitando o texto
  completo (não só o título) — nunca copie frases exatas da fonte.
- "key_numbers": todos os números relevantes citados no texto (percentuais,
  valores, datas de referência).
- "market_impact": 1 a 2 frases objetivas sobre o efeito esperado no mercado.
- "sentiment": "up", "down" ou "neutral"."""

    return call_gemini_json(prompt)


# -------------------------------------------------------------- Texto completo

def fetch_full_text(url: str) -> str | None:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded)
        if not text:
            return None
        return text[:MAX_CHARS_POR_ARTIGO]
    except Exception as exc:
        print(f"Falha extraindo texto completo de {url}: {exc}")
        return None


# --------------------------------------------------------------- Formatação

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_category_message(cat: dict, data: dict) -> str:
    today = datetime.now().strftime("%d/%m")
    lines = [f"<b>{cat['emoji']} {cat['name']}</b> · {today}"]
    items = data.get("items", [])
    if not items:
        lines.append("Sem novidades relevantes hoje.")
    for it in items:
        icon = {"up": "📈", "down": "📉"}.get(it.get("sentiment", "neutral"), "➖")
        lines.append("")
        lines.append(f"{icon} <b>{escape_html(it.get('title', ''))}</b>")
        if it.get("source"):
            lines.append(f"<i>{escape_html(it['source'])}</i>")
        if it.get("summary"):
            lines.append(escape_html(it["summary"]))
        nums = it.get("key_numbers") or []
        if nums:
            lines.append("📊 " + " · ".join(escape_html(n) for n in nums))
        if it.get("market_impact"):
            lines.append("💡 " + escape_html(it["market_impact"]))
    return "\n".join(lines)


def send_telegram(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not resp.ok:
        print(f"Erro ao enviar mensagem pro chat {chat_id}:", resp.text)


def resolve_chat_id(category_name: str) -> str | None:
    return CATEGORY_CHAT_IDS.get(category_name) or DEFAULT_CHAT_ID


# --------------------------------------------------------------------- Main

def main() -> None:
    full_text_cache: dict[str, str] = {}

    pool = fetch_pool()
    pool += fetch_pool_from_html_sources(full_text_cache)  # sites sem RSS, ex: Bora Investir
    print(f"{len(pool)} notícias no pool (últimas {JANELA_HORAS}h, {len(RSS_FEEDS)} feeds RSS + {len(HTML_SOURCES)} sites sem RSS).")
    if not pool:
        print("Pool vazio, nada a fazer hoje.")
        return

    pool_by_link = {item["link"]: item for item in pool}

    try:
        selecao = triagem(pool)
    except Exception as exc:
        print(f"Triagem falhou ({exc}) — abortando.")
        return

    # baixa texto completo só das URLs que alguma categoria escolheu, uma vez cada
    # (itens de HTML_SOURCES já estão em full_text_cache, então não baixam de novo)
    urls_selecionadas = {url for urls in selecao.values() for url in urls}
    textos = dict(full_text_cache)
    for url in urls_selecionadas:
        if url in textos:
            continue
        texto = fetch_full_text(url)
        # se não conseguir o texto completo, cai pro resuminho do RSS mesmo assim
        textos[url] = texto or pool_by_link.get(url, {}).get("summary", "")

    for cat in CATEGORIES:
        chat_id = resolve_chat_id(cat["name"])
        if not chat_id:
            print(f"Sem grupo nem chat padrão configurado para '{cat['name']}' — pulando.")
            continue

        urls_cat = selecao.get(cat["name"], [])
        articles = []
        for url in urls_cat:
            base = pool_by_link.get(url)
            if not base:
                continue
            articles.append({**base, "text": textos.get(url, base["summary"])})

        try:
            data = sintese(cat, articles)
            message = format_category_message(cat, data)
        except Exception as exc:
            message = f"⚠️ <b>{cat['emoji']} {cat['name']}</b>\nNão consegui buscar essa categoria hoje ({exc})."

        send_telegram(chat_id, message)
        time.sleep(2)


if __name__ == "__main__":
    main()