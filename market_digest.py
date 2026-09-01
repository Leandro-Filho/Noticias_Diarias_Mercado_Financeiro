"""
Resumo de Mercado — bot de digest diário no Telegram, 100% gratuito.

Como funciona agora (com texto completo, não só resuminho de RSS):
  1. Busca notícias recentes em vários feeds RSS (gratuito, sem limite).
  2. TRIAGEM (1 chamada à Groq): olha o pool inteiro (títulos + resumo
     curto) e decide quais notícias são relevantes pra cada categoria.
  3. TEXTO COMPLETO: baixa e extrai o artigo inteiro (não só o resuminho do
     RSS) de cada notícia selecionada, usando a biblioteca trafilatura.
  4. SÍNTESE (1 chamada à Groq por categoria): junta o texto completo de
     todas as notícias selecionadas daquela categoria e produz um resumo
     rico, com números e impacto no mercado — usando o máximo de conteúdo
     disponível, não só a linha do RSS.
  5. Envia uma mensagem por categoria pro Telegram.

Custo: R$ 0. RSS, extração de texto (trafilatura roda local, sem API), a
camada gratuita da Groq (7 chamadas por dia: 1 triagem + 6 síntese) e o
Telegram Bot API não cobram nada nesse volume. Ver README para o único
cuidado real (limite gratuito pode mudar, embora o da Groq tenha histórico
bem mais estável que o do Gemini).

Variáveis de ambiente necessárias (ver README.md):
  GROQ_API_KEY                 - chave gratuita do console.groq.com
  TELEGRAM_BOT_TOKEN           - token do bot, gerado pelo @BotFather
  TELEGRAM_CHAT_ID             - chat padrão (fallback)
  TELEGRAM_CATEGORY_CHAT_IDS   - JSON opcional mapeando categoria -> chat_id
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import trafilatura
from dotenv import load_dotenv
from json_repair import repair_json
from openai import OpenAI
from trafilatura import sitemaps as trafilatura_sitemaps
from trafilatura import feeds as trafilatura_feeds

load_dotenv()  # se existir um .env local, carrega ele; no GitHub Actions
                # não existe .env, então isso não faz nada e os secrets
                # continuam vindo normalmente das variáveis de ambiente

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CATEGORY_CHAT_IDS = json.loads(os.environ.get("TELEGRAM_CATEGORY_CHAT_IDS") or "{}")

# Checagem explícita: se algum secret obrigatório não foi configurado no
# GitHub (ou ficou vazio), o GitHub Actions passa string vazia em vez de
# não passar nada — o que gera erro confuso lá na frente. Falhando aqui,
# com mensagem clara, poupa tempo de depuração.
if not GROQ_API_KEY:
    raise SystemExit(
        "GROQ_API_KEY está vazio ou não configurado. Confere em "
        "Settings → Secrets and variables → Actions no GitHub se o secret "
        "GROQ_API_KEY existe com esse nome exato e tem um valor de verdade."
    )
if not TELEGRAM_BOT_TOKEN:
    raise SystemExit(
        "TELEGRAM_BOT_TOKEN está vazio ou não configurado. Confere o secret "
        "TELEGRAM_BOT_TOKEN no GitHub."
    )

# A Groq expõe uma API compatível com o formato da OpenAI (autenticação
# padrão via Bearer token) — por isso dá pra usar o pacote "openai" comum,
# só apontando pra base_url da Groq. Formato de autenticação padrão do
# mercado, sem as pegadinhas de formato de chave que tivemos com o Gemini.
groq_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

# Nomes de modelo mudam com o tempo — se parar de funcionar, confira
# https://console.groq.com/docs/models (a Groq costuma avisar por e-mail
# com bastante antecedência quando descontinua um modelo).
# Histórico do que já tentamos e não deu certo, pra não repetir:
#   - openai/gpt-oss-120b: modelo "de raciocínio" (gasta token pensando
#     antes de responder) + bug documentado no fórum da Groq com JSON
#     garantido (json_validate_failed).
#   - moonshotai/kimi-k2-instruct: descontinuado pela Groq em 2026.
# Qwen 3.6 27B suporta desligar o raciocínio via reasoning_effort="none"
# (ver abaixo, em call_groq_json) — é servido como modelo "preview" pela
# Groq, então pode mudar de status; se um dia parar de funcionar, comece
# a investigação por aí.
GROQ_MODEL = "qwen/qwen3.6-27b"

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
# feed de jeito nenhum.
HTML_SOURCES = [
    "https://borainvestir.b3.com.br/noticias/",
    "https://www.clubefiinews.com.br/",  # fundos imobiliários (FIIs) — reforça a categoria mais fraca hoje
    "https://livecoins.com.br/noticias/",  # cripto — maior portal brasileiro do tema
    "https://guiadobitcoin.com.br/noticias/",  # cripto — segunda fonte, pra não depender só do Livecoins
]

# Quantos links puxar de cada fonte sem RSS (a lista costuma vir sem
# garantia de ordem cronológica, então nem tudo aqui vai ser recentíssimo).
MAX_LINKS_POR_HTML_SOURCE = 8

# Quantas horas pra trás considerar uma notícia "recente" no pool.
JANELA_HORAS = 36

# Onde o digest do dia é salvo — o chat_bot.py lê esse arquivo pra poder
# conversar com base no que já foi analisado hoje.
DIGEST_PATH = "ultimo_digest.json"

# Quantos itens no máximo por categoria vão pro texto completo (controla
# custo de banda/tempo e tamanho do prompt de síntese). Reduzido de 4 pra 2
# porque cada item agora traz análise bem mais rica (mecanismo, implicação,
# próximo evento) — 4 itens desse tamanho estourava o limite de caracteres
# de uma mensagem do Telegram.
MAX_ITENS_POR_CATEGORIA = 2

# Quantos caracteres do texto completo de cada artigo entram no prompt de
# síntese (o suficiente pra uma notícia inteira, sem estourar o limite de
# tokens da camada gratuita).
MAX_CHARS_POR_ARTIGO = 4000

# Quantos caracteres do resumo de cada notícia entram no prompt de TRIAGEM
# (não confundir com MAX_CHARS_POR_ARTIGO, que é pro texto completo na
# síntese). A triagem só precisa de contexto suficiente pra classificar a
# categoria, então mantemos curto — com o pool crescendo (mais feeds, mais
# notícias), isso evita estourar o limite de tokens por minuto da camada
# gratuita.
MAX_CHARS_TRIAGEM = 80

CATEGORIES = [
    {"id": "renda_fixa", "name": "Renda Fixa", "emoji": "🏦", "hint": "Selic, CDI, IPCA, Tesouro Direto, CDBs, LCI, LCA"},
    {"id": "acoes", "name": "Renda Variável / Ações", "emoji": "📈", "hint": "Ibovespa, ações e empresas listadas na bolsa brasileira"},
    {"id": "cripto", "name": "Criptomoedas", "emoji": "🪙", "hint": "Bitcoin, Ethereum, criptomoedas, regulação cripto"},
    {"id": "debentures", "name": "Debêntures e Crédito Privado", "emoji": "📄", "hint": "debêntures, emissões, crédito privado, spread de crédito"},
    {"id": "exterior", "name": "Mercado Internacional", "emoji": "🌎", "hint": "Federal Reserve, juros nos EUA, S&P 500, Nasdaq, mercados globais"},
    {"id": "imobiliario", "name": "Mercado Imobiliário", "emoji": "🏗️", "hint": "fundos imobiliários (FIIs), CRI, financiamento imobiliário"},
]

MESES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def data_hoje_pt() -> str:
    """Monta a data em português sem depender de locale do sistema (que pode
    não estar disponível/configurado igual no GitHub Actions)."""
    agora = datetime.now()
    return f"{agora.day} de {MESES_PT[agora.month - 1]} de {agora.year}"


def aviso_data() -> str:
    """Aviso pra colocar em todo prompt: sem isso, o modelo pode tratar
    notícia/lei datada do ano corrente como 'futuro' ou 'erro', porque o
    conhecimento de treinamento dele tem um corte anterior a hoje."""
    return (
        f"IMPORTANTE: a data de hoje é {data_hoje_pt()}. Trate isso como o "
        "presente real — seu conhecimento de treinamento pode ter um corte "
        f"anterior a essa data, então NUNCA trate uma notícia, lei ou evento "
        f"datado de {datetime.now().year} como \"futuro\", \"hipotético\" ou "
        '"erro de data" só por parecer posterior ao que você aprendeu. Se a '
        "notícia afirma algo com essa data, é fato atual, não projeção."
    )


def parse_data_artigo(data_str: str | None) -> datetime | None:
    """O trafilatura (via htmldate) devolve a data do artigo no formato
    YYYY-MM-DD. Se não vier nesse formato ou vier vazio, devolve None — nesse
    caso o item é descartado por segurança em vez de arriscar mostrar
    notícia velha como se fosse recente."""
    if not data_str:
        return None
    try:
        return datetime.strptime(data_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


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
    texto completo baixado (evita baixar a mesma página duas vezes).

    Importante: como não vem de RSS, não tem data pronta — extraímos a data
    do próprio artigo e descartamos qualquer um fora da janela de recência
    (JANELA_HORAS), ou sem data reconhecível. Sem isso, notícia velha da
    lista do site (o sitemap não garante ordem cronológica) podia entrar
    junto com as de hoje sem nenhum aviso — o que já aconteceu na prática.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=JANELA_HORAS)
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

            data_artigo = parse_data_artigo(metadata.date if metadata else None)
            if data_artigo is None:
                print(f"Sem data reconhecível, descartando por segurança: {link}")
                continue
            if data_artigo < cutoff:
                print(f"Notícia antiga ({data_artigo.date()}), descartando: {link}")
                continue

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
            # feedparser.parse(url) não tem timeout embutido — se passarmos
            # a URL direto, um feed lento pode travar o script indefinidamente.
            # Por isso baixamos com requests (que tem timeout) e só então
            # entregamos o conteúdo já baixado pro feedparser interpretar.
            resp = requests.get(feed_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
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


# --------------------------------------------------------------------- Groq

def call_groq_json(prompt: str, max_tokens: int = 1500, tentativas: int = 3) -> dict:
    """Chama a Groq pedindo JSON. NÃO usamos response_format={"type":"json_object"}
    de propósito: esse modo ("JSON garantido"/constrained decoding) tem bug
    documentado no fórum da própria Groq nos modelos gpt-oss (json_validate_failed,
    reproduzível). Em vez disso, pedimos JSON só por instrução no prompt e
    extraímos o bloco {...} da resposta na mão — mais simples, mas não depende
    de um mecanismo da Groq que está com bug conhecido."""
    prompt_com_reforco = prompt + "\n\nResponda em português, SOMENTE com o JSON pedido, sem markdown, sem texto antes ou depois."

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            response = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt_com_reforco}],
                temperature=0,  # mais previsível pra gerar JSON válido, menos "criativo"
                max_tokens=max_tokens,
                extra_body={"reasoning_effort": "none"},  # desliga a etapa de "pensar" do
                                                            # Qwen 3.6, que senão gasta tokens
                                                            # de saída antes de escrever o JSON
            )
            raw = response.choices[0].message.content.strip()
            clean = raw.replace("```json", "").replace("```", "").strip()
            start, end = clean.find("{"), clean.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"Resposta sem JSON reconhecível: {raw[:200]}")
            # repair_json conserta erros comuns de LLM (vírgula faltando, chave
            # sem aspas, vírgula sobrando no fim) em vez de exigir JSON perfeito
            resultado = repair_json(clean[start : end + 1], return_objects=True)
            if not isinstance(resultado, dict):
                raise ValueError(f"JSON reparado não é um objeto: {resultado!r}")
            return resultado
        except Exception as exc:
            ultimo_erro = exc
            print(f"Tentativa {tentativa}/{tentativas} falhou ({exc}); tentando de novo...")
            time.sleep(2)
    raise ultimo_erro


def triagem(pool: list[dict]) -> dict:
    """1 chamada só: decide quais itens do pool servem pra cada categoria."""
    pool_text = "\n".join(
        f"{i}. [{item['source']}] {item['title']} — {item['summary'][:MAX_CHARS_TRIAGEM]}"
        for i, item in enumerate(pool)
    )
    categorias_text = "\n".join(f'- "{c["id"]}" ({c["name"]}): {c["hint"]}' for c in CATEGORIES)
    exemplo_schema = "{" + ", ".join(f'"{c["id"]}": []' for c in CATEGORIES) + "}"

    prompt = f"""{aviso_data()}

