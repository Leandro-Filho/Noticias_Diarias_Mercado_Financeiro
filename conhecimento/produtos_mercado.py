"""
Catálogo dos produtos de investimento do mercado brasileiro — a resposta
pro problema "o bot não sabe o que existe no mercado".

É TEXTO ESTÁTICO de propósito: o que cada produto é, como é tributado, se
tem FGC, liquidez e pra que serve são regras estruturais que mudam raramente
(anos). Conhecimento estável no prompt = zero risco de alucinação, enquanto
os NÚMEROS vivos (Selic, CDI, taxas do Tesouro do dia, cotações) continuam
vindo das APIs a cada conversa — o modelo junta os dois.

O que este catálogo NÃO cobre, por honestidade: a taxa do CDB/LCA que cada
banco está oferecendo HOJE — isso não existe em fonte pública gratuita (cada
corretora mostra só a própria prateleira). As referências vivas que o bot
tem são o Tesouro Direto do dia (no radar), a Selic e o CDI — e dá pra
comparar qualquer oferta que a pessoa receber contra elas.

Manutenção: se uma regra tributária mudar (ex: fim da isenção de LCA/LCI,
que já foi discutido em Brasília), atualize a linha aqui.
"""

CATALOGO_PRODUTOS = """Catálogo de produtos do mercado brasileiro (regras estruturais — use pra \
explicar, comparar e contextualizar; as taxas do dia vêm dos indicadores e do radar acima):

RENDA FIXA — títulos públicos:
- Tesouro Selic: pós-fixado na Selic, liquidez diária quase sem marcação a mercado; o lugar padrão de reserva de emergência. IR regressivo (22,5%→15%).
- Tesouro Prefixado: taxa travada até o vencimento; vender antes tem marcação a mercado (pode dar perda nominal). IR regressivo.
- Tesouro IPCA+: inflação + taxa real travada; protege poder de compra no vencimento; marcação a mercado forte em prazos longos. IR regressivo.

RENDA FIXA — bancária (todas com FGC até R$ 250 mil por emissor por CPF):
- CDB: % do CDI, prefixado ou IPCA+; liquidez conforme o papel; IR regressivo. Banco menor paga mais % do CDI justamente porque o risco é maior (o FGC é o colchão).
- LCI/LCA: lastro imobiliário/agro; ISENTAS de IR pra PF; costumam ter carência (90 dias+); por serem isentas, taxa menor que CDB equivale a líquido maior — comparação justa: taxa da LCA ÷ 0,85 vs CDB bruto (prazo >2 anos).
- LC e RDB: parecidos com CDB (LC de financeiras); mesmos impostos e FGC.

RENDA FIXA — crédito privado (SEM FGC — o risco é o emissor):
- Debênture comum: dívida de empresa; IR regressivo; spread sobre CDI ou IPCA+ paga o risco de crédito.
- Debênture incentivada (Lei 12.431, infraestrutura): ISENTA de IR pra PF; mesma lógica de risco.
- CRI/CRA: securitização imobiliária/agro; ISENTOS de IR pra PF; sem FGC; liquidez baixa no secundário.

FUNDOS:
- Fundo DI/Renda Fixa: cesta de títulos; taxa de adm corrói retorno; come-cotas em maio/novembro.
- Fundo de crédito privado: debêntures/CRIs na cesta; resgate costuma ter prazo (D+15, D+30...); come-cotas.
- Multimercado: liberdade de estratégia (juros, moedas, bolsa); retorno e risco dependem do gestor; come-cotas.
- Fundo de ações: mínimo 67% em bolsa; SEM come-cotas (IR 15% só no resgate).
- Previdência (PGBL/VGBL): PGBL deduz até 12% da renda tributável no IR (só vale com declaração completa); VGBL pro resto; tabela regressiva chega a 10% após 10 anos; sem come-cotas; tem taxa de carregamento/adm que pode comer a vantagem.
- FI-Infra e Fiagro (listados na B3): rendimentos ISENTOS pra PF; sem FGC; cota oscila em bolsa.
- FIDC: direitos creditórios; retorno alto, risco e complexidade também; cota sênior/subordinada.

BOLSA:
- Ações: sócio da empresa; dividendos isentos (regra atual), JCP tributado na fonte; vendas até R$ 20 mil/mês isentas de IR sobre ganho; acima, 15%.
- FIIs: imóveis (tijolo), CRIs (papel), híbridos ou FOFs; dividendo mensal isento pra PF; venda de cota paga 20% SEM a isenção dos 20 mil.
- ETFs de índice (BOVA11, IVVB11...): cesta barata e diversificada; IR 15% na venda SEM isenção de 20 mil; não distribuem dividendo (reinvestem).
- BDRs: recibos de ações estrangeiras negociados na B3; expostos a câmbio + ativo.
- ETFs de cripto na B3 (HASH11, BITH11...): exposição a cripto sem custódia própria; IR como ETF.

OUTROS:
- Poupança: isenta, liquidez total, mas rende 70% da Selic (Selic >8,5%) — quase sempre perde pra Tesouro Selic/CDB líquidos.
- COE: "capital protegido" com cenários; custo embutido alto e liquidez travada — o retorno médio histórico costuma ser pior do que a combinação simples de renda fixa + um pouco de bolsa.
- Cripto direto (exchange): sem proteção institucional; vendas até R$ 35 mil/mês isentas; acima, 15%+.
- Dólar/exterior: conta internacional ou fundos cambiais — proteção cambial, não "investimento que rende" por si."""