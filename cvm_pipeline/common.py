"""Utilitários compartilhados: leitura dos CSVs da CVM, CNPJ e calendário de meses."""
import logging
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

log = logging.getLogger("cvm")

# Os CSVs da CVM usam ';' como separador, '.' como decimal e latin-1.
CSV = dict(sep=";", encoding="latin1")


def ler_csv_zip(zip_path: Path, membro: str, **kw) -> pd.DataFrame:
    """Lê um CSV de dentro do ZIP sem extrair (os arquivos baixados são dado não confiável)."""
    with zipfile.ZipFile(zip_path) as z, z.open(membro) as f:
        return pd.read_csv(f, **{**CSV, **kw})


def somente_digitos(serie: pd.Series, largura: int | None = None) -> pd.Series:
    """CNPJ/CPF só com dígitos (a CVM mistura '00.017.024/0001-53' e '00017024000153')."""
    saida = serie.str.replace(r"\D", "", regex=True)
    return saida.str.zfill(largura) if largura else saida


def formatar_cnpj(cnpj: pd.Series) -> pd.Series:
    """00017024000153 -> 00.017.024/0001-53 (CPF de 11 dígitos fica como está)."""
    f = cnpj.str.replace(r"^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$", r"\1.\2.\3/\4-\5", regex=True)
    return f


def ultimos_meses(n: int, ate: date | None = None) -> list[str]:
    """['202509', ..., '202611']: os n meses até `ate` (inclusive), do mais antigo ao mais novo."""
    ate = ate or date.today()
    ano, mes = ate.year, ate.month
    meses = []
    for _ in range(n):
        meses.append(f"{ano}{mes:02d}")
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return meses[::-1]