Abaixo está uma lista numerada de notícias recentes de mercado
financeiro, sem categoria definida, e a lista de categorias que existem
(o texto entre aspas é o identificador que você deve usar como chave).

Categorias:
{categorias_text}

Notícias (numeradas de 0 em diante):
{pool_text}

Pra cada categoria, escolha até {MAX_ITENS_POR_CATEGORIA} notícias da lista
que sejam relevantes pra ela (pode ser lista vazia). Uma mesma notícia pode
servir pra mais de uma categoria se fizer sentido. Responda SOMENTE com um
JSON válido, usando exatamente os identificadores entre aspas acima como
chave, e o NÚMERO de cada notícia escolhida (não a URL, não o título — só o
número que precede ela na lista) como valor. Formato exato, sem nenhum texto
antes ou depois:

{exemplo_schema}

Exemplo de resposta válida (números são só ilustrativos): {{"renda_fixa": [3, 17], "acoes": [0, 5, 22]}}"""

    return call_groq_json(prompt, max_tokens=400)  # resposta é só números, cabe tranquilo


def analisa_noticia(article: dict) -> dict:
    """1 chamada por notícia (não por categoria): analisa em profundidade,
    focado em ajudar decisão de investimento de curto/médio prazo — não só
    resumir o que já aconteceu."""
    prompt = f"""{aviso_data()}

