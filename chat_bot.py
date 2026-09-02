"""
Chat sobre o digest do dia — roda via GitHub Actions, disparado pelo
cron-job.org (não pelo agendamento interno do GitHub, que provou ser pouco
confiável — ver README). Não é um servidor sempre ligado, então a resposta
não é instantânea; o atraso máximo é o intervalo entre disparos do
cron-job.org (2 min, por padrão).

Como funciona:
  1. Pergunta pro Telegram se tem mensagem nova desde a última checada (o
     próprio Telegram guarda essa fila — não precisamos persistir estado
     entre execuções pra isso).
  2. Ignora mensagem de chat não reconhecido (só responde no chat padrão ou
     nos grupos configurados em TELEGRAM_CATEGORY_CHAT_IDS) — evita gastar
     chamada de API se alguém de fora achar o bot.
  3. Pra cada mensagem válida, monta o contexto com o digest do dia (lido
     de ultimo_digest.json) e manda pra Groq responder — cada pergunta é
     tratada isolada, sem lembrar de mensagens anteriores da conversa.
  4. Manda a resposta de volta no mesmo chat.

Variáveis de ambiente necessárias:
  GROQ_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
  TELEGRAM_CATEGORY_CHAT_IDS (opcional)
"""

import json
import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CATEGORY_CHAT_IDS = json.loads(os.environ.get("TELEGRAM_CATEGORY_CHAT_IDS") or "{}")

if not GROQ_API_KEY:
    raise SystemExit("GROQ_API_KEY está vazio ou não configurado.")
if not TELEGRAM_BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN está vazio ou não configurado.")

groq_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
GROQ_MODEL = "qwen/qwen3.6-27b"  # mesmo modelo do market_digest.py — ver comentários lá
DIGEST_PATH = "ultimo_digest.json"

# Só responde em chats reconhecidos — evita gastar chamada de API se alguém
# de fora encontrar o bot e mandar mensagem.
CHATS_PERMITIDOS = {str(v) for v in CATEGORY_CHAT_IDS.values()}
if DEFAULT_CHAT_ID:
    CHATS_PERMITIDOS.add(str(DEFAULT_CHAT_ID))


def carrega_digest() -> dict:
    if not os.path.exists(DIGEST_PATH):
        return {"itens": []}
    with open(DIGEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def busca_mensagens_novas() -> list[dict]:
    resp = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
        params={"timeout": 0},
        timeout=30,
    )
    resp.raise_for_status()
    updates = resp.json().get("result", [])
    mensagens = [u["message"] for u in updates if "message" in u and "text" in u.get("message", {})]

    # confirma pro Telegram que já processamos até aqui — senão a mesma
    # mensagem reaparece na próxima checada (o Telegram é quem guarda essa
    # fila, então isso substitui precisar persistir estado entre execuções)
    if updates:
        maior_update_id = max(u["update_id"] for u in updates)
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": maior_update_id + 1, "timeout": 0},
            timeout=30,
        )

    return mensagens


def monta_contexto_digest(digest: dict) -> str:
    itens = digest.get("itens", [])
    if not itens:
        return "Nenhuma notícia no digest de hoje ainda."
    blocos = []
    for it in itens:
        nums = it.get("key_numbers") or []
        linha_nums = f"  Números: {' · '.join(nums)}\n" if nums else ""
        blocos.append(
            f"- {it.get('title')} ({it.get('source')})\n"
            f"  Resumo: {it.get('resumo')}\n"
            f"{linha_nums}"
            f"  Mecanismo: {it.get('mecanismo')}\n"
            f"  Implicação: {it.get('implicacao')}\n"
            f"  Risco: {it.get('risco')}\n"
            f"  De olho em: {it.get('proximo_evento')}"
        )
    return "\n\n".join(blocos)


# ------------------------------------------------- Indicadores de mercado (BCB)

# Séries do SGS (Sistema Gerenciador de Séries Temporais) do Banco Central —
# API pública, gratuita e sem chave.
SERIES_SGS = {
    1: "Dólar (PTAX venda, R$/US$)",
    432: "Meta Selic (% a.a.)",
}


def fetch_indicadores_mercado() -> str:
    """Busca o valor mais recente de indicadores-chave direto do Banco
    Central. Sem isso, o modelo responde sobre "juros subindo/caindo" ou
    "dólar forte/fraco" pela memória de padrões do treinamento, e já erramos
    a DIREÇÃO de indicador por causa disso — com o número na frente, ele não
    precisa adivinhar."""
    linhas = []
    for codigo, rotulo in SERIES_SGS.items():
        try:
            resp = requests.get(
                f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/1",
                params={"formato": "json"},
                timeout=15,
            )
            resp.raise_for_status()
            dados = resp.json()
            if dados:
                d = dados[-1]
                linhas.append(f"- {rotulo}: {d['valor']} (em {d['data']})")
        except Exception as exc:
            print(f"Falha buscando série {codigo} no BCB: {exc}")

    if not linhas:
        return ""
    return "Indicadores oficiais mais recentes (Banco Central):\n" + "\n".join(linhas)


