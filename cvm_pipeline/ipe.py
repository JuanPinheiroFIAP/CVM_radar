"""Publicações das companhias abertas (IPE) e mapa ticker -> CNPJ (FCA)."""
import logging

import pandas as pd

from .common import ler_csv_zip, somente_digitos
from .config import PARQUET, RAW
from .download import baixar

log = logging.getLogger("cvm")

PASTA_IPE = RAW / "ipe"
PASTA_FCA = RAW / "fca"

# O IPE não tem categoria própria para recompra: só dá para achar pelo texto do assunto.
_RECOMPRA = (r"recompra|re-compra|buy-?back|a[cç][õo]es (?:mantidas )?em tesouraria"
             r"|a[cç][õo]es de (?:sua )?pr[óo]pria emiss[ãa]o|aquisi[cç][ãa]o de a[cç][õo]es de (?:sua )?emiss[ãa]o")
_RECOMPRA_FORTE = r"recompra|re-compra|buy-?back|aquisi[cç][ãa]o de a[cç][õo]es"
_CANCELA_PROGRAMA = (r"(?:cancelamento|encerramento|t[ée]rmino|extin[cç][ãa]o) d[oa]s? programa"
                     r"|programa de recompra.{0,40}(?:encerrad|cancelad|extint)")
_CANCELA_TESOURARIA = r"cancelamento d[ea]s? a[cç][õo]es|cancelamento de a[cç][õo]es em tesouraria"
# Recompra de dívida (ex.: "Recompra de Senior Notes 2050") não é recompra de ações.
_DIVIDA = r"notes?\b|bonds?\b|deb[êe]nture|s[êe]nior|eurobonds?|t[íi]tulos de d[íi]vida|d[íi]vida"
_ACOES = r"a[cç][õo]es"
CATEGORIAS_RECOMPRA = {"Fato Relevante", "Comunicado ao Mercado", "Reunião da Administração",
                        "Aviso aos Acionistas", "Assembleia"}

# ticker -> trecho do nome empresarial no FCA, para os casos em que o FCA não traz o ticker certo
OVERRIDE_TICKERS = {"BPAC11": "BCO BTG PACTUAL", "MBRF3": "MARFRIG GLOBAL FOODS", "EGGY3": "GRANJA FARIA"}

REGRAS_TEMA = [  # (regex sobre a categoria em minúsculas, tema)
    (r"^fato relevante", "Fato relevante"),
    (r"^comunicado ao mercado", "Comunicado ao mercado"),
    (r"^assembleia", "Assembleia"),
    (r"^aviso aos acionistas", "Aviso aos acionistas"),
    (r"^reuni[ãa]o da administra", "Reunião da administração"),
    (r"provento|calend[áa]rio de eventos", "Proventos e eventos"),
    (r"^opa\b", "OPA"),
    (r"oferta de distribui", "Oferta pública"),
    (r"^valores mobili[áa]rios negociados", "Negociação de insiders"),
    (r"^dados econ[ôo]mico", "Resultados e DFs"),
]


def baixar_publicacoes(anos_ipe: list[int], anos_fca: list[int]) -> None:
    for a in anos_ipe:
        baixar(f"CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{a}.zip", PASTA_IPE, obrigatorio=False)
    for a in anos_fca:  # cada ano só lista quem atualizou o FCA nele: precisa de vários
        baixar(f"CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{a}.zip", PASTA_FCA, obrigatorio=False)


def classificar_tema(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    cat = df.categoria.str.lower()
    tema = pd.Series(pd.NA, index=df.index, dtype="object")
    for regex, nome in REGRAS_TEMA:
        tema = tema.mask(tema.isna() & cat.str.contains(regex, regex=True, na=False), nome)
    tema = tema.fillna("Outros")

    texto = (df.assunto.fillna("") + " " + df.tipo.fillna("") + " " + df.especie.fillna("")).str.lower()
    recompra = texto.str.contains(_RECOMPRA, regex=True)
    so_divida = texto.str.contains(_DIVIDA, regex=True) & ~texto.str.contains(_ACOES, regex=True)
    eh_recompra = recompra & ~so_divida & df.categoria.isin(CATEGORIAS_RECOMPRA)

    # Subtipo: o sinal para o mercado é diferente em cada caso.
    sub = pd.Series("Aprovação / andamento do programa", index=df.index)
    sub = sub.mask(texto.str.contains(_CANCELA_PROGRAMA, regex=True), "Encerramento / cancelamento do programa")
    sub = sub.mask(texto.str.contains(_CANCELA_TESOURARIA, regex=True) & ~texto.str.contains(_RECOMPRA_FORTE, regex=True),
                   "Cancelamento de ações em tesouraria")
    return tema.mask(eh_recompra, "Recompra de ações"), sub.where(eh_recompra)


def ingerir_publicacoes(anos: list[int]) -> pd.DataFrame:
    partes = []
    for a in anos:
        zip_path = PASTA_IPE / f"ipe_cia_aberta_{a}.zip"
        if zip_path.exists():
            partes.append(ler_csv_zip(zip_path, f"ipe_cia_aberta_{a}.csv", dtype=str))
    if not partes:
        log.warning("nenhum arquivo IPE disponível")
        return pd.DataFrame()
    d = pd.concat(partes, ignore_index=True)
    pub = pd.DataFrame({
        "cnpj_companhia": somente_digitos(d.CNPJ_Companhia, 14),
        "companhia": d.Nome_Companhia,
        "codigo_cvm": d.Codigo_CVM,
        "data_referencia": pd.to_datetime(d.Data_Referencia, errors="coerce"),
        "data_entrega": pd.to_datetime(d.Data_Entrega, errors="coerce"),
        "categoria": d.Categoria, "tipo": d.Tipo, "especie": d.Especie, "assunto": d.Assunto,
        "protocolo": d.Protocolo_Entrega,
        "versao": pd.to_numeric(d.Versao, errors="coerce"),
        "link": d.Link_Download,
    })
    # Uma entrega pode ser reapresentada: fica a versão mais recente.
    pub = (pub.sort_values("versao").drop_duplicates("protocolo", keep="last")
              .sort_values("data_entrega", ascending=False, ignore_index=True))
    pub["tema"], pub["evento_recompra"] = classificar_tema(pub)
    pub.to_parquet(PARQUET / "publicacoes.parquet", index=False, compression="zstd")
    log.info("publicações: %d (%s a %s); recompras: %d", len(pub), pub.data_entrega.min().date(),
             pub.data_entrega.max().date(), (pub.tema == "Recompra de ações").sum())
    return pub


def ingerir_tickers(anos: list[int]) -> pd.DataFrame:
    """Ticker -> CNPJ da companhia (FCA), para cruzar publicações com as carteiras do CDA."""
    cols = ["CNPJ_Companhia", "Data_Referencia", "Versao", "Nome_Empresarial", "Valor_Mobiliario",
            "Codigo_Negociacao", "Data_Fim_Negociacao"]
    partes = []
    for a in anos:
        zip_path = PASTA_FCA / f"fca_cia_aberta_{a}.zip"
        if zip_path.exists():
            partes.append(ler_csv_zip(zip_path, f"fca_cia_aberta_valor_mobiliario_{a}.csv",
                                      dtype=str, usecols=cols))
    if not partes:
        return pd.DataFrame()
    d = pd.concat(partes, ignore_index=True)
    fca_nomes = d.drop_duplicates("CNPJ_Companhia", keep="last").set_index("CNPJ_Companhia").Nome_Empresarial
    d = d[d.Codigo_Negociacao.notna()].copy()
    # pode vir mais de um por linha; há tickers com dígito no meio (B3SA3) e sufixo (ENMA3B)
    d["ticker"] = d.Codigo_Negociacao.str.upper().str.findall(r"[A-Z][A-Z0-9]{3}\d{1,2}[A-Z]?")
    d = d.explode("ticker").dropna(subset=["ticker"])
    # O FCA às vezes traz código inválido ('000000' no BTG, 'ADR' na Marfrig): resolve pelo nome.
    for ticker, trecho_nome in OVERRIDE_TICKERS.items():
        achou = fca_nomes[fca_nomes.str.contains(trecho_nome, case=False, na=False)]
        if achou.empty:
            continue
        cnpj = achou.index[0]
        d = pd.concat([d, pd.DataFrame([{"CNPJ_Companhia": cnpj, "Data_Referencia": "9999", "Versao": "999",
                                         "Nome_Empresarial": achou.iloc[0], "Valor_Mobiliario": "Ações / Units",
                                         "Data_Fim_Negociacao": None, "ticker": ticker}])], ignore_index=True)
    t = pd.DataFrame({
        "ticker": d.ticker,
        "cnpj_companhia": somente_digitos(d.CNPJ_Companhia, 14),
        "companhia": d.Nome_Empresarial,
        "valor_mobiliario": d.Valor_Mobiliario,
        "negociando": d.Data_Fim_Negociacao.isna(),
        "_ordem": d.Data_Referencia + "|" + d.Versao.str.zfill(3),
    })
    t = t.sort_values("_ordem").drop_duplicates("ticker", keep="last").drop(columns="_ordem")
    t.to_parquet(PARQUET / "dim_ticker.parquet", index=False, compression="zstd")
    log.info("dim_ticker: %d tickers de %d companhias", len(t), t.cnpj_companhia.nunique())
    return t
