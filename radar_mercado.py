"""
Radar do Mercado — mensagem extra enviada junto com o digest da manhã, com:
  - maiores altas e baixas do último pregão entre as AÇÕES mais líquidas da B3
  - maiores altas e baixas entre os FIIs mais líquidos
  - índices internacionais (S&P 500, Nasdaq, Dow, Euro Stoxx, Nikkei)
  - Bitcoin e Ethereum
  - últimos proventos pagos pelos ativos de bolsa da carteira (ex: HGLG11)

Fontes (todas gratuitas):
  - brapi.dev (/api/quote/list) pros rankings da B3 — usa o mesmo BRAPI_TOKEN
    que o chat_bot.py já usa. IMPORTANTE: pra isso funcionar no workflow do
    digest, o main.yml precisa passar BRAPI_TOKEN no env (o chat.yml já passa).
  - yfinance (biblioteca, sem chave) pros índices internacionais, cripto e
    histórico de dividendos. É uma fonte NÃO oficial (lê os dados do Yahoo
    Finance) e o Yahoo às vezes limita requisições vindas de datacenter
    (como o GitHub Actions) — por isso TUDO aqui degrada graciosamente:
    qualquer seção que falhar é simplesmente omitida da mensagem, sem
    derrubar o digest.

Honestidade sobre o horário: o digest roda ~08:07, antes da B3 abrir (10h).
Então as variações de ações/FIIs são do FECHAMENTO DO ÚLTIMO PREGÃO, não do
dia que está começando — a mensagem diz isso explicitamente pra não parecer
que PETR4 "está subindo agora" às 8 da manhã com o mercado fechado.

Sem cotação em tempo real aqui (e em lugar nenhum público/gratuito): FIDCs,
fundos 555/FIF (como o BTG Crédito Corporativo), LCAs e debêntures — esses
não têm pregão contínuo público. Os listados em bolsa (ações, FIIs, ETFs,
BDRs) são o que dá pra cobrir de verdade.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests

BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "")
CARTEIRA_PATH = "carteira.json"

# Quantos ativos mostrar em cada ranking (altas e baixas).
TOP_N = 5

# Universo de liquidez: pegamos os N mais negociados por volume e SÓ ENTÃO
# ordenamos por variação. Sem esse filtro, o ranking de "maiores altas" vira
# uma lista de micro-caps sem liquidez que subiram 40% com meia dúzia de
# negócios — informação inútil e enganosa.
UNIVERSO_ACOES = 150
UNIVERSO_FIIS = 80

INDICES_MUNDO = {
    "^GSPC": "S&P 500 (EUA)",
    "^IXIC": "Nasdaq (EUA)",
    "^DJI": "Dow Jones (EUA)",
    "^STOXX50E": "Euro Stoxx 50 (Europa)",
    "^N225": "Nikkei 225 (Japão)",
}

CRIPTO = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
}


def carrega_carteira() -> dict:
    try:
        with open(CARTEIRA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_pct(valor: float) -> str:
    """+3,21% / -0,87%, com vírgula de decimal como a gente escreve no Brasil."""
    return f"{valor:+.2f}%".replace(".", ",")


def fmt_reais(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------ Rankings (brapi)

def _busca_lista_brapi(type_: str, limit: int, sub_type: str | None = None) -> list[dict]:
    """Busca os `limit` ativos mais líquidos (ordenados por volume) de um tipo.
    A ordenação por variação é feita aqui no código, em cima desse universo."""
    if not BRAPI_TOKEN:
        print("BRAPI_TOKEN não configurado no workflow do digest — pulando rankings da B3.")
        return []
    params = {"sortBy": "volume", "sortOrder": "desc", "limit": limit, "type": type_}
    if sub_type:
        params["subType"] = sub_type
    resp = requests.get(
        "https://brapi.dev/api/quote/list",
        params=params,
        headers={"Authorization": f"Bearer {BRAPI_TOKEN}"},
        timeout=20,
    )
    resp.raise_for_status()
    ativos = resp.json().get("stocks", [])
    # filtra item sem variação ou com valor absurdo (glitch de dado acontece)
    return [
        a for a in ativos
        if isinstance(a.get("change"), (int, float)) and abs(a["change"]) < 60
    ]


def _linhas_ranking(ativos: list[dict]) -> list[str]:
    linhas = []
    for a in ativos:
        preco = a.get("close")
        preco_txt = f" ({fmt_reais(preco)})" if isinstance(preco, (int, float)) else ""
        linhas.append(f"• {escape_html(a.get('stock', '?'))} {fmt_pct(a['change'])}{preco_txt}")
    return linhas


def secao_rankings_b3() -> list[str]:
    """Maiores altas/baixas entre as ações e FIIs mais líquidos, último pregão."""
    linhas: list[str] = []
    for rotulo, type_, sub_type, universo in [
        ("ações", "stock", None, UNIVERSO_ACOES),
        ("FIIs", "fund", "fii", UNIVERSO_FIIS),
    ]:
        try:
            ativos = _busca_lista_brapi(type_, universo, sub_type)
        except Exception as exc:
            print(f"Falha buscando ranking de {rotulo} na brapi: {exc}")
            continue
        if not ativos:
            continue
        ordenado = sorted(ativos, key=lambda a: a["change"], reverse=True)
        linhas.append(f"📈 <b>Maiores altas — {rotulo}:</b>")
        linhas.extend(_linhas_ranking(ordenado[:TOP_N]))
        linhas.append(f"📉 <b>Maiores baixas — {rotulo}:</b>")
        linhas.extend(_linhas_ranking(list(reversed(ordenado[-TOP_N:]))))
        linhas.append("")
    return linhas


# ------------------------------------------- Índices mundiais e cripto (yfinance)

def _variacao_yfinance(simbolo: str):
    """Variação do último fechamento vs o anterior, via Yahoo Finance.
    Devolve (variacao_pct, ultimo_fechamento) ou None se não der."""
    import yfinance as yf  # import aqui dentro: se a lib não estiver
    #                        instalada/funcionando, só essa seção é perdida

    hist = yf.Ticker(simbolo).history(period="5d", interval="1d")
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        return None
    anterior, ultimo = float(closes.iloc[-2]), float(closes.iloc[-1])
    if anterior == 0:
        return None
    return (ultimo / anterior - 1) * 100, ultimo


def secao_mundo_e_cripto() -> list[str]:
    linhas: list[str] = []
    mundo = []
    for simbolo, nome in INDICES_MUNDO.items():
        try:
            resultado = _variacao_yfinance(simbolo)
        except Exception as exc:
            print(f"Falha buscando {simbolo} no Yahoo Finance: {exc}")
            continue
        if resultado:
            mundo.append(f"• {nome}: {fmt_pct(resultado[0])}")
    if mundo:
        linhas.append("🌎 <b>Mundo (último fechamento):</b>")
        linhas.extend(mundo)

    cripto = []
    for simbolo, nome in CRIPTO.items():
        try:
            resultado = _variacao_yfinance(simbolo)
        except Exception as exc:
            print(f"Falha buscando {simbolo} no Yahoo Finance: {exc}")
            continue
        if resultado:
            var, preco = resultado
            cripto.append(f"• {nome}: {fmt_pct(var)} (US$ {preco:,.0f})")
    if cripto:
        linhas.append("🪙 <b>Cripto (últimas 24h de fechamento diário):</b>")
        linhas.extend(cripto)

    if linhas:
        linhas.append("")
    return linhas


# --------------------------------------------------- Proventos da carteira

def secao_proventos_carteira(carteira: dict) -> list[str]:
    """Último provento pago e dividend yield dos últimos 12 meses pra cada
    ativo DE BOLSA da carteira (os sem ticker público não têm esse dado)."""
    tickers = [p["ticker"] for p in carteira.get("posicoes_b3", []) if p.get("ticker")]
    if not tickers:
        return []

    import yfinance as yf

    linhas = []
    for ticker in tickers:
        try:
            t = yf.Ticker(f"{ticker}.SA")
            divs = t.dividends
            if divs is None or divs.empty:
                continue
            ultimo_valor = float(divs.iloc[-1])
            ultima_data = divs.index[-1]
            corte_12m = ultima_data - timedelta(days=365)
            soma_12m = float(divs[divs.index >= corte_12m].sum())

            hist = t.history(period="5d", interval="1d")
            closes = hist["Close"].dropna()
            dy_txt = ""
            if len(closes) and float(closes.iloc[-1]) > 0:
                dy = soma_12m / float(closes.iloc[-1]) * 100
                dy_txt = f" · DY 12m ~{dy:.1f}%".replace(".", ",")

            linhas.append(
                f"• {ticker}: último provento {fmt_reais(ultimo_valor)}/cota "
                f"em {ultima_data.strftime('%d/%m')}{dy_txt}"
            )
        except Exception as exc:
            print(f"Falha buscando proventos de {ticker}: {exc}")

    if not linhas:
        return []
    return ["💰 <b>Proventos da sua carteira:</b>"] + linhas + [""]


# ----------------------------------------------------------------- Montagem

def monta_radar(carteira: dict | None = None) -> str:
    """Monta a mensagem completa do radar (HTML pro Telegram). Devolve string
    vazia se NENHUMA seção funcionou — nesse caso o digest simplesmente não
    manda essa mensagem, sem quebrar nada."""
    if carteira is None:
        carteira = carrega_carteira()

    hoje = datetime.now().strftime("%d/%m")
    secoes: list[str] = []
    # cada seção falha ISOLADA: se o yfinance estiver bloqueado, por exemplo,
    # os rankings da brapi ainda saem (e vice-versa)
    for nome, secao in [
        ("rankings B3", secao_rankings_b3),
        ("mundo/cripto", secao_mundo_e_cripto),
        ("proventos", lambda: secao_proventos_carteira(carteira)),
    ]:
        try:
            secoes.extend(secao())
        except Exception as exc:
            print(f"Seção '{nome}' do radar falhou ({exc}) — omitida.")

    if not secoes:
        return ""

    cabecalho = [
        f"📊 <b>Radar do Mercado</b> · {hoje}",
        "<i>Ações e FIIs: variação do fechamento do último pregão (a B3 ainda não abriu).</i>",
        "",
    ]
    corpo = "\n".join(cabecalho + secoes).rstrip()
    return corpo


def radar_sem_html(texto: str) -> str:
    """Versão sem tags HTML, pra guardar no ultimo_digest.json — assim o
    chat_bot consegue responder 'o que mais subiu hoje?' com esses dados."""
    return re.sub(r"<[^>]+>", "", texto)


if __name__ == "__main__":
    # teste local: python radar_mercado.py (precisa de BRAPI_TOKEN no .env/env)
    from dotenv import load_dotenv

    load_dotenv()
    BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "")
    print(monta_radar())