Você é um analista de mercado ajudando um investidor de curto/médio
prazo a decidir onde prestar atenção — não é só um resumo de notícia, é uma
leitura de investimento. Responda sempre em português do Brasil.

Fonte: {article['source']}
Título: {article['title']}
Texto completo:
{article['text']}

Responda SOMENTE com um JSON no formato exato:

{{"resumo":"...","mecanismo":"...","proximo_evento":"...","implicacao":"...","key_numbers":["Rótulo: valor"],"sentiment":"up"}}

Regras pra cada campo:
- "resumo": 1 a 2 frases, o fato em si, com suas PRÓPRIAS palavras — nunca
  copie frases exatas da fonte.
- "mecanismo": explique a CADEIA causal — o que está acontecendo e POR QUE
  isso leva ao efeito esperado. Exemplo do estilo esperado: "O Copom sinaliza
  corte de 0,50 p.p. na próxima reunião porque a inflação desacelerou mais
  que o previsto — isso reduz o custo de crédito e tende a beneficiar ativos
  sensíveis a juros, como IPCA+ mais longos.". Seja específico, não genérico.
- "proximo_evento": qual é o PRÓXIMO gatilho relacionado a essa notícia que
  vale acompanhar (próxima reunião, próximo dado divulgado, prazo) — isso é
  o que vira notícia depois, não o que já aconteceu.
