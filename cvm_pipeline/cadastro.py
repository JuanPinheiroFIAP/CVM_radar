"""Cadastro de fundos (Res. CVM 175): dimensão de classes, já com gestora e grupo de peers."""
import logging

import pandas as pd

from .common import ler_csv_zip, somente_digitos
from .config import PARQUET, RAW
from .download import baixar

log = logging.getLogger("cvm")

PASTA = RAW / "cadastro"
ARQUIVO = PASTA / "registro_fundo_classe.zip"

COLS_CLASSE = ["ID_Registro_Fundo", "CNPJ_Classe", "Tipo_Classe", "Denominacao_Social", "Situacao",
               "Classificacao", "Classificacao_Anbima", "Classe_Cotas", "Exclusivo", "Publico_Alvo",
               "Custodiante"]
COLS_FUNDO = ["ID_Registro_Fundo", "CNPJ_Fundo", "Tipo_Pessoa_Gestor", "CPF_CNPJ_Gestor", "Gestor",
              "CNPJ_Administrador", "Administrador"]


def baixar_cadastro() -> None:
    baixar("FI/CAD/DADOS/registro_fundo_classe.zip", PASTA)


def construir_dim_classe() -> pd.DataFrame:
    """Uma linha por classe. A gestora fica no registro do FUNDO, não da classe."""
    classes = ler_csv_zip(ARQUIVO, "registro_classe.csv", dtype=str, usecols=COLS_CLASSE)
    fundos = ler_csv_zip(ARQUIVO, "registro_fundo.csv", dtype=str, usecols=COLS_FUNDO)
    d = classes.merge(fundos, on="ID_Registro_Fundo", how="left")

    tipo = d.Tipo_Classe.str.replace("Classes de Cotas de Fundos ", "", regex=False)
    doc = somente_digitos(d.CPF_CNPJ_Gestor)
    pf = d.Tipo_Pessoa_Gestor.eq("PF")

    dim = pd.DataFrame({
        "cnpj_classe": somente_digitos(d.CNPJ_Classe, 14),
        "cnpj_fundo": somente_digitos(d.CNPJ_Fundo, 14),   # CNPJ do fundo (guarda-chuva), que pode diferir do da classe
        "nome_classe": d.Denominacao_Social,
        "tipo_classe": tipo,
        "situacao": d.Situacao,
        "ativa": d.Situacao.eq("Em Funcionamento Normal"),
        # grupo de peers: classificação ANBIMA (ex.: "Multimercados Macro"); sem ela, a da CVM
        "peer_group": d.Classificacao_Anbima.fillna(d.Classificacao).fillna(tipo),
        "publico_alvo": d.Publico_Alvo,
        "exclusivo": d.Exclusivo.eq("S"),
        "classe_cotas": d.Classe_Cotas.eq("S"),
        "cnpj_gestor": doc.str.zfill(14).where(~pf, doc.str.zfill(11)),
        "nome_gestor": d.Gestor,
        "cnpj_administrador": somente_digitos(d.CNPJ_Administrador, 14),
        "administrador": d.Administrador,
        "custodiante": d.Custodiante,
    })
    antes = len(dim)
    dim = (dim.dropna(subset=["cnpj_classe"])
              .sort_values("ativa", ascending=False)
              .drop_duplicates("cnpj_classe"))
    log.info("dim_classe: %d classes (%d duplicadas descartadas), %.1f%% com gestora",
             len(dim), antes - len(dim), 100 * dim.cnpj_gestor.notna().mean())

    PARQUET.mkdir(parents=True, exist_ok=True)
    dim.to_parquet(PARQUET / "dim_classe.parquet", index=False, compression="zstd")
    return dim
