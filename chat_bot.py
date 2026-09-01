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
     de ultimo_digest.json) + o histórico da conversa (guardado no Upstash
     Redis, já que cada execução do GitHub Actions começa do zero e não
     lembra da execução anterior sozinha) e manda pra Groq responder.
  4. Manda a resposta de volta no mesmo chat, e salva a troca no histórico.

Variáveis de ambiente necessárias:
  GROQ_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
  TELEGRAM_CATEGORY_CHAT_IDS (opcional)
  UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN (opcional — sem isso o
  bot funciona igual, só sem lembrar de mensagens anteriores)
"""

import json
import os
import time

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CATEGORY_CHAT_IDS = json.loads(os.environ.get("TELEGRAM_CATEGORY_CHAT_IDS") or "{}")
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

if not GROQ_API_KEY:
    raise SystemExit("GROQ_API_KEY está vazio ou não configurado.")
if not TELEGRAM_BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN está vazio ou não configurado.")

groq_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
GROQ_MODEL = "qwen/qwen3.6-27b"  # mesmo modelo do market_digest.py — ver comentários lá
DIGEST_PATH = "ultimo_digest.json"

# Quantas trocas (pergunta+resposta) manter no histórico de cada conversa.
MAX_TROCAS_HISTORICO = 6

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
        blocos.append(
            f"- {it.get('title')} ({it.get('source')})\n"
            f"  Resumo: {it.get('resumo')}\n"
            f"  Mecanismo: {it.get('mecanismo')}\n"
            f"  Implicação: {it.get('implicacao')}\n"
            f"  De olho em: {it.get('proximo_evento')}"
        )
    return "\n\n".join(blocos)


# --------------------------------------------------------- Histórico (Upstash)

def historico_carrega(chat_id: str) -> list[dict]:
    """Pega as últimas mensagens dessa conversa. Se o Upstash não estiver
    configurado, ou der qualquer erro, devolve vazio — o bot ainda funciona,
    só sem lembrar de mensagens anteriores."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return []
    try:
        resp = requests.get(
            f"{UPSTASH_URL}/get/historico:{chat_id}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        valor = resp.json().get("result")
        return json.loads(valor) if valor else []
    except Exception as exc:
        print(f"Falha lendo histórico do Upstash ({chat_id}): {exc}")
        return []


def historico_salva(chat_id: str, historico: list[dict]) -> None:
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return
    historico = historico[-(MAX_TROCAS_HISTORICO * 2):]  # cada troca = 2 entradas (user+assistant)
    try:
        requests.post(
            f"{UPSTASH_URL}/set/historico:{chat_id}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            json=json.dumps(historico),  # valor precisa ir como string JSON
            timeout=10,
        )
    except Exception as exc:
        print(f"Falha salvando histórico no Upstash ({chat_id}): {exc}")


# ------------------------------------------------------------------- Resposta

def responde(pergunta: str, digest: dict, historico: list[dict]) -> str:
    contexto_digest = monta_contexto_digest(digest)

    system_prompt = f"""Você é um analista de mercado conversando por chat com um
investidor de curto/médio prazo. Responda sempre em português do Brasil, em
texto corrido normal (nada de JSON).

Regras importantes:
- Responda DIRETO à pergunta feita — não recapitule o digest inteiro antes
  se a pergunta for específica. Se a pessoa pergunta sobre um tema pontual
  (ex: FIIs, uma ação, um setor), vá direto nesse tema.
- Só traga o contexto do digest de hoje quando ele for relevante pra
  pergunta específica — não é obrigatório usar tudo, nem citar tudo.
- Se a pergunta não tiver relação com o digest, responda com seu
  conhecimento geral, deixando claro que essa parte não veio do digest.
- Fale de setor ou classe de ativo — nunca recomende comprar ou vender uma
  ação específica por nome; dê o raciocínio, não a ordem de compra.
- Seja conciso: 2 a 4 parágrafos curtos costuma bastar, a não ser que a
  pergunta peça claramente mais profundidade.
- Essa é uma conversa contínua — use as mensagens anteriores pra entender o
  contexto (ex: "aquela notícia" pode se referir a algo já mencionado antes).

Notícias do digest de hoje (use quando for relevante):
{contexto_digest}"""

    mensagens = [{"role": "system", "content": system_prompt}]
    mensagens.extend(historico)
    mensagens.append({"role": "user", "content": pergunta})

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=mensagens,
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

    for msg in mensagens:
        chat_id = str(msg["chat"]["id"])
        texto = msg["text"]

        if CHATS_PERMITIDOS and chat_id not in CHATS_PERMITIDOS:
            print(f"Ignorando mensagem de chat não reconhecido ({chat_id}).")
            continue

        print(f"Pergunta recebida ({chat_id}): {texto[:60]}")
        historico = historico_carrega(chat_id)
        try:
            resposta = responde(texto, digest, historico)
        except Exception as exc:
            resposta = f"Não consegui responder agora ({exc}). Tenta de novo daqui a pouco."
            envia_telegram(chat_id, resposta)
            time.sleep(1)
            continue

        envia_telegram(chat_id, resposta)
        historico.append({"role": "user", "content": texto})
        historico.append({"role": "assistant", "content": resposta})
        historico_salva(chat_id, historico)
        time.sleep(1)


if __name__ == "__main__":
    main()