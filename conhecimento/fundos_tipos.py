"""
Classificação dos fundos listados mais líquidos da B3 — tipo de cada um.

Por que isso existe: quando perguntaram ao chat "quais tipos de FII estão
subindo?", ele respondeu ensinando a consultar a B3 — porque não TINHA o
dado. Nenhuma API gratuita entrega a categoria (papel/tijolo/híbrido/FOF)
de forma estruturada, então a solução honesta é um catálogo CURADO: só
entram fundos cuja classificação é estável e conhecida. Fundo fora da
lista aparece sem anotação no radar, e o chat é instruído a dizer "tipo
não catalogado" em vez de adivinhar.

Manutenção: classificação de FII muda raramente (um fundo de logística não
vira FOF), mas fundos novos surgem — adicione aqui conforme aparecerem nos
seus radares. Formato: ticker -> tipo curto.
"""

TIPOS_FUNDOS = {
    # FIIs de papel (CRI/recebíveis — sensíveis à curva de juros)
    "KNCR11": "papel (CDI)", "KNIP11": "papel (IPCA)", "KNSC11": "papel",
    "KNHY11": "papel (high yield)", "RBRR11": "papel", "VGIR11": "papel (CDI)",
    "VRTA11": "papel", "HGCR11": "papel", "CPTS11": "papel", "RECR11": "papel",
    "IRDM11": "papel", "MXRF11": "papel/híbrido", "ARRI11": "papel",
    "DEVA11": "papel (high yield)", "HCTR11": "papel (high yield)",
    "VCJR11": "papel (juros real)", "URPR11": "papel (high yield)",
    "VGIP11": "papel (IPCA)", "CVBI11": "papel", "BTCI11": "papel",

    # FIIs de tijolo — logística/industrial
    "HGLG11": "tijolo: logística", "XPLG11": "tijolo: logística",
    "VILG11": "tijolo: logística", "BTLG11": "tijolo: logística",
    "LVBI11": "tijolo: logística", "BRCO11": "tijolo: logística",
    "GGRC11": "tijolo: logística", "PATL11": "tijolo: logística",

    # FIIs de tijolo — shoppings
    "XPML11": "tijolo: shoppings", "VISC11": "tijolo: shoppings",
    "HSML11": "tijolo: shoppings", "MALL11": "tijolo: shoppings",
    "HGBS11": "tijolo: shoppings",

    # FIIs de tijolo — lajes corporativas
    "HGRE11": "tijolo: lajes", "PVBI11": "tijolo: lajes",
    "RCRB11": "tijolo: lajes", "BRCR11": "tijolo: lajes",
    "TEPP11": "tijolo: lajes", "VINO11": "tijolo: lajes",
    "JSRE11": "tijolo: lajes/híbrido",

    # FIIs híbridos e renda urbana
    "KNRI11": "híbrido (lajes+logística)", "HGRU11": "renda urbana",
    "TRXF11": "renda urbana", "RBRP11": "híbrido",

    # FOFs (fundos de fundos)
    "HFOF11": "FOF", "BCFF11": "FOF", "RBRF11": "FOF", "KFOF11": "FOF",

    # Fiagro (crédito do agronegócio — CRAs)
    "KNCA11": "Fiagro (CRA)", "CPTR11": "Fiagro (CRA)", "VGIA11": "Fiagro (CRA)",
    "RURA11": "Fiagro (CRA)", "AAZQ11": "Fiagro (CRA)",

    # FI-Infra (debêntures incentivadas de infraestrutura — rendimentos isentos)
    "KDIF11": "FI-Infra", "JURO11": "FI-Infra", "IFRA11": "FI-Infra",
    "BDIV11": "FI-Infra",
}


def tipo_do_fundo(ticker: str) -> str | None:
    return TIPOS_FUNDOS.get(ticker.upper())


def texto_tipos_para_chat() -> str:
    """Versão compacta pro prompt do chat, agrupada por tipo."""
    grupos: dict[str, list[str]] = {}
    for ticker, tipo in TIPOS_FUNDOS.items():
        chave = tipo.split(" (")[0].split(":")[0].strip()  # papel, tijolo, híbrido, FOF, Fiagro, FI-Infra, renda urbana
        grupos.setdefault(chave, []).append(ticker)
    linhas = [f"- {tipo}: {', '.join(sorted(tickers))}" for tipo, tickers in sorted(grupos.items())]
    return (
        "Tipos dos fundos listados mais líquidos da B3 (catálogo curado — use pra "
        "responder perguntas como 'quais tipos de FII estão subindo?' cruzando com o radar do dia; "
        "fundo que NÃO estiver nesta lista: diga que o tipo não está catalogado, nunca adivinhe):\n"
        + "\n".join(linhas)
    )