MESES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def data_hoje_pt() -> str:
    """Monta a data em português sem depender de locale do sistema (que pode
    não estar disponível/configurado igual no GitHub Actions)."""
    agora = datetime.now()
    return f"{agora.day} de {MESES_PT[agora.month - 1]} de {agora.year}"


def responde(pergunta: str, digest: dict, indicadores: str = "") -> str:
    contexto_digest = monta_contexto_digest(digest)
    hoje = data_hoje_pt()
    bloco_indicadores = f"\n{indicadores}\n" if indicadores else ""

    prompt = f"""Você é um analista de mercado conversando com um investidor de
curto/médio prazo sobre o resumo de notícias que ele recebeu hoje. Responda
sempre em português do Brasil, em texto corrido normal (nada de JSON aqui).
{bloco_indicadores}
A data de hoje é {hoje}. Trate isso como o presente real — seu conhecimento
de treinamento pode ter um corte anterior a essa data, então NUNCA assuma
que uma notícia, lei ou evento datado de {datetime.now().year} é "futuro",
"hipotético" ou "erro de data" só porque parece posterior ao que você
aprendeu. Se a notícia do digest afirma algo com essa data, trate como fato
atual, não como projeção.

Notícias do digest de hoje:
{contexto_digest}

Pergunta da pessoa: {pergunta}

Regras importantes:
- REGRA CRÍTICA — nunca afirme a DIREÇÃO de um indicador (subiu, caiu, está
  em alta, está em baixa) que não esteja explícito no material acima. Isso
  vale especialmente pra juros americanos (Treasuries), petróleo, índices
  de Wall Street, dólar e commodities. Não complete com o que "normalmente"
  acontece nem com o padrão clássico que você aprendeu: se o dado não está
  acima, diga que não tem esse dado no material de hoje e siga sem ele. É
  melhor responder "não tenho a direção dos juros americanos hoje" do que
  chutar — um erro de direção inverte todo o raciocínio e já aconteceu aqui.
- Cuidado especial com raciocínio de correlação clássica ("juros caindo lá
  fora → bolsa emergente sobe"). Mercados frequentemente destoam desse
  padrão: o Brasil pode subir com o exterior caindo, por motivos locais. Só
  afirme que dois mercados se moveram juntos se o material acima disser
  isso; caso contrário, trate os movimentos como independentes.
- Responda DIRETO à pergunta feita — não recapitule o digest inteiro antes
  se a pergunta for específica. Se a pessoa pergunta sobre um tema pontual
  (ex: FIIs, uma ação, um setor), vá direto nesse tema.
- Use as notícias acima quando forem relevantes pra pergunta — não é
  obrigatório usar tudo, nem citar tudo.
- Se a pergunta não tiver relação com o digest, responda com seu
  conhecimento geral (conceitos e mecanismos gerais de economia são
  permitidos e úteis) — mas deixe claro que essa parte não veio do digest,
  e continue valendo a regra crítica acima sobre não inventar direção de
  indicador do dia.
- Fale de setor ou classe de ativo — nunca recomende comprar ou vender uma
  ação específica por nome; dê o raciocínio, não a ordem de compra.
- Seja conciso: 2 a 4 parágrafos curtos costuma bastar, a não ser que a
  pergunta peça claramente mais profundidade."""

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1000,
        extra_body={"reasoning_effort": "none"},
    )
    return response.choices[0].message.content.strip()


def envia_telegram(chat_id: str, texto: str) -> None:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": texto[:4096]},
        timeout=30,
    )
    if not resp.ok:
        print(f"Erro ao enviar resposta pro chat {chat_id}:", resp.text)


def main() -> None:
    mensagens = busca_mensagens_novas()
    if not mensagens:
        print("Nenhuma mensagem nova.")
        return

    digest = carrega_digest()
    indicadores = fetch_indicadores_mercado()
    print("Indicadores BCB:", "OK" if indicadores else "indisponíveis, seguindo sem eles")

    for msg in mensagens:
        chat_id = str(msg["chat"]["id"])
        texto = msg["text"]

        if CHATS_PERMITIDOS and chat_id not in CHATS_PERMITIDOS:
            print(f"Ignorando mensagem de chat não reconhecido ({chat_id}).")
            continue

        print(f"Pergunta recebida ({chat_id}): {texto[:60]}")
        try:
            resposta = responde(texto, digest, indicadores)
        except Exception as exc:
            resposta = f"Não consegui responder agora ({exc}). Tenta de novo daqui a pouco."

        envia_telegram(chat_id, resposta)
        time.sleep(1)


if __name__ == "__main__":
    main()