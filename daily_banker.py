"""
Daily Banker — a camada de "assistente de banker" do bot. Roda todo dia útil
junto com o digest (passo próprio no main.yml) e manda mensagem APENAS
quando tem algo a dizer — banker bom não faz ruído:

  1. AGENDA DA SEMANA (toda segunda): Copom, Fed (FOMC) e divulgação do
     IPCA nos próximos 7 dias — pra nenhuma quarta-feira de decisão de
     juros te pegar de surpresa.
  2. ALERTA DE VENCIMENTO: cada posição de renda fixa avisa 30 e 7 dias
     antes de vencer (uma vez por marco) — gestão de liquidez básica de
     qualquer assistente.
  3. ALOCAÇÃO vs ALVO: você define o alvo no secret (ex: 75% RF / 25% RV,
     banda de ±5 p.p.); quando o mercado te desalinha além da banda, o bot
     avisa (no máximo 1 vez por semana, pra não virar spam).
  4. ÍNDICE DA CARTEIRA (todo dia, silencioso): registra a rentabilidade
     como índice base-100 sobre o CUSTO (valores aplicados) em
     estado_banker.json — são só percentuais, por isso PODE ser commitado
     no repositório público sem expor patrimônio.
  5. RELATÓRIO MENSAL (1º dia útil do mês): quanto a carteira rendeu no mês
     vs CDI do mesmo período.

Limitações honestas, por design:
  - O índice parte do custo informado no secret; aporte/resgate muda o custo
    e dá um degrau no índice (sem ajuste de fluxo de caixa na v1).
  - Pra renda variável entrar no índice, a posição precisa de "preco_medio"
    no secret (sem ele, índice e relatório cobrem só a renda fixa, e o
    script avisa no log).
  - Nada aqui recomenda comprar ou vender — aponta desvio e evento; a
    decisão é sua.

Reusa as funções de dados do carteira_excel.py (carteira privada, fatores
IPCA/CDI do BCB, cotações) — mesma fonte, mesmos números da planilha.
"""

import json
import os
from datetime import date, datetime, timedelta

import requests
from dotenv import load_dotenv

import carteira_excel as ce

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ESTADO_PATH = "estado_banker.json"
MARCOS_VENCIMENTO_DIAS = [30, 7]
COOLDOWN_ALERTA_ALOCACAO_DIAS = 7

# ---------------------------------------------------------------- Agenda
# Datas VERIFICADAS em 29/10/2026:
#   - Copom 2026: calendário divulgado pelo BCB (via InfoMoney).
#   - FOMC 2026 + 1ª de 2027: página oficial do Federal Reserve.
#   - IPCA 2026: calendário do IBGE via fonte secundária — confira em
#     ibge.gov.br se uma data parecer errada.
# MANUTENÇÃO: quando o último evento passar, o bot manda UMA mensagem
# pedindo pra atualizar esta lista (os calendários do ano seguinte saem
# no meio do ano anterior).
AGENDA = [
    ("2026-11-03", "Reunião do Copom começa (decisão da Selic amanhã)"),
    ("2026-11-04", "Decisão do Copom sobre a Selic"),
    ("2026-11-12", "IBGE divulga o IPCA de outubro"),
    ("2026-12-08", "Começam Copom e Fed (FOMC) — decisões de juros amanhã"),
    ("2026-12-09", "Decisão do Copom (Selic) e do Fed (juros dos EUA) no mesmo dia"),
    ("2026-12-11", "IBGE divulga o IPCA de novembro"),
    ("2027-01-26", "Reunião do Fed (FOMC) começa"),
    ("2027-01-27", "Decisão do Fed sobre juros dos EUA"),
]


DIAS_SEMANA_PT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def hoje_local() -> date:
    return date.today()


def fmt_pct_br(valor: float, casas: int = 1) -> str:
    return f"{valor:.{casas}f}%".replace(".", ",")


def dias_uteis_entre(inicio: date, fim: date) -> int:
    """Dias úteis (seg-sex) com o dia inicial valendo zero — mesma convenção
    da planilha (NETWORKDAYS(...)-1). Aproximação sem feriados."""
    if fim <= inicio:
        return 0
    total, d = 0, inicio
    while d < fim:
        d += timedelta(days=1)
        if d.weekday() < 5:
            total += 1
    return total


# ---------------------------------------------------------------- Estado

def carrega_estado() -> dict:
    try:
        with open(ESTADO_PATH, encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def salva_estado(estado: dict) -> None:
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# ---------------------------------------------- Valor da carteira (de hoje)

def valor_renda_fixa(carteira: dict, hoje: date) -> tuple[float, float]:
    """(valor atual, custo) da renda fixa — mesmo cálculo da planilha."""
    atual = custo = 0.0
    for pos in carteira.get("renda_fixa", []):
        aplic = datetime.strptime(pos["data_aplicacao"], "%Y-%m-%d").date()
        aplicado = float(pos["valor_aplicado"])
        custo += aplicado
        indexador = pos["indexador"].lower()
        du = dias_uteis_entre(aplic, hoje)
        try:
            if indexador == "ipca+":
                fator, _ = ce.fator_ipca_acumulado(aplic)
                atual += aplicado * fator * (1 + pos.get("taxa_aa", 0) / 100) ** (du / 252)
            elif indexador == "cdi":
                fator, _ = ce.fator_cdi_acumulado(aplic, pos.get("percentual_cdi", 100))
                atual += aplicado * fator
            else:  # prefixado
                atual += aplicado * (1 + pos.get("taxa_aa", 0) / 100) ** (du / 252)
        except Exception as exc:
            print(f"Índice indisponível pra {pos['nome']} ({exc}) — usando valor sem correção.")
            atual += aplicado
    return atual, custo


def valor_renda_variavel(carteira: dict) -> tuple[float | None, float | None]:
    """(valor atual, custo) da renda variável. Valor exige cotação do dia;
    custo exige preco_medio no secret. Qualquer um pode vir None."""
    atual, custo = 0.0, 0.0
    tem_custo = True
    for pos in carteira.get("renda_variavel", []):
        q = ce.cotacao(pos["ticker"])
        if not q or not isinstance(q.get("preco"), (int, float)):
            print(f"Sem cotação pra {pos['ticker']} — alocação/índice ficam pra amanhã.")
            return None, None
        atual += pos.get("quantidade", 0) * q["preco"]
        if pos.get("preco_medio") is not None:
            custo += pos.get("quantidade", 0) * float(pos["preco_medio"])
        else:
            tem_custo = False
    return atual, (custo if tem_custo else None)


# ------------------------------------------------------------- Mensagens

def msg_agenda_semana(hoje: date, estado: dict) -> str | None:
    """Segunda-feira: eventos dos próximos 7 dias. Se o calendário embutido
    acabou, avisa UMA vez pra atualizar a lista AGENDA no código."""
    ultima_data = max(datetime.strptime(d, "%Y-%m-%d").date() for d, _ in AGENDA)
    if hoje > ultima_data and estado.get("aviso_agenda_vencida") != str(hoje.year):
        estado["aviso_agenda_vencida"] = str(hoje.year)
        return (
            "🗓️ <b>Manutenção:</b> o calendário de eventos do daily_banker.py acabou — "
            "adicione as datas novas de Copom, FOMC e IPCA na lista AGENDA "
            "(os calendários do ano seguinte saem no meio do ano)."
        )
    if hoje.weekday() != 0:  # só segunda
        return None
    fim = hoje + timedelta(days=7)
    eventos = [
        (datetime.strptime(d, "%Y-%m-%d").date(), label)
        for d, label in AGENDA
        if hoje <= datetime.strptime(d, "%Y-%m-%d").date() < fim
    ]
    if not eventos:
        return None
    linhas = [
        f"• {DIAS_SEMANA_PT[d.weekday()]} {d.strftime('%d/%m')}: {label}"
        for d, label in sorted(eventos)
    ]
    return "🗓️ <b>Sua semana no mercado</b>\n" + "\n".join(linhas)


def msgs_vencimento(carteira: dict, hoje: date, estado: dict) -> list[str]:
    avisados = estado.setdefault("vencimentos_avisados", {})
    mensagens = []
    for pos in carteira.get("renda_fixa", []):
        if not pos.get("vencimento"):
            continue
        venc = datetime.strptime(pos["vencimento"], "%Y-%m-%d").date()
        dias = (venc - hoje).days
        if dias < 0:
            continue
        ja = avisados.setdefault(pos["nome"], [])
        for marco in MARCOS_VENCIMENTO_DIAS:
            if dias <= marco and marco not in ja:
                ja.append(marco)
                mensagens.append(
                    f"⏳ <b>Vencimento chegando:</b> {pos['nome']} vence em "
                    f"{dias} dia(s) ({venc.strftime('%d/%m/%Y')}). Vale já pensar no "
                    f"reinvestimento — dinheiro parado em conta rende zero."
                )
                break  # um aviso por posição por dia basta
    return mensagens


def msg_alocacao(carteira: dict, rf_atual: float, rv_atual: float | None,
                 hoje: date, estado: dict) -> str | None:
    alvo = carteira.get("alocacao_alvo")
    if not alvo or rv_atual is None:
        return None
    total = rf_atual + rv_atual
    if total <= 0:
        return None
    rv_pct = rv_atual / total * 100
    rf_pct = rf_atual / total * 100
    estado["alocacao_atual"] = {"renda_fixa": round(rf_pct, 1), "renda_variavel": round(rv_pct, 1),
                                "em": str(hoje)}
    banda = alvo.get("banda_pp", 5)
    desvio_rv = rv_pct - alvo.get("renda_variavel", 0)
    if abs(desvio_rv) <= banda:
        return None
    ultimo = estado.get("ultimo_alerta_alocacao")
    if ultimo and (hoje - datetime.strptime(ultimo, "%Y-%m-%d").date()).days < COOLDOWN_ALERTA_ALOCACAO_DIAS:
        return None
    estado["ultimo_alerta_alocacao"] = str(hoje)
    direcao = "acima" if desvio_rv > 0 else "abaixo"
    return (
        f"⚖️ <b>Alocação fora da banda:</b> renda variável está em {fmt_pct_br(rv_pct)} da "
        f"carteira, {direcao} do alvo de {alvo.get('renda_variavel')}% (banda ±{banda} p.p.). "
        f"Renda fixa: {fmt_pct_br(rf_pct)} vs alvo {alvo.get('renda_fixa')}%. "
        f"Não é ordem de compra/venda — é o desvio que um rebalanceamento corrigiria; a decisão é sua."
    )


def registra_indice(rf_atual: float, rf_custo: float, rv_atual: float | None,
                    rv_custo: float | None, hoje: date, estado: dict) -> None:
    """Índice base-100 sobre o custo. Só percentual — seguro pro repo público."""
    if rv_atual is not None and rv_custo is not None:
        atual, custo, escopo = rf_atual + rv_atual, rf_custo + rv_custo, "carteira completa"
    else:
        atual, custo, escopo = rf_atual, rf_custo, "só renda fixa (adicione preco_medio no secret pra incluir a RV)"
    if custo <= 0:
        return
    historico = estado.setdefault("indice", {})
    historico[str(hoje)] = round(atual / custo * 100, 4)
    estado["indice_escopo"] = escopo
    # poda: guarda ~2 anos de pontos
    if len(historico) > 520:
        for k in sorted(historico)[: len(historico) - 520]:
            del historico[k]


def _cdi_acumulado_mes(ano: int, mes: int) -> float | None:
    """CDI acumulado (%) de um mês fechado, via série SGS 12 do BCB."""
    try:
        inicio = date(ano, mes, 1)
        dados = ce._sgs(ce.SGS_CDI_DIARIO, data_inicial=inicio)
        fator = 1.0
        for d in dados:
            dia = datetime.strptime(d["data"], "%d/%m/%Y").date()
            if dia.year == ano and dia.month == mes:
                fator *= 1 + float(d["valor"]) / 100
        return (fator - 1) * 100
    except Exception as exc:
        print(f"CDI do mês {mes}/{ano} indisponível: {exc}")
        return None


def msg_relatorio_mensal(hoje: date, estado: dict) -> str | None:
    """1º dia útil do mês: rendimento do mês fechado vs CDI do mesmo mês."""
    if hoje.weekday() >= 5 or hoje.day > 3:
        return None
    mes_ref = (hoje.replace(day=1) - timedelta(days=1))  # último dia do mês anterior
    chave_mes = mes_ref.strftime("%Y-%m")
    if estado.get("ultimo_relatorio_mensal") == chave_mes:
        return None
    historico = estado.get("indice", {})
    no_mes = sorted(k for k in historico if k.startswith(chave_mes))
    mes_anterior = (mes_ref.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    base_candidatas = sorted(k for k in historico if k.startswith(mes_anterior))
    if not no_mes or not base_candidatas:
        if no_mes:  # tem dados do mês mas não do anterior: histórico ainda curto
            estado["ultimo_relatorio_mensal"] = chave_mes
            return (
                f"📈 <b>Fechamento de {mes_ref.strftime('%m/%Y')}:</b> ainda estou construindo "
                f"o histórico da carteira (começei a medir este mês) — o primeiro comparativo "
                f"completo contra o CDI sai no fechamento que vem."
            )
        return None
    estado["ultimo_relatorio_mensal"] = chave_mes
    indice_fim = historico[no_mes[-1]]
    indice_base = historico[base_candidatas[-1]]
    retorno = (indice_fim / indice_base - 1) * 100
    cdi = _cdi_acumulado_mes(mes_ref.year, mes_ref.month)
    cdi_txt = f" vs CDI {cdi:+.2f}%".replace(".", ",") if cdi is not None else " (CDI do mês indisponível hoje)"
    escopo = estado.get("indice_escopo", "")
    return (
        f"📈 <b>Fechamento de {mes_ref.strftime('%m/%Y')}:</b> sua carteira variou "
        f"{retorno:+.2f}%".replace(".", ",") + cdi_txt +
        f". Escopo: {escopo}. Lembrete: aportes/resgates no período distorcem essa conta."
    )


# ---------------------------------------------------------------- Envio

def envia(texto: str) -> None:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("(modo teste, sem Telegram):\n" + texto)
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": texto[:4096], "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    if not resp.ok:
        print("Erro enviando mensagem do daily banker:", resp.text)


def main() -> None:
    hoje = hoje_local()
    carteira = ce.carrega_carteira_privada()
    estado = carrega_estado()

    mensagens: list[str] = []

    m = msg_agenda_semana(hoje, estado)
    if m:
        mensagens.append(m)

    mensagens.extend(msgs_vencimento(carteira, hoje, estado))

    rf_atual, rf_custo = valor_renda_fixa(carteira, hoje)
    rv_atual, rv_custo = valor_renda_variavel(carteira)

    m = msg_alocacao(carteira, rf_atual, rv_atual, hoje, estado)
    if m:
        mensagens.append(m)

    registra_indice(rf_atual, rf_custo, rv_atual, rv_custo, hoje, estado)

    m = msg_relatorio_mensal(hoje, estado)
    if m:
        mensagens.append(m)

    salva_estado(estado)

    if not mensagens:
        print("Daily banker: nada digno de mensagem hoje (isso é bom sinal).")
    for texto in mensagens:
        envia(texto)

    print(f"Daily banker: {len(mensagens)} mensagem(ns); índice registrado pra {hoje}.")


if __name__ == "__main__":
    main()
