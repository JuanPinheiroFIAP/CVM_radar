"""CSVs para carga no Salesforce (Data Loader / importação), gerados a partir dos marts.

Chave de cruzamento com o CRM: cnpj_gestora (formatado) ou cnpj_gestora_digitos. Valores em R$
(não em milhares), percentuais em pontos (2,5 = 2,5%), UTF-8 sem BOM, ponto decimal.
"""
import json
import logging

import pandas as pd

from .config import MARTS, SAIDAS

log = logging.getLogger("cvm")

SUFIXO = {1: "1d", 5: "5d", 21: "21d", 63: "63d", 252: "12m"}
JANELAS_CRM = (21, 63, 252)   # o CRM não precisa das cinco janelas


def _inteiro(serie: pd.Series) -> pd.Series:
    return serie.round(0).astype("Int64")


def _gravar_csv(df: pd.DataFrame, nome: str) -> None:
    """Grava via arquivo temporário. Se o destino está aberto no Excel (Windows trava o arquivo),
    avisa e segue em vez de derrubar o pipeline."""
    destino = SAIDAS / nome
    tmp = destino.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    try:
        tmp.replace(destino)
        log.info("%s: %d linhas", nome, len(df))
    except PermissionError:
        tmp.unlink(missing_ok=True)
        log.warning("%s está aberto em outro programa (Excel?). Feche-o e rode: python run_pipeline.py --so exportar", nome)


def _fluxos(df: pd.DataFrame, janelas: list[int]) -> dict[str, pd.Series]:
    out = {}
    for w in janelas:
        s = SUFIXO[w]
        out[f"captacao_liq_{s}"] = _inteiro(df[f"fluxo_liq_{w}"])
        out[f"captacao_pct_{s}"] = (df[f"fluxo_pct_{w}"] * 100).round(2)
        if f"excesso_pp_{w}" in df:
            out[f"excesso_vs_peers_pp_{s}"] = df[f"excesso_pp_{w}"].round(2)
    return out


def gerar() -> None:
    meta = json.loads((MARTS / "meta.json").read_text(encoding="utf-8"))
    janelas = [w for w in JANELAS_CRM if w in meta["janelas_ok"]]
    ref, cda_mes = meta["data_ref"], meta.get("cda_mes")
    SAIDAS.mkdir(parents=True, exist_ok=True)

    # --- Gestoras: uma linha por gestora --------------------------------------------------
    g = pd.read_parquet(MARTS / "gestora_snapshot.parquet")
    saida = pd.DataFrame({
        "cnpj_gestora": g.cnpj_gestor_fmt, "cnpj_gestora_digitos": g.cnpj_gestor, "nome_gestora": g.nome_gestor,
        "data_referencia": ref,
        "aum_total": _inteiro(g.aum_total), "aum_ex_fic": _inteiro(g.aum_ex_fic),
        "aum_nao_exclusivo": _inteiro(g.aum_nao_exclusivo),
        "n_classes": g.n_classes, "rank_aum": g.rank_aum, "grupo_peers_principal": g.peer_group_principal,
        **_fluxos(g, janelas)})
    alu = MARTS / "oportunidade_aluguel.parquet"
    if alu.exists():
        a = pd.read_parquet(alu).set_index("cnpj_gestor")
        saida = saida.join(pd.DataFrame({
            "acoes_exposicao": _inteiro(a.exposicao_acoes), "aluguel_doador_vl": _inteiro(a.doador_vl),
            "aluguel_utilizacao_pct": (a.util_doador * 100).round(2),
            "aluguel_utilizacao_peers_pct": (a.util_peers * 100).round(2),
            "aluguel_potencial_rs": _inteiro(a.potencial_doador_rs), "aluguel_tomador_vl": _inteiro(a.tomador_vl),
            "oportunidade_aluguel": a.oportunidade, "cda_mes_ref": cda_mes}), on="cnpj_gestora_digitos")
    if "segmento_b3" in g:
        saida["segmento_b3"] = g.segmento_b3
        saida["pct_pl_bmf"] = (g.pct_pl_bmf * 100).round(1)
        saida["pct_pl_bov"] = (g.pct_pl_bov * 100).round(1)
    saida["atualizado_em"] = meta["gerado_em"]
    _gravar_csv(saida.sort_values("aum_total", ascending=False), "salesforce_gestoras.csv")

    # --- Fundos (classes): uma linha por classe com PL ------------------------------------
    f = pd.read_parquet(MARTS / "fundo_snapshot.parquet")
    f = f[(f.pl > 0) & f.cnpj_gestor.notna()]
    cnpj_fmt = f.cnpj_classe.str.replace(r"^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$", r"\1.\2.\3/\4-\5", regex=True)
    fundos = pd.DataFrame({
        "cnpj_classe": cnpj_fmt, "nome_classe": f.nome_classe, "cnpj_gestora_digitos": f.cnpj_gestor,
        "nome_gestora": f.nome_gestor, "grupo_peers": f.peer_group, "publico_alvo": f.publico_alvo,
        "exclusivo": f.exclusivo, "fic": f.is_fic, "data_referencia": ref,
        "pl": _inteiro(f.pl), "cotistas": f.cotistas.astype("Int64"), **_fluxos(f, janelas)})
    for w in janelas:
        fundos[f"percentil_peers_{SUFIXO[w]}"] = f[f"pctl_{w}"].round(3)
    if "segmento_b3" in f:
        fundos["segmento_b3"] = f.segmento_b3
        fundos["fatores_bmf"] = f.fatores_bmf
        fundos["pct_renda_variavel"] = (f.pct_rv * 100).round(2)
    fundos["atualizado_em"] = meta["gerado_em"]
    _gravar_csv(fundos.sort_values("pl", ascending=False), "salesforce_fundos.csv")