- "implicacao": a cadeia de raciocínio setorial/classe de ativo, no estilo
  "porque X, o setor/classe Y tende a Z, já que [mecanismo]". Exemplo do
  estilo esperado: "Com a Selic caindo, o setor imobiliário tende a se
  beneficiar, porque crédito mais barato aumenta o poder de financiamento e
  puxa demanda por imóveis.". Fale de SETOR ou CLASSE DE ATIVO, nunca de uma
  ação/empresa específica por nome — isso não é recomendação de compra/venda,
  é raciocínio pra você aplicar com seu próprio julgamento.
- "key_numbers": no máximo 4 números, cada um com RÓTULO (formato "Rótulo:
  valor", ex: "Selic: 15%") — nunca número solto sem dizer o que é.
- "sentiment": "up", "down" ou "neutral"."""

    return call_groq_json(prompt, max_tokens=900)


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


def format_category_message(cat: dict, items: list[dict]) -> str:
    today = datetime.now().strftime("%d/%m")
    lines = [f"<b>{cat['emoji']} {cat['name']}</b> · {today}"]
    if not items:
        lines.append("Sem novidades relevantes hoje.")
    for it in items:
        icon = {"up": "📈", "down": "📉"}.get(it.get("sentiment", "neutral"), "➖")
        lines.append("")
        lines.append(f"{icon} <b>{escape_html(it.get('title', ''))}</b>")
        if it.get("source"):
            lines.append(f"<i>{escape_html(it['source'])}</i>")
        if it.get("resumo"):
            lines.append(escape_html(it["resumo"]))
        nums = it.get("key_numbers") or []
        if nums:
            lines.append("📊 " + " · ".join(escape_html(n) for n in nums))
        if it.get("mecanismo"):
            lines.append("🔎 " + escape_html(it["mecanismo"]))
        if it.get("implicacao"):
            lines.append("💡 " + escape_html(it["implicacao"]))
        if it.get("proximo_evento"):
            lines.append("📅 <i>De olho em:</i> " + escape_html(it["proximo_evento"]))
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

    # a triagem devolve números (índices na lista `pool`), não URL — resolvemos aqui
    def indices_validos(valores):
        return [i for i in valores if isinstance(i, int) and 0 <= i < len(pool)]

    # baixa texto completo só das URLs que alguma categoria escolheu, uma vez cada
    # (itens de HTML_SOURCES já estão em full_text_cache, então não baixam de novo)
    indices_selecionados = {i for idxs in selecao.values() for i in indices_validos(idxs)}
    urls_selecionadas = {pool[i]["link"] for i in indices_selecionados}
    textos = dict(full_text_cache)
    for url in urls_selecionadas:
        if url in textos:
            continue
        texto = fetch_full_text(url)
        # se não conseguir o texto completo, cai pro resuminho do RSS mesmo assim
        textos[url] = texto or pool_by_link.get(url, {}).get("summary", "")

    # analisa cada notícia selecionada UMA VEZ (mesmo que sirva pra mais de uma
    # categoria), cacheando por índice — evita gastar chamada em dobro
    analises_por_indice: dict[int, dict] = {}
    for i in sorted(indices_selecionados):
        base = pool[i]
        article = {**base, "text": textos.get(base["link"], base["summary"])}
        try:
            analises_por_indice[i] = {**base, **analisa_noticia(article)}
        except Exception as exc:
            print(f"Falha analisando notícia {i} ({base.get('title', '')[:60]}): {exc}")
        time.sleep(1)  # espaça as chamadas de análise entre si

    for cat in CATEGORIES:
        chat_id = resolve_chat_id(cat["name"])
        if not chat_id:
            print(f"Sem grupo nem chat padrão configurado para '{cat['name']}' — pulando.")
            continue

        idxs_cat = indices_validos(selecao.get(cat["id"], []))
        items = [analises_por_indice[i] for i in idxs_cat if i in analises_por_indice]

        try:
            message = format_category_message(cat, items)
        except Exception as exc:
            message = f"⚠️ <b>{cat['emoji']} {cat['name']}</b>\nNão consegui montar essa categoria hoje ({exc})."

        send_telegram(chat_id, message)
        time.sleep(2)

    salvar_digest_do_dia(selecao, analises_por_indice, indices_validos)


def salvar_digest_do_dia(selecao, analises_por_indice, indices_validos) -> None:
    """Grava o digest de hoje num arquivo do próprio repositório, pro
    chat_bot.py conseguir responder pergunta com base nisso depois."""
    categorias_por_indice: dict[int, list[str]] = {}
    for cat in CATEGORIES:
        for i in indices_validos(selecao.get(cat["id"], [])):
            categorias_por_indice.setdefault(i, []).append(cat["id"])

    itens = []
    for i, analise in analises_por_indice.items():
        itens.append(
            {
                "title": analise.get("title"),
                "source": analise.get("source"),
                "link": analise.get("link"),
                "categorias": categorias_por_indice.get(i, []),
                "resumo": analise.get("resumo"),
                "mecanismo": analise.get("mecanismo"),
                "implicacao": analise.get("implicacao"),
                "proximo_evento": analise.get("proximo_evento"),
                "key_numbers": analise.get("key_numbers"),
                "sentiment": analise.get("sentiment"),
            }
        )

    with open(DIGEST_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"gerado_em": datetime.now(timezone.utc).isoformat(), "itens": itens},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Salvo {DIGEST_PATH} com {len(itens)} notícias analisadas hoje.")


if __name__ == "__main__":
    main()