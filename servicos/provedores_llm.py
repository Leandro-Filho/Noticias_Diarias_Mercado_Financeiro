"""
Camada multi-provedor de LLM — a resposta pro "quero um modelo melhor".

A ideia: em vez de depender de UM provedor, existe uma cadeia com dois
níveis de qualidade, e cada chamada declara o que precisa:

  qualidade="alta"  → tenta primeiro o GPT-5 (e depois o GPT-4.1) do GitHub
                      Models — modelos de fronteira da OpenAI, gratuitos com
                      conta GitHub, ~50 requisições/dia somadas — e, se não
                      estiverem configurados ou o limite do dia acabar, cai
                      pro OpenRouter (se configurado) e por fim pra Groq.
                      Usada onde a inteligência aparece: chat, /analise,
                      análises profundas do digest, alerta de carteira.
  qualidade="bulk"  → vai direto pra Groq (14.400 req/dia — a operária do
                      volume), com os provedores "alta" como último recurso
                      se a Groq inteira cair. Usada no trabalho mecânico:
                      triagem e menções rasas, onde o Qwen 27B dá conta e
                      não vale gastar o orçamento do GPT.

Nenhuma chave nova é OBRIGATÓRIA: provedor sem chave no ambiente é pulado.
Com só o GROQ_API_KEY de sempre, tudo continua funcionando como antes.
Pra ligar o GPT-5/4.1: fine-grained token no GitHub (Settings → Developer
settings → Fine-grained tokens) com a permissão de CONTA "Models: read"
(fica em Account permissions, não em Repository permissions), salvo como
secret GH_MODELS_TOKEN (nome de secret não pode começar com GITHUB_). Os
workflows mapeiam ele pra variável GITHUB_MODELS_TOKEN.

Limites que valem saber do tier gratuito do GPT-5/GPT-4.1: ~50 req/dia
(somadas), 10 req/min, prompt de até ~8K tokens e saída de até ~4K. Nossos
prompts cabem; quando o dia estourar, a cadeia desce pra Groq
automaticamente e o bot não para. A conta do orçamento: digest nobre
(análises profundas + alerta) gasta ~13 de manhã, sobram ~37 pro chat e
pros /analise (4 chamadas cada) — mesma filosofia do fallback de modelos
da Groq, que já nos salvou de 3 descontinuações (gpt-oss com bug de JSON
garantido, kimi-k2 e qwen3.6 descontinuados — a última custou 7 dias de
bot mudo em setembro/2026).

Um aviso de termos de uso que importa pro futuro: o free tier do GitHub
Models é pra prototipagem/experimentação (uso pessoal como este). Virar
PRODUTO atendendo clientes com esse tier violaria os termos — nesse dia,
API paga entra no custo do negócio.
"""

import os
import time

from json_repair import repair_json
from openai import OpenAI

# Ordem importa: dentro de cada nível, o primeiro configurado é tentado antes.
PROVEDORES = [
    {
        # GPT-5 primeiro (mais forte), GPT-4.1 como segundo: os dois estão no
        # mesmo tier gratuito (~50 req/dia, 10/min) do GitHub Models, então se
        # o GPT-5 não existir pra sua conta, o 404 pula pro 4.1 sozinho.
        # NÃO coloque aqui os modelos "de raciocínio" do catálogo (DeepSeek-R1,
        # Grok-3): eles têm tier próprio de 8-15 req/dia — esgotam antes do
        # digest terminar. O endpoint antigo da Azure
        # (models.inference.ai.azure.com) foi DESATIVADO pelo GitHub em 2026,
        # por isso só existe este.
        "nome": "github-models",
        "base_url": "https://models.github.ai/inference",
        "key_env": "GITHUB_MODELS_TOKEN",
        "modelos": ["openai/gpt-5", "openai/gpt-4.1"],
        "nivel": "alta",
    },
    {
        # opcional: 50 req/dia gratuitas, vários modelos grandes. Os IDs
        # ":free" mudam conforme o catálogo — confira em openrouter.ai/models
        # se ambos derem 404 (o código só pula pro próximo, não quebra).
        "nome": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "modelos": ["deepseek/deepseek-chat-v3.1:free", "openai/gpt-oss-120b:free"],
        "nivel": "alta",
    },
    {
        "nome": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "modelos": [
            "qwen/qwen3.8-27b",
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-120b",
        ],
        "nivel": "bulk",
    },
]

# Modelos "de raciocínio" da Groq — recebem reasoning_effort="none" pra não
# gastarem o orçamento de saída "pensando". Só vale pra Groq: outros
# provedores podem rejeitar o parâmetro.
GROQ_REASONING_MODELS = {"qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"}

_clients: dict[str, OpenAI] = {}


def _client(prov: dict) -> OpenAI:
    if prov["nome"] not in _clients:
        _clients[prov["nome"]] = OpenAI(
            api_key=os.environ.get(prov["key_env"], ""), base_url=prov["base_url"]
        )
    return _clients[prov["nome"]]


def _cadeia(qualidade: str) -> list[dict]:
    """Provedores configurados (com chave no ambiente), na ordem certa pro
    nível pedido. 'alta' desce pra bulk no fim; 'bulk' sobe pra alta só em
    último caso (Groq fora do ar > resposta nenhuma)."""
    alta = [p for p in PROVEDORES if p["nivel"] == "alta"]
    bulk = [p for p in PROVEDORES if p["nivel"] == "bulk"]
    ordem = alta + bulk if qualidade == "alta" else bulk + alta
    return [p for p in ordem if os.environ.get(p["key_env"])]


def _pula_modelo(exc: Exception) -> bool:
    """Erro que não adianta repetir com o MESMO modelo (não existe/sem acesso)."""
    t = str(exc).lower()
    return "model_not_found" in t or "does not exist" in t or "unknown model" in t or "404" in t


def _pula_provedor(exc: Exception) -> bool:
    """Erro que condena o provedor inteiro por agora: limite do dia/minuto
    estourado (429) ou chave inválida (401/403). Ir pro próximo modelo do
    MESMO provedor não resolveria — pula direto pro provedor seguinte."""
    t = str(exc).lower()
    return "429" in t or "rate limit" in t or "quota" in t or "401" in t or "403" in t or "unauthorized" in t


def call_llm_text(
    prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.3,
    qualidade: str = "alta",
    tentativas_por_modelo: int = 2,
) -> str:
    """Texto corrido, com fallback entre provedores e modelos."""
    ultimo_erro: Exception | None = None
    cadeia = _cadeia(qualidade)
    if not cadeia:
        raise RuntimeError("Nenhum provedor de LLM configurado (nem GROQ_API_KEY).")

    for prov in cadeia:
        for modelo in prov["modelos"]:
            extra = (
                {"reasoning_effort": "none"}
                if prov["nome"] == "groq" and modelo in GROQ_REASONING_MODELS
                else {}
            )
            for tentativa in range(1, tentativas_por_modelo + 1):
                try:
                    resposta = _client(prov).chat.completions.create(
                        model=modelo,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                        max_tokens=max_tokens,
                        extra_body=extra,
                    )
                    return resposta.choices[0].message.content.strip()
                except Exception as exc:
                    ultimo_erro = exc
                    if _pula_provedor(exc):
                        print(f"[{prov['nome']}] indisponível agora ({exc}) — próximo provedor.")
                        break
                    if _pula_modelo(exc):
                        print(f"[{prov['nome']}] modelo {modelo} não existe ({exc}) — próximo modelo.")
                        break
                    print(f"[{prov['nome']}/{modelo}] tentativa {tentativa}/{tentativas_por_modelo} falhou ({exc}).")
                    time.sleep(2)
            else:
                continue  # esgotou tentativas transitórias desse modelo → próximo modelo
            if _pula_provedor(ultimo_erro):
                break  # abandona os outros modelos desse provedor também
    raise ultimo_erro


def call_llm_json(prompt: str, max_tokens: int = 1500, qualidade: str = "bulk", tentativas: int = 3) -> dict:
    """JSON extraído e reparado na mão. NÃO usamos response_format
    ("JSON garantido") de propósito: tem bug documentado no fórum da Groq
    nos modelos gpt-oss (json_validate_failed). Mesma cadeia de fallback do
    call_llm_text, com re-tentativa se a resposta vier sem JSON
    reconhecível."""
    prompt_reforcado = prompt + "\n\nResponda em português, SOMENTE com o JSON pedido, sem markdown, sem texto antes ou depois."
    ultimo_erro: Exception | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            raw = call_llm_text(prompt_reforcado, max_tokens=max_tokens, temperature=0, qualidade=qualidade)
            clean = raw.replace("```json", "").replace("```", "").strip()
            start, end = clean.find("{"), clean.rfind("}")
            if start == -1 or end == -1:
                raise ValueError(f"Resposta sem JSON reconhecível: {raw[:200]}")
            resultado = repair_json(clean[start : end + 1], return_objects=True)
            if not isinstance(resultado, dict):
                raise ValueError(f"JSON reparado não é um objeto: {resultado!r}")
            return resultado
        except Exception as exc:
            ultimo_erro = exc
            print(f"call_llm_json tentativa {tentativa}/{tentativas} falhou ({exc}).")
            time.sleep(2)
    raise ultimo_erro