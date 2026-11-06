"""Informe diário: PL, captação e resgates por classe de fundo e dia."""
import logging

import numpy as np
import pandas as pd

from .common import ler_csv_zip, somente_digitos
from .config import PARQUET, RAW
from .download import baixar

log = logging.getLogger("cvm")

PASTA = RAW / "informe"
SAIDA = PARQUET / "fundo_dia"
COLS = ["CNPJ_FUNDO_CLASSE", "ID_SUBCLASSE", "DT_COMPTC", "VL_QUOTA", "VL_PATRIM_LIQ",
        "CAPTC_DIA", "RESG_DIA", "NR_COTST"]
NUMERICAS = ["VL_QUOTA", "VL_PATRIM_LIQ", "CAPTC_DIA", "RESG_DIA", "NR_COTST"]


def baixar_informe(meses: list[str]) -> None:
    for m in meses:
        baixar(f"FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{m}.zip", PASTA, obrigatorio=False)


def processar_mes(m: str, forcar: bool = False) -> bool:
    """ZIP do mês -> Parquet por (classe, dia). Só refaz se o ZIP é mais novo que o Parquet
    (ou com `forcar`, para reprocessar depois de mudar a lógica)."""
    origem = PASTA / f"inf_diario_fi_{m}.zip"
    destino = SAIDA / f"{m}.parquet"
    if not origem.exists():
        return False
    if not forcar and destino.exists() and destino.stat().st_mtime >= origem.stat().st_mtime:
        return False

    df = ler_csv_zip(origem, f"inf_diario_fi_{m}.csv", dtype=str, usecols=COLS)
    for c in NUMERICAS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["cnpj_classe"] = somente_digitos(df.CNPJ_FUNDO_CLASSE, 14)
    df["dt"] = pd.to_datetime(df.DT_COMPTC, format="%Y-%m-%d")

    # Classe com subclasses reporta SÓ por subclasse (nunca classe + subclasse no mesmo dia),
    # então somar por (classe, dia) não dupla-conta. Cota não é somável: fica só quando há 1 linha.
    g = df.groupby(["cnpj_classe", "dt"], as_index=False, sort=True).agg(
        pl=("VL_PATRIM_LIQ", "sum"), captacao=("CAPTC_DIA", "sum"), resgate=("RESG_DIA", "sum"),
        cotistas=("NR_COTST", "sum"), n_subclasses=("ID_SUBCLASSE", "nunique"),
        vl_quota=("VL_QUOTA", "first"))
    g.loc[g.n_subclasses > 1, "vl_quota"] = np.nan
    g["n_subclasses"] = g.n_subclasses.astype("int16")

    SAIDA.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    g.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(destino)
    log.info("informe %s: %d linhas, %d classes, %s a %s", m, len(g), g.cnpj_classe.nunique(),
             g.dt.min().date(), g.dt.max().date())
    return True


def ingerir_informe(meses: list[str], forcar: bool = False) -> None:
    for m in meses:
        processar_mes(m, forcar)
