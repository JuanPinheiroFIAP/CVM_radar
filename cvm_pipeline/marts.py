"""Marts: tabelas finais (leves) que o app e as exportações consomem.

Entrada: camada Parquet (dim_classe, fundo_dia, cda_*, publicacoes, dim_ticker).
Saída (MARTS/): fundo_snapshot, gestora_snapshot, gestora_dia, peers_captacao,
penetracao_gestora, penetracao_produto, oportunidade_aluguel, posicoes_acoes,
publicacoes, dim_ticker, qualidade_dia, meta.json
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from . import cda
from .common import formatar_cnpj
from .config import (ACOES_MIN_OPORTUNIDADE, ANOMALIA_DIAS_MAX, ANOMALIA_FRACAO, ANOMALIA_MIN_PL,
                     BOV_MIN_PCT_PL, COBERTURA_MIN_DIA, FAIXAS_PL, FFILL_PL_DIAS, FIC_PCT_COTAS,
                     FLUXO_MAX_X_PL0, JANELAS, MARTS, MIN_FUNDOS_GRUPO, PARQUET, PL_MIN_PEERS,
                     ROTULOS_FAIXA, SEGMENTO_GESTORA_MIN_PCT)

log = logging.getLogger("cvm")

PRODUTOS_ANALISE = ["acoes", "bdr", "aluguel_doador", "aluguel_tomador", "opcoes", "futuros", "termo",
                    "swaps", "titulos_publicos", "compromissadas", "credito_corporativo",
                    "titulos_bancarios", "cotas_fundos", "exterior"]


def _salvar(df: pd.DataFrame, nome: str) -> None:
    MARTS.mkdir(parents=True, exist_ok=True)
    destino = MARTS / f"{nome}.parquet"
    tmp = destino.with_suffix(".tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(destino)
    log.info("mart %-22s %9d linhas", nome, len(df))


# ---------------------------------------------------------------------------------------
# Painéis do informe diário (datas x classes)
# ---------------------------------------------------------------------------------------
@dataclass
class Paineis:
    pl: pd.DataFrame          # PL; classe que atrasou mantém o último valor por poucos dias
    cap: pd.DataFrame         # captação bruta (0 onde não reportou)
    res: pd.DataFrame         # resgates (0 onde não reportou)
    cotistas: pd.Series       # nº de cotistas na data de referência
    data_ref: pd.Timestamp    # última data com cobertura completa; o snapshot é feito nela
    ultima_data: pd.Timestamp # última data com algum dado (pode estar incompleta)
    qualidade: pd.DataFrame   # linhas e cobertura por dia
    anomalias: pd.DataFrame   # PLs descartados por erro de preenchimento (auditoria)


def filtrar_anomalias(pl: pd.DataFrame, cap: pd.DataFrame, res: pd.DataFrame):
    """Limpa erros de preenchimento do informe diário. Compara o PL do dia com o último PL
    válido somado aos fluxos desde então; se o inexplicado passa de 95% do maior PL, há erro,
    e a pergunta é de que lado:

    * PL: o PL pulou e não há fluxo que explique (Redwood: R$ 0,15 bi -> R$ 824 bi, captação 0).
      O PL do dia é descartado (herda o último válido). Se o "erro" persiste por
      ANOMALIA_DIAS_MAX pregões, é aceito como novo patamar (incorporação, por exemplo).
    * Fluxo: o PL ficou igual mas o fundo informou captação/resgate do tamanho do próprio PL
      (PL R$ 351 mi, captação R$ 351,6 mi). O fluxo do dia é zerado.

    Salto legítimo vem com o fluxo que o explica (captação R$ 2,5 bi -> PL +R$ 2,5 bi): passa."""
    P = pl.to_numpy(dtype=float)
    C, R = cap.reindex_like(pl).fillna(0.0).to_numpy(), res.reindex_like(pl).fillna(0.0).to_numpy()
    T, N = P.shape
    bom, acum, corrida = np.full(N, np.nan), np.zeros(N), np.zeros(N, dtype=int)
    erro_pl, erro_fluxo, registros = np.zeros((T, N), dtype=bool), np.zeros((T, N), dtype=bool), []
    for t in range(T):
        x = P[t]
        acum += C[t] - R[t]
        dpl = x - bom
        base = np.maximum(np.abs(bom), np.abs(x))
        suspeito = (~np.isnan(x) & ~np.isnan(bom) & (base > ANOMALIA_MIN_PL)
                    & (np.abs(dpl - acum) > ANOMALIA_FRACAO * base))
        fluxo = suspeito & (np.abs(dpl) < 0.5 * np.abs(acum))   # PL quase parado: o errado é o fluxo
        ruim = suspeito & ~fluxo
        corrida = np.where(ruim, corrida + 1, 0)
        aceito = ruim & (corrida > ANOMALIA_DIAS_MAX)
        ruim &= ~aceito
        corrida = np.where(aceito, 0, corrida)
        for j in np.flatnonzero(ruim | fluxo):
            registros.append((pl.index[t], pl.columns[j], "fluxo" if fluxo[j] else "pl", x[j], bom[j],
                              C[t, j] - R[t, j]))
        valido = ~np.isnan(x) & ~ruim
        bom, acum = np.where(valido, x, bom), np.where(valido, 0.0, acum)
        erro_pl[t], erro_fluxo[t] = ruim, fluxo
    anomalias = pd.DataFrame(registros, columns=["dt", "cnpj_classe", "tipo", "pl_informado",
                                                 "pl_valido_anterior", "fluxo_liq_informado"])
    como_df = lambda m: pd.DataFrame(m, index=pl.index, columns=pl.columns)  # noqa: E731
    return (pl.mask(como_df(erro_pl)), cap.mask(como_df(erro_fluxo), 0.0),
            res.mask(como_df(erro_fluxo), 0.0), anomalias)


def carregar_paineis() -> Paineis:
    arquivos = sorted((PARQUET / "fundo_dia").glob("*.parquet"))
    if not arquivos:
        raise RuntimeError("sem dados do informe diário: rode as etapas 'baixar' e 'ingerir'")
    cols = ["cnpj_classe", "dt", "pl", "captacao", "resgate", "cotistas"]
    df = pd.concat([pd.read_parquet(a, columns=cols) for a in arquivos], ignore_index=True)

    # Cobertura do dia = linhas / mediana dos 10 dias anteriores. Dia com <50% é feriado
    # (em 12/out havia 5 linhas) e sai do calendário; o último dia costuma estar incompleto
    # porque parte dos fundos entrega com atraso.
    linhas = df.groupby("dt").size()
    tipico = linhas.rolling(10, min_periods=3).median().shift(1).fillna(linhas)
    cobertura = linhas / tipico
    util = cobertura >= 0.5
    fechado = util & (cobertura >= COBERTURA_MIN_DIA)
    ultima_data, data_ref = linhas.index[util][-1], linhas.index[fechado][-1]
    qualidade = pd.DataFrame({"linhas": linhas, "cobertura": cobertura, "util": util}).reset_index()

    df = df[df.dt.isin(linhas.index[util]) & (df.dt <= data_ref)]
    pivo = lambda col: df.pivot(index="dt", columns="cnpj_classe", values=col)  # noqa: E731
    cap, res = pivo("captacao").fillna(0.0), pivo("resgate").fillna(0.0)
    pl, cap, res, anomalias = filtrar_anomalias(pivo("pl"), cap, res)
    pl = pl.ffill(limit=FFILL_PL_DIAS)
    cotistas = df[df.dt == data_ref].set_index("cnpj_classe").cotistas
    log.info("painéis: %d pregões x %d classes; data de referência %s (última com dados: %s); "
             "anomalias: %d erros de PL e %d de fluxo, em %d classes", len(pl), pl.shape[1],
             data_ref.date(), ultima_data.date(), (anomalias.tipo == "pl").sum(),
             (anomalias.tipo == "fluxo").sum(), anomalias.cnpj_classe.nunique())
    return Paineis(pl, cap, res, cotistas, data_ref, ultima_data, qualidade, anomalias)


# ---------------------------------------------------------------------------------------
# Cadastro enriquecido: FIC pela composição da carteira
# ---------------------------------------------------------------------------------------
def dim_com_fic(dim: pd.DataFrame, mes_cda: str | None) -> pd.DataFrame:
    """is_fic = registrada como classe de cotas OU >=95% do PL em cotas de outros fundos.
    A flag do registro sozinha deixa passar fundos de fundos (4 mil classes, R$ 785 bi)."""
    dim = dim.copy()
    dim["pct_cotas"] = np.nan
    if mes_cda:
        prod = pd.read_parquet(PARQUET / "cda_produto" / f"{mes_cda}.parquet", columns=["cnpj_classe", "produto", "vl"])
        pl = pd.read_parquet(PARQUET / "cda_pl" / f"{mes_cda}.parquet").set_index("cnpj_classe").pl_cda
        cotas = prod[prod.produto == "cotas_fundos"].set_index("cnpj_classe").vl.reindex(pl.index).fillna(0.0)
        dim["pct_cotas"] = dim.cnpj_classe.map((cotas / pl).where(pl >= PL_MIN_PEERS))
    dim["is_fic"] = dim.classe_cotas | (dim.pct_cotas >= FIC_PCT_COTAS)
    return dim


# ---------------------------------------------------------------------------------------
# Segmento da B3: BMF x BOV
# ---------------------------------------------------------------------------------------
ORDEM_FATOR = ["Juros", "Câmbio", "Índice", "Commodities", "Outros"]


def classificar_b3(dim: pd.DataFrame, mes: str) -> pd.DataFrame:
    """Em que segmento da B3 cada classe opera, pelas posições do CDA (inclui as confidenciais,
    que vêm agregadas por tipo):

    * BMF = tem posição em futuros (qualquer contrato; o fator de risco só descreve onde atua).
    * BOV = renda variável direta (ações, units, BDR e ações cedidas) >= 0,5% do PL, ou qualquer
      posição em opções, termo ou aluguel tomador. Opções contam como BOV por serem, na maioria,
      sobre ações (opções de Ibovespa/dólar/flexíveis ficam junto, são poucas).
    * "BMF + BOV" se os dois; "Sem BMF/BOV" se nenhum (renda fixa, crédito, cotas);
      "FIC" para fundos de cotas (a carteira real está no fundo-alvo)."""
    prod = pd.read_parquet(PARQUET / "cda_produto" / f"{mes}.parquet")
    fut = pd.read_parquet(PARQUET / "cda_futuros" / f"{mes}.parquet")
    c = pd.read_parquet(PARQUET / "cda_pl" / f"{mes}.parquet").set_index("cnpj_classe")
    colunas = ["acoes", "bdr", "aluguel_doador", "aluguel_tomador", "opcoes", "termo", "futuros", "swaps"]

    def matriz(coluna: str) -> pd.DataFrame:
        return prod.pivot(index="cnpj_classe", columns="produto", values=coluna).reindex(
            index=c.index, columns=colunas).fillna(0.0)
    vl, npos = matriz("vl_abs"), matriz("n_pos")

    c["pct_rv"] = ((vl.acoes + vl.bdr + vl.aluguel_doador) / c.pl_cda.where(c.pl_cda > 0))
    c["usa_bmf"] = npos.futuros > 0
    c["usa_bov"] = (c.pct_rv >= BOV_MIN_PCT_PL) | (vl.opcoes > 0) | (vl.termo > 0) | (vl.aluguel_tomador > 0)
    c["usa_opcoes"], c["usa_swaps"] = vl.opcoes > 0, vl.swaps > 0
    c["n_futuros"] = npos.futuros.astype(int)

    fator = (fut[fut.fator.notna()].groupby(["cnpj_classe", "fator"]).n_pos.sum().reset_index()
             .assign(ordem=lambda t: t.fator.map({f: i for i, f in enumerate(ORDEM_FATOR)}))
             .sort_values(["cnpj_classe", "ordem"]).groupby("cnpj_classe").fator.agg(", ".join))
    c["fatores_bmf"] = c.index.map(fator)
    c.loc[c.usa_bmf & c.fatores_bmf.isna(), "fatores_bmf"] = "não detalhado"  # futuros só no CONFID

    fic = dim.set_index("cnpj_classe").is_fic.reindex(c.index).eq(True)
    c["segmento_b3"] = np.select(
        [fic, c.usa_bmf & c.usa_bov, c.usa_bmf, c.usa_bov], ["FIC", "BMF + BOV", "BMF", "BOV"], "Sem BMF/BOV")
    return c.reset_index()


# ---------------------------------------------------------------------------------------
# Captação: fundos, peers e gestoras
# ---------------------------------------------------------------------------------------
def snapshot_fundos(p: Paineis, dim: pd.DataFrame) -> pd.DataFrame:
    s = pd.DataFrame(index=p.pl.columns)
    s.index.name = "cnpj_classe"
    s["pl"] = p.pl.iloc[-1]
    s["cotistas"] = p.cotistas
    liq = p.cap - p.res
    for w in JANELAS:
        if len(p.pl) <= w:
            log.warning("histórico insuficiente para a janela de %d pregões; rode com mais meses", w)
            continue
        pl0 = p.pl.iloc[-(w + 1)]                      # PL no fechamento do dia anterior à janela
        s[f"pl0_{w}"] = pl0.where(pl0 > 0)
        s[f"fluxo_liq_{w}"] = liq.iloc[-w:].sum()
        # Base das métricas em %: PL inicial relevante e fluxo que não seja lançamento. Quem fica
        # de fora continua somando nos fluxos em R$, só não entra em %, peers e percentis.
        s[f"base_pct_{w}"] = ((s[f"pl0_{w}"] >= PL_MIN_PEERS)
                              & (s[f"fluxo_liq_{w}"].abs() <= FLUXO_MAX_X_PL0 * s[f"pl0_{w}"]))
        s[f"fluxo_pct_{w}"] = (s[f"fluxo_liq_{w}"] / s[f"pl0_{w}"]).where(s[f"base_pct_{w}"])
    # fundos liquidados dentro da janela continuam contando nos fluxos (evita viés de sobrevivência)
    s = s[s.drop(columns="cotistas").notna().any(axis=1)]

    colunas = ["nome_classe", "peer_group", "publico_alvo", "exclusivo", "is_fic", "cnpj_gestor", "nome_gestor"]
    colunas += [c for c in ("segmento_b3", "fatores_bmf", "pct_rv", "n_futuros") if c in dim]
    s = s.join(dim.set_index("cnpj_classe")[colunas], how="left")
    if "segmento_b3" in s:
        s["segmento_b3"] = s.segmento_b3.fillna("Sem CDA")
    s["peer_group"] = s.peer_group.fillna("Sem classificação")
    s["exclusivo"] = s.exclusivo.eq(True)  # classe sem cadastro: tratada como não exclusiva
    s["is_fic"] = s.is_fic.eq(True)
    return s


def estatisticas_peers(s: pd.DataFrame) -> pd.DataFrame:
    """Benchmark de captação por grupo de peers e janela. Base: classes não exclusivas
    (fundo exclusivo tem 1 cotista e fluxo em blocos, só distorce a média). A média é ponderada
    por PL ('agregado'); a mediana fica como referência, mas em janelas curtas costuma ser 0
    porque a maioria dos fundos não tem movimento."""
    partes = []
    for w in JANELAS:
        if f"fluxo_pct_{w}" not in s:
            continue
        pct = f"fluxo_pct_{w}"
        b = s[~s.exclusivo & s[f"base_pct_{w}"]]
        g = b.groupby("peer_group")
        st = pd.DataFrame({
            "n": g.size(), "mediana": g[pct].median(),
            "p25": g[pct].quantile(0.25), "p75": g[pct].quantile(0.75),
            "agregado": g[f"fluxo_liq_{w}"].sum() / g[f"pl0_{w}"].sum(),  # fluxo ponderado por PL
            "pl0": g[f"pl0_{w}"].sum()})
        st.loc[st.n < MIN_FUNDOS_GRUPO, ["mediana", "p25", "p75", "agregado"]] = np.nan
        partes.append(st.assign(janela=w).reset_index())
    return pd.concat(partes, ignore_index=True)


def aplicar_peers(s: pd.DataFrame, peers: pd.DataFrame) -> pd.DataFrame:
    for w in JANELAS:
        if f"fluxo_pct_{w}" not in s:
            continue
        st = peers[peers.janela == w].set_index("peer_group")
        s[f"peer_mediana_{w}"] = s.peer_group.map(st.mediana)
        s[f"peer_agregado_{w}"] = s.peer_group.map(st.agregado)
        # excesso sobre a média ponderada dos peers (mesmo benchmark usado para as gestoras)
        s[f"excesso_pp_{w}"] = (s[f"fluxo_pct_{w}"] - s[f"peer_agregado_{w}"]) * 100
        base = ~s.exclusivo & s[f"base_pct_{w}"] & s[f"peer_agregado_{w}"].notna()
        s[f"pctl_{w}"] = np.nan
        s.loc[base, f"pctl_{w}"] = s[base].groupby("peer_group")[f"fluxo_pct_{w}"].rank(pct=True)
    return s


def snapshot_gestoras(s: pd.DataFrame) -> pd.DataFrame:
    """AuM e captação por gestora.

    Fluxo em R$ = soma das classes não exclusivas. Fluxo % e 'esperado' usam só classes com
    PL inicial válido (como a base dos peers). 'Esperado' = quanto a gestora teria captado se
    cada uma de suas classes captasse como o agregado do seu grupo de peers (ajusta pelo mix).
    """
    s = s[s.cnpj_gestor.notna()]
    vivo = s[s.pl > 0]
    g = vivo.groupby("cnpj_gestor")
    out = pd.DataFrame({
        "nome_gestor": g.nome_gestor.first(), "aum_total": g.pl.sum(),
        "aum_ex_fic": vivo[~vivo.is_fic].groupby("cnpj_gestor").pl.sum(),
        "aum_nao_exclusivo": vivo[~vivo.exclusivo].groupby("cnpj_gestor").pl.sum(),
        "n_classes": g.size()})
    out[["aum_ex_fic", "aum_nao_exclusivo"]] = out[["aum_ex_fic", "aum_nao_exclusivo"]].fillna(0.0)
    por_grupo = (vivo.groupby(["cnpj_gestor", "peer_group"]).pl.sum().reset_index()
                     .sort_values("pl").drop_duplicates("cnpj_gestor", keep="last").set_index("cnpj_gestor"))
    out["peer_group_principal"] = por_grupo.peer_group
    out["peer_group_principal_pct"] = por_grupo.pl / out.aum_total

    if "segmento_b3" in s:  # participação do PL (fundos finais) em classes que operam BMF / BOV
        fin = vivo[~vivo.is_fic & vivo.segmento_b3.isin(["BMF", "BOV", "BMF + BOV", "Sem BMF/BOV"])]
        total = fin.groupby("cnpj_gestor").pl.sum().reindex(out.index)

        def parcela(segmentos: list[str]) -> pd.Series:
            soma = fin[fin.segmento_b3.isin(segmentos)].groupby("cnpj_gestor").pl.sum()
            return soma.reindex(out.index).fillna(0.0) / total
        out["pct_pl_bmf"], out["pct_pl_bov"] = parcela(["BMF", "BMF + BOV"]), parcela(["BOV", "BMF + BOV"])
        bmf, bov = out.pct_pl_bmf >= SEGMENTO_GESTORA_MIN_PCT, out.pct_pl_bov >= SEGMENTO_GESTORA_MIN_PCT
        out["segmento_b3"] = np.select([bmf & bov, bmf, bov, total.notna()], ["BMF + BOV", "BMF", "BOV", "Sem BMF/BOV"],
                                       "Sem CDA / só FIC")

    ne = s[~s.exclusivo]
    for w in JANELAS:
        if f"fluxo_pct_{w}" not in s:
            continue
        out[f"fluxo_liq_{w}"] = ne.groupby("cnpj_gestor")[f"fluxo_liq_{w}"].sum()
        b = ne[ne[f"base_pct_{w}"] & ne[f"peer_agregado_{w}"].notna()]
        b = b.assign(_esp=b[f"peer_agregado_{w}"] * b[f"pl0_{w}"])
        gb = b.groupby("cnpj_gestor")
        pl0 = gb[f"pl0_{w}"].sum()
        out[f"base_pl0_{w}"] = pl0
        out[f"fluxo_pct_{w}"] = gb[f"fluxo_liq_{w}"].sum() / pl0
        out[f"esperado_pct_{w}"] = gb["_esp"].sum() / pl0
        out[f"excesso_pp_{w}"] = (out[f"fluxo_pct_{w}"] - out[f"esperado_pct_{w}"]) * 100
    out["rank_aum"] = out.aum_total.rank(ascending=False, method="min").astype(int)
    out = out.reset_index()
    out.insert(1, "cnpj_gestor_fmt", formatar_cnpj(out.cnpj_gestor))
    return out


def serie_gestoras(p: Paineis, s: pd.DataFrame) -> pd.DataFrame:
    """AuM e fluxo diário por gestora, para os gráficos de evolução."""
    cols = p.pl.columns
    gest = s.cnpj_gestor.reindex(cols)
    fic, excl = s.is_fic.reindex(cols).eq(True), s.exclusivo.reindex(cols).eq(True)

    def soma(painel: pd.DataFrame, mascara: pd.Series) -> pd.Series:
        m = (gest.notna() & mascara).to_numpy()
        agrupado = painel.loc[:, m].T.groupby(gest[m].to_numpy()).sum().T
        return agrupado.stack()

    todas = pd.Series(True, index=cols)
    out = pd.concat({
        "aum_total": soma(p.pl, todas), "aum_ex_fic": soma(p.pl, ~fic),
        "captacao": soma(p.cap, ~excl), "resgate": soma(p.res, ~excl)}, axis=1)
    out = out.rename_axis(["dt", "cnpj_gestor"]).reset_index()
    out["fluxo_liq"] = out.captacao - out.resgate
    return out[out.aum_total > 0]


# ---------------------------------------------------------------------------------------
# Penetração por produto (CDA)
# ---------------------------------------------------------------------------------------
def razao_peers(df: pd.DataFrame, num: str, den: str) -> tuple[pd.Series, pd.Series]:
    """Razão agregada (Σnum/Σden) dos peers de cada linha. Usa grupo+faixa de PL quando há
    classes suficientes; senão só o grupo; senão o mercado todo. Devolve (razão, nível usado)."""
    resultado = pd.Series(df[num].sum() / df[den].sum(), index=df.index)
    nivel = pd.Series("mercado", index=df.index)
    for chaves, nome in ((["peer_group"], "grupo"), (["peer_group", "faixa"], "grupo+faixa")):
        g = df.groupby(chaves)
        ok = g[num].transform("size") >= MIN_FUNDOS_GRUPO
        razao = g[num].transform("sum") / g[den].transform("sum")
        resultado, nivel = resultado.mask(ok, razao), nivel.mask(ok, nome)
    return resultado, nivel


def penetracao(dim: pd.DataFrame, mes: str):
    """Compara o que cada gestora tem em cada produto com o que seus peers (mesma classe e
    faixa de PL) têm. Só classes finais: FIC fica de fora (a carteira real está no fundo-alvo)."""
    prod = pd.read_parquet(PARQUET / "cda_produto" / f"{mes}.parquet")
    pl = pd.read_parquet(PARQUET / "cda_pl" / f"{mes}.parquet")
    cl = pl.merge(dim[["cnpj_classe", "peer_group", "cnpj_gestor", "nome_gestor", "is_fic"]], on="cnpj_classe")
    cl = cl[(cl.pl_cda >= PL_MIN_PEERS) & ~cl.is_fic & cl.cnpj_gestor.notna()].reset_index(drop=True)
    cl["faixa"] = pd.cut(cl.pl_cda, FAIXAS_PL, labels=ROTULOS_FAIXA, right=False).astype(str)

    def matriz(coluna: str) -> pd.DataFrame:
        m = prod.pivot(index="cnpj_classe", columns="produto", values=coluna)
        return m.reindex(index=cl.cnpj_classe, columns=PRODUTOS_ANALISE).fillna(0.0)
    vl, npos = matriz("vl_abs"), matriz("n_pos")

    base_g = cl.groupby("cnpj_gestor").agg(nome_gestor=("nome_gestor", "first"), pl_base=("pl_cda", "sum"),
                                           n_classes=("cnpj_classe", "size")).reset_index()
    linhas = []
    for p in PRODUTOS_ANALISE:
        usa = (npos[p] > 0 if p == "futuros" else vl[p] > 0).to_numpy()  # futuros têm VL ~0
        d = cl.assign(vl=vl[p].to_numpy(), usa=usa, pl_usa=cl.pl_cda * usa)
        d["peso_peers"], d["nivel_peer"] = razao_peers(d, "vl", "pl_cda")
        d["adocao_peers"], _ = razao_peers(d, "pl_usa", "pl_cda")
        d["esp_vl"], d["esp_usa"] = d.peso_peers * d.pl_cda, d.adocao_peers * d.pl_cda
        g = d.groupby("cnpj_gestor").agg(vl=("vl", "sum"), pl_usa=("pl_usa", "sum"), esp_vl=("esp_vl", "sum"),
                                         esp_usa=("esp_usa", "sum"), n_usam=("usa", "sum")).reset_index()
        linhas.append(g.assign(produto=p))
    G = pd.concat(linhas, ignore_index=True).merge(base_g[["cnpj_gestor", "pl_base", "n_classes"]], on="cnpj_gestor")
    G["peso"], G["peso_peers"] = G.vl / G.pl_base, G.esp_vl / G.pl_base
    G["adocao"], G["adocao_peers"] = G.pl_usa / G.pl_base, G.esp_usa / G.pl_base
    G["gap_pp"] = (G.peso_peers - G.peso) * 100
    G["gap_rs"] = (G.esp_vl - G.vl).clip(lower=0)
    G = G.drop(columns=["esp_vl", "esp_usa", "pl_usa"])

    # Aluguel de ações. Exposição = ações em carteira + ações cedidas (posições separadas).
    # Utilização = cedidas / exposição, comparada com a dos peers de mesmo perfil.
    e = cl.assign(acoes=vl["acoes"].to_numpy(), doador=vl["aluguel_doador"].to_numpy(),
                  tomador=vl["aluguel_tomador"].to_numpy())
    e["exposicao"] = e.acoes + e.doador
    e = e[e.exposicao >= PL_MIN_PEERS].copy()
    e["util_peers"], _ = razao_peers(e, "doador", "exposicao")
    e["esp_doador"] = e.util_peers * e.exposicao
    a = e.groupby("cnpj_gestor").agg(
        nome_gestor=("nome_gestor", "first"), n_classes_acoes=("exposicao", "size"),
        exposicao_acoes=("exposicao", "sum"), acoes_vl=("acoes", "sum"), doador_vl=("doador", "sum"),
        tomador_vl=("tomador", "sum"), esp_doador=("esp_doador", "sum")).reset_index()
    a["util_doador"], a["util_peers"] = a.doador_vl / a.exposicao_acoes, a.esp_doador / a.exposicao_acoes
    a["potencial_doador_rs"] = (a.esp_doador - a.doador_vl).clip(lower=0)
    a["sem_aluguel"] = a.doador_vl <= 0
    a["oportunidade"] = (a.potencial_doador_rs > 0) & (a.exposicao_acoes >= ACOES_MIN_OPORTUNIDADE)
    a = a.drop(columns="esp_doador").sort_values("potencial_doador_rs", ascending=False)

    soma_vl = prod.groupby("cnpj_classe").vl.sum().reindex(pl.cnpj_classe).to_numpy()
    cobertura = soma_vl / pl.pl_cda.where(pl.pl_cda > 0).to_numpy()
    ok = float((np.abs(cobertura[(pl.pl_cda >= PL_MIN_PEERS).to_numpy()] - 1) < 0.05).mean())
    return base_g, G, a, ok


def posicoes_acoes(dim: pd.DataFrame, mes: str) -> pd.DataFrame:
    """Quem tem o quê: gestora x ticker, com a companhia emissora (via FCA). Usa o mês mais
    recente com ticker completo (ver cda.mes_com_tickers), não necessariamente o da penetração."""
    a = pd.read_parquet(PARQUET / "cda_acoes" / f"{mes}.parquet")
    a = a.merge(dim[["cnpj_classe", "cnpj_gestor", "nome_gestor"]], on="cnpj_classe")
    a = a[a.cnpj_gestor.notna()]
    g = a.groupby(["cnpj_gestor", "nome_gestor", "ticker", "produto"], as_index=False).agg(
        vl=("vl", "sum"), qt=("qt", "sum"), n_classes=("cnpj_classe", "nunique"))
    tick = PARQUET / "dim_ticker.parquet"
    if tick.exists():
        g = g.merge(pd.read_parquet(tick)[["ticker", "cnpj_companhia"]], on="ticker", how="left")
    return g


# ---------------------------------------------------------------------------------------
def construir_tudo() -> None:
    t_ini = datetime.now()
    mes_cda = cda.mes_completo()
    log.info("mês do CDA usado: %s", mes_cda)
    dim = dim_com_fic(pd.read_parquet(PARQUET / "dim_classe.parquet"), mes_cda)
    classif = None
    if mes_cda:
        classif = classificar_b3(dim, mes_cda)
        dim = dim.merge(classif[["cnpj_classe", "segmento_b3", "fatores_bmf", "pct_rv", "n_futuros"]],
                        on="cnpj_classe", how="left")

    p = carregar_paineis()
    s = snapshot_fundos(p, dim)
    peers = estatisticas_peers(s)
    s = aplicar_peers(s, peers)
    gest = snapshot_gestoras(s)
    dia = serie_gestoras(p, s)

    pl_total = s.pl.where(s.pl > 0).sum()
    sem_gestor = s.loc[s.cnpj_gestor.isna() & (s.pl > 0), "pl"].sum() / pl_total
    assert abs(gest.aum_total.sum() - s.loc[s.cnpj_gestor.notna() & (s.pl > 0), "pl"].sum()) < 1, \
        "AuM das gestoras não bate com a soma das classes"
    log.info("PL total R$ %.2f tri; %.3f%% sem gestora; %d gestoras", pl_total / 1e12, 100 * sem_gestor, len(gest))

    _salvar(s.reset_index(), "fundo_snapshot")
    _salvar(peers, "peers_captacao")
    _salvar(gest, "gestora_snapshot")
    _salvar(dia, "gestora_dia")
    _salvar(p.qualidade, "qualidade_dia")
    _salvar(p.anomalias.merge(dim[["cnpj_classe", "nome_classe", "nome_gestor"]], on="cnpj_classe", how="left"),
            "anomalias_pl")
    if classif is not None:
        _salvar(classif, "classificacao_b3")

    meta = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "data_ref": str(p.data_ref.date()),
            "ultima_data": str(p.ultima_data.date()), "primeira_data": str(p.pl.index[0].date()),
            "pregoes": len(p.pl), "pl_total": float(pl_total), "n_gestoras": int(len(gest)),
            "pl_sem_gestora_pct": float(100 * sem_gestor), "janelas_ok": [w for w in JANELAS if len(p.pl) > w],
            "anomalias_pl_obs": int(len(p.anomalias)), "anomalias_pl_classes": int(p.anomalias.cnpj_classe.nunique()),
            "cda_mes": mes_cda}
    if classif is not None:
        seg = s[s.pl > 0].groupby("segmento_b3").agg(classes=("pl", "size"), pl=("pl", "sum"))
        meta["segmentos_b3"] = {k: {"classes": int(r.classes), "pl": float(r.pl)} for k, r in seg.iterrows()}

    if mes_cda:
        base_g, G, aluguel, ok = penetracao(dim, mes_cda)
        _salvar(base_g, "penetracao_gestora")
        _salvar(G, "penetracao_produto")
        _salvar(aluguel, "oportunidade_aluguel")
        mes_pos = cda.mes_com_tickers()
        cob = cda.cobertura_ticker()
        posicoes = posicoes_acoes(dim_com_fic(dim, mes_pos), mes_pos)
        _salvar(posicoes, "posicoes_acoes")
        meta["cda_mes_posicoes"] = mes_pos
        meta["cda_ticker_cobertura_pct"] = {m: round(100 * c, 1) for m, c in cob.items()}
        log.info("cobertura de ticker por mês: %s -> posições por ticker de %s", meta["cda_ticker_cobertura_pct"], mes_pos)
        meta["cda_classes_composicao_ok_pct"] = round(100 * ok, 1)
        meta["cda_pl_base"] = float(base_g.pl_base.sum())
    else:
        posicoes = pd.DataFrame()
        log.warning("sem CDA: penetração e posições em ações não foram geradas")

    pub_path = PARQUET / "publicacoes.parquet"
    if pub_path.exists():
        pub = pd.read_parquet(pub_path)
        if len(posicoes) and "cnpj_companhia" in posicoes:
            carteira = posicoes[posicoes.produto.isin(["acoes", "aluguel_doador"])].groupby("cnpj_companhia").agg(
                n_gestoras=("cnpj_gestor", "nunique"), vl_carteiras=("vl", "sum"))
            pub = pub.merge(carteira, left_on="cnpj_companhia", right_index=True, how="left")
        _salvar(pub, "publicacoes")
        meta["publicacoes_ate"] = str(pub.data_entrega.max().date())
    if (PARQUET / "dim_ticker.parquet").exists():
        _salvar(pd.read_parquet(PARQUET / "dim_ticker.parquet"), "dim_ticker")

    (MARTS / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("marts prontos em %.0fs", (datetime.now() - t_ini).total_seconds())
