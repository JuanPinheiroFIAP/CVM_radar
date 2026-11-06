"""CDA (composição e diversificação das carteiras): posição por classe e tipo de ativo."""
import logging
import zipfile

import pandas as pd
import pyarrow.parquet as pq

from .common import ler_csv_zip, somente_digitos
from .config import CDA_COMPLETO_MIN, PARQUET, RAW
from .download import baixar

log = logging.getLogger("cvm")

PASTA = RAW / "cda"
SAIDA = PARQUET

# TP_APLIC (texto da CVM, em minúsculas) -> produto do nosso modelo. O que não estiver aqui
# vira "outros" e é avisado no log, para a gente notar se a CVM criar tipos novos.
PRODUTOS = {
    "títulos públicos": "titulos_publicos",
    "operações compromissadas": "compromissadas",
    "cotas de fundos": "cotas_fundos",
    "cotas de fundos de investimento - instrução nº 409": "cotas_fundos",
    "cotas de fundos de renda variável": "cotas_fundos",
    "diferencial de swap a pagar": "swaps",
    "diferencial de swap a receber": "swaps",
    "debêntures": "credito_corporativo",
    "debêntures simples": "credito_corporativo",
    "títulos de crédito privado": "credito_corporativo",
    "títulos ligados ao agronegócio": "credito_corporativo",
    "depósitos a prazo e outros títulos de if": "titulos_bancarios",
    "investimento no exterior": "exterior",
    "ações": "acoes",
    "certificado ou recibo de depósito de valores mobiliários": "acoes",  # units e similares
    "brazilian depository receipt - bdr": "bdr",
    # Aluguel: as ações emprestadas aparecem como posição SEPARADA de "Ações"
    # (um fundo pode ter 560 mil ações em carteira e 2,2 milhões cedidas), então
    # exposição total = acoes + aluguel_doador.
    "ações e outros tvm cedidos em empréstimo": "aluguel_doador",
    "obrigações por ações e outros tvm recebidos em empréstimo": "aluguel_tomador",
    "opções - posições titulares": "opcoes",
    "opções - posições lançadas": "opcoes",
    "mercado futuro - posições compradas": "futuros",
    "mercado futuro - posições vendidas": "futuros",
    "compras a termo a receber": "termo",
    "vendas a termo a receber": "termo",
    "obrigações por compra a termo a pagar": "termo",
    "obrigações por venda a termo a entregar": "termo",
    "outros valores mobiliários registrados na cvm objeto de oferta pública": "outros_valores_mobiliarios",
    "outros valores mobiliários ofertados privadamente": "outros_valores_mobiliarios",
    "disponibilidades": "caixa_e_outros",
    "valores a pagar": "caixa_e_outros",
    "valores a receber": "caixa_e_outros",
    "outras aplicações": "caixa_e_outros",
    "outras operações passivas e exigibilidades": "caixa_e_outros",
}
COM_TICKER = {"acoes", "bdr", "aluguel_doador", "aluguel_tomador"}

# Código do contrato futuro -> fator de risco. Qualquer futuro marca o fundo como BMF; o fator só
# descreve em que ele atua. Contrato fora da lista vira "Outros" (e é avisado no log).
FATOR_FUTURO = {
    "DI1": "Juros", "DAP": "Juros", "T10": "Juros",                      # DI, cupom de IPCA, T-Note
    "DOL": "Câmbio", "WDO": "Câmbio", "DDI": "Câmbio", "FRC": "Câmbio",  # dólar cheio/mini, cupom cambial
    "EUP": "Câmbio", "JAP": "Câmbio", "CAN": "Câmbio", "AUS": "Câmbio",  # euro, iene, dólar canadense/australiano
    "MEX": "Câmbio", "SWI": "Câmbio", "NZL": "Câmbio", "SEK": "Câmbio",  # peso mexicano, franco suíço, NZD, coroa sueca
    "IND": "Índice", "WIN": "Índice", "BRI": "Índice",                   # Ibovespa cheio/mini, IBrX-50
    "WSP": "Índice", "ISP": "Índice", "ESX": "Índice",                   # S&P 500 (cheio/mini), Euro Stoxx
    "BGI": "Commodities", "CNI": "Commodities", "CCM": "Commodities", "ICF": "Commodities",
    "SJC": "Commodities", "ETH": "Commodities", "GLD": "Commodities",    # boi, milho, café, soja, etanol, ouro
}


def baixar_cda(meses: list[str]) -> None:
    for m in meses:
        baixar(f"FI/DOC/CDA/DADOS/cda_fi_{m}.zip", PASTA, obrigatorio=False)


def _produto(tp: pd.Series) -> pd.Series:
    return tp.str.strip().str.lower().map(PRODUTOS)


def _contrato(d: pd.DataFrame) -> pd.Series:
    """Código do contrato futuro (DI1, DAP, DOL, WIN...), lido do tipo do ativo ('Futuro de
    DI1:DI de 1 dia'), da descrição ('FUT DAP/Q32') ou do código ('DI1FUTF28')."""
    cod = d.TP_ATIVO.str.extract(r"Futuro de ([A-Z0-9]{3})\s*:", expand=False)
    cod = cod.fillna(d.DS_ATIVO.str.upper().str.extract(r"\bFUT(?![A-Z])[\s/_-]*([A-Z][A-Z0-9]{2})", expand=False))
    if "CD_ATIVO" in d:
        cod = cod.fillna(d.CD_ATIVO.str.upper().str.extract(r"^([A-Z0-9]{3})FUT", expand=False))
    return cod


def processar_mes(m: str, forcar: bool = False) -> bool:
    origem = PASTA / f"cda_fi_{m}.zip"
    destinos = {n: SAIDA / n / f"{m}.parquet" for n in ("cda_produto", "cda_pl", "cda_acoes", "cda_futuros")}
    if not origem.exists():
        return False
    if not forcar and all(p.exists() and p.stat().st_mtime >= origem.stat().st_mtime for p in destinos.values()):
        return False

    with zipfile.ZipFile(origem) as z:
        nomes = set(z.namelist())
    if f"cda_fi_PL_{m}.csv" not in nomes:
        log.warning("CDA %s: sem arquivo de PL (mês ainda não publicado de fato)", m)
        return False

    # Blocos 1-8 + CONFID. As posições sob confidencialidade vêm só no CONFID (agregadas por
    # tipo) e SOMAM aos blocos: sem elas a composição fecha com o PL em 56% das classes; com
    # elas, em 91%. O mês corrente chega aos poucos e pode não ter todos os blocos.
    linhas, futuros = [], []
    acoes = pd.DataFrame({"cnpj_classe": [], "ticker": [], "produto": [], "qt": [], "vl": []})
    for nome in [f"BLC_{b}" for b in range(1, 9)] + ["CONFID"]:
        if f"cda_fi_{nome}_{m}.csv" not in nomes:
            log.info("CDA %s: bloco %s ausente (mês incompleto)", m, nome)
            continue
        extra = {"BLC_4": ["CD_ATIVO", "QT_POS_FINAL", "TP_ATIVO", "DS_ATIVO"],
                 "BLC_8": ["TP_ATIVO", "DS_ATIVO"]}.get(nome, [])
        d = ler_csv_zip(origem, f"cda_fi_{nome}_{m}.csv", dtype=str,
                        usecols=["CNPJ_FUNDO_CLASSE", "TP_APLIC", "VL_MERC_POS_FINAL"] + extra)
        base = pd.DataFrame({"cnpj_classe": somente_digitos(d.CNPJ_FUNDO_CLASSE, 14),
                             "tp": d.TP_APLIC,
                             "vl": pd.to_numeric(d.VL_MERC_POS_FINAL, errors="coerce")})
        linhas.append(base)
        if nome in ("BLC_4", "BLC_8"):
            eh_futuro = _produto(d.TP_APLIC).eq("futuros")
            if eh_futuro.any():
                futuros.append(base[eh_futuro].assign(contrato=_contrato(d)[eh_futuro]))
        if nome == "BLC_4":
            t = base.assign(produto=_produto(d.TP_APLIC),
                            ticker=d.CD_ATIVO.str.strip().str.upper(),
                            qt=pd.to_numeric(d.QT_POS_FINAL, errors="coerce"))
            acoes = (t[t.produto.isin(COM_TICKER) & t.ticker.notna()]
                     .groupby(["cnpj_classe", "ticker", "produto"], as_index=False)
                     .agg(qt=("qt", "sum"), vl=("vl", "sum")))

    if not linhas:
        return False
    todas = pd.concat(linhas, ignore_index=True)
    todas["produto"] = _produto(todas.tp)
    nao = todas[todas.produto.isna() & todas.tp.notna()].groupby("tp").vl.agg(["size", "sum"])
    if len(nao):
        log.warning("CDA %s: tipos de ativo sem mapeamento (viram 'outros'):\n%s", m, nao.to_string())
    todas["produto"] = todas.produto.fillna("outros")
    todas["vl_abs"] = todas.vl.abs()
    produto = todas.groupby(["cnpj_classe", "produto"], as_index=False).agg(
        vl=("vl", "sum"), vl_abs=("vl_abs", "sum"), n_pos=("vl", "size"))

    pl = ler_csv_zip(origem, f"cda_fi_PL_{m}.csv", dtype=str, usecols=["CNPJ_FUNDO_CLASSE", "VL_PATRIM_LIQ"])
    pl = (pd.DataFrame({"cnpj_classe": somente_digitos(pl.CNPJ_FUNDO_CLASSE, 14),
                        "pl_cda": pd.to_numeric(pl.VL_PATRIM_LIQ, errors="coerce")})
            .groupby("cnpj_classe", as_index=False).pl_cda.sum())

    # Futuros por contrato e fator (só os blocos 4 e 8 trazem o tipo; no CONFID só sabemos que existem).
    fut = (pd.concat(futuros, ignore_index=True) if futuros
           else pd.DataFrame({"cnpj_classe": [], "vl": [], "contrato": []}))
    fut["contrato"] = fut.contrato.fillna("n/d")
    fut["fator"] = fut.contrato.map(FATOR_FUTURO).fillna("Outros").where(fut.contrato != "n/d")  # n/d: sem fator
    sem_fator = fut[fut.fator == "Outros"].contrato.value_counts()
    if len(sem_fator):
        log.info("CDA %s: contratos futuros sem fator (viram 'Outros'): %s", m, sem_fator.head(8).to_dict())
    futuros_df = fut.groupby(["cnpj_classe", "contrato", "fator"], as_index=False, dropna=False).agg(
        n_pos=("vl", "size"))

    for nome, df in (("cda_produto", produto), ("cda_pl", pl), ("cda_acoes", acoes), ("cda_futuros", futuros_df)):
        destinos[nome].parent.mkdir(parents=True, exist_ok=True)
        tmp = destinos[nome].with_suffix(".tmp")
        df.to_parquet(tmp, index=False, compression="zstd")
        tmp.replace(destinos[nome])
    log.info("CDA %s: %d classes no PL, %d linhas classe x produto, %d posições com ticker, %d com contrato futuro",
             m, len(pl), len(produto), len(acoes), len(futuros_df))
    return True


def ingerir_cda(meses: list[str], forcar: bool = False) -> None:
    for m in meses:
        processar_mes(m, forcar)


def cobertura_ticker() -> dict[str, float]:
    """Por mês: fração da exposição em ações que o CDA identifica por ticker. Posições sob
    sigilo (até 90 dias) vêm só agregadas, sem ticker, e a CVM republica o mês depois com tudo:
    em 05/11, setembro tinha ~22% de cobertura e julho muito mais."""
    cob = {}
    for p in sorted((SAIDA / "cda_acoes").glob("*.parquet")):
        prod = pd.read_parquet(SAIDA / "cda_produto" / p.name, columns=["produto", "vl"])
        total = prod.loc[prod.produto == "acoes", "vl"].sum()
        a = pd.read_parquet(p, columns=["produto", "vl"])
        cob[p.stem] = float(a.loc[a.produto == "acoes", "vl"].sum() / total) if total else 0.0
    return cob


def meses_completos() -> list[str]:
    """Meses do CDA com cobertura suficiente de classes: o mês corrente chega aos poucos
    (em 05/11 o arquivo de outubro tinha 393 KB contra 15 MB de setembro)."""
    contagens = {p.stem: pq.ParquetFile(p).metadata.num_rows
                 for p in sorted((SAIDA / "cda_pl").glob("*.parquet"))}
    if not contagens:
        return []
    melhor = max(contagens.values())
    return [m for m, n in contagens.items() if n >= CDA_COMPLETO_MIN * melhor]


def mes_completo() -> str | None:
    meses = meses_completos()
    return max(meses) if meses else None


def mes_com_tickers(minimo: float = 0.5) -> str | None:
    """Mês completo mais recente com ticker suficientemente identificado (cruzamento 'quem
    tem a ação'). Se nenhum chega ao mínimo, o de maior cobertura."""
    completos = set(meses_completos())
    cob = {m: c for m, c in cobertura_ticker().items() if m in completos}
    if not cob:
        return None
    bons = [m for m, c in cob.items() if c >= minimo]
    return max(bons) if bons else max(cob, key=cob.get)
