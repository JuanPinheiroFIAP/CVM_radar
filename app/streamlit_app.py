"""CVM Radar: AuM e captação por gestora, monitor vs peers, penetração por produto e publicações.

    streamlit run app/streamlit_app.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import estilo as E  # noqa: E402
from cvm_pipeline.config import COBERTURA_MIN_DIA, MARTS, ROTULO_PRODUTO, SAIDAS  # noqa: E402

st.set_page_config(page_title="CVM Radar", page_icon="📊", layout="wide")
P = E.paleta()
E.aplicar_css(P)

ROTULO_JANELA = {1: "1 pregão", 5: "5 pregões", 21: "21 pregões (~1 mês)", 63: "63 pregões (~3 meses)",
                 252: "252 pregões (~12 meses)"}
JANELA_CURTA = {1: "1d", 5: "5d", 21: "21d", 63: "63d", 252: "12m"}
JANELA_EXTENSO = {1: "1 dia", 5: "1 semana", 21: "1 mês", 63: "3 meses", 252: "12 meses"}
BASES_AUM = {"AuM total": "aum_total", "AuM ex-FIC (sem dupla contagem)": "aum_ex_fic",
             "AuM de fundos não exclusivos": "aum_nao_exclusivo"}
PRODUTO_CURTO = {  # rótulos de coluna curtos para o mapa de calor (ficam inclinados)
    "acoes": "Ações", "bdr": "BDR", "aluguel_doador": "Aluguel doador", "aluguel_tomador": "Aluguel tomador",
    "opcoes": "Opções", "futuros": "Futuros", "termo": "Termo", "swaps": "Swaps", "titulos_publicos": "Títulos públicos",
    "compromissadas": "Compromissadas", "credito_corporativo": "Crédito corporativo",
    "titulos_bancarios": "Títulos bancários", "cotas_fundos": "Cotas de fundos", "exterior": "Exterior"}
EVENTO_CURTO = {"Aprovação / andamento do programa": "Programa em andamento",
                "Encerramento / cancelamento do programa": "Programa encerrado",
                "Cancelamento de ações em tesouraria": "Ações canceladas"}
SEG_GESTORA = ["BMF + BOV", "BMF", "BOV", "Sem BMF/BOV", "Sem CDA / só FIC"]
SEG_FUNDO = ["BMF + BOV", "BMF", "BOV", "Sem BMF/BOV", "FIC", "Sem CDA"]

if not (MARTS / "meta.json").exists():
    st.error("Ainda não há dados. Rode `atualizar_dados.bat` (ou `python run_pipeline.py`) e recarregue esta página.")
    st.stop()

META = json.loads((MARTS / "meta.json").read_text(encoding="utf-8"))
VERSAO = META["gerado_em"]
JANELAS = META["janelas_ok"]
J_PADRAO = JANELAS.index(63) if 63 in JANELAS else len(JANELAS) - 1
TEM_B3 = "segmentos_b3" in META


@st.cache_data(show_spinner=False)
def ler(nome: str, versao: str) -> pd.DataFrame:
    return pd.read_parquet(MARTS / f"{nome}.parquet")


@st.cache_data(show_spinner=False)
def arquivo(nome: str, versao: str) -> bytes:
    return (SAIDAS / nome).read_bytes()


def T(nome: str) -> pd.DataFrame:
    return ler(nome, VERSAO)


def data_br(s) -> str:
    return pd.Timestamp(s).strftime("%d/%m/%Y")


def mes_br(aaaamm: str) -> str:
    meses = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    return f"{meses[int(aaaamm[4:]) - 1]}/{aaaamm[:4]}"


def nao_nulo(x, padrao=0.0) -> float:
    return padrao if pd.isna(x) else float(x)


def linha_selecionada(evento) -> int | None:
    linhas = evento.selection.rows
    return linhas[0] if linhas else None


def curto(nome: pd.Series, n: int = 30) -> pd.Series:
    """Nome truncado com reticências (aceita série vazia ou só com nulos)."""
    s = pd.Series(nome)
    s = s.astype("object").where(s.notna(), "").astype(str)
    return s.where(s.str.len() <= n, s.str.slice(0, n - 1).str.rstrip() + "…")


def sinal_cor(x: float) -> str:
    return "blue" if x >= 0 else "red"


def cartao():
    return st.container(border=True)


def explicacao_b3(html: bool = False) -> str:
    """Definição de BMF/BOV. `html=True` para cabeçalhos de seção (HTML); senão, Markdown."""
    neg = ("<b>", "</b>") if html else ("**", "**")
    return (f"{neg[0]}BMF{neg[1]} = tem posição em futuros (juros, câmbio, índice, commodities) · {neg[0]}BOV{neg[1]} = "
            "ações, BDR, aluguel, termo ou opções · classificação pelas carteiras (CDA) de " + mes_br(META["cda_mes"]) +
            ". FIC = fundo de cotas (a carteira real está no fundo-alvo).")


@st.cache_data(show_spinner=False)
def mercado(versao: str) -> pd.DataFrame:
    """PL e fluxo líquido do mercado por dia (soma das gestoras)."""
    d = ler("gestora_dia", versao).groupby("dt", as_index=False).agg(pl=("aum_total", "sum"), fluxo=("fluxo_liq", "sum"))
    return d.sort_values("dt")


# =======================================================================================
# Cabeçalho e tiles
# =======================================================================================
E.hero("CVM Radar", "Gestoras, captação e carteiras de fundos com dados públicos da CVM", [
    f"Informe diário até {data_br(META['data_ref'])}",
    f"Carteiras (CDA) de {mes_br(META['cda_mes'])}" if META.get("cda_mes") else "Sem carteiras (CDA)",
    f"Carga de {pd.Timestamp(VERSAO).strftime('%d/%m %H:%M')}", "Fonte: dados.cvm.gov.br"])

fu_all, merc = T("fundo_snapshot"), mercado(VERSAO)
wk = 63 if 63 in JANELAS else JANELAS[-1]
ne = fu_all[~fu_all.exclusivo]
base_ok = ne[f"base_pct_{wk}"]
fluxo_pct = ne.loc[base_ok, f"fluxo_liq_{wk}"].sum() / ne.loc[base_ok, f"pl0_{wk}"].sum()
var_pl = merc.pl.iloc[-1] / merc.pl.iloc[-22] - 1 if len(merc) > 22 else 0.0

k1, k2, k3, k4 = st.columns(4)
k1.metric("PL dos fundos", E.moeda(META["pl_total"]), f"{E.br(var_pl * 100)}% em 21d", delta_color=sinal_cor(var_pl),
          border=True, chart_data=(merc.pl.tail(150) / 1e12).tolist(), chart_type="area",
          help="Soma do PL de todas as classes do informe diário (variação em 21 pregões). "
               "Fundos de fundos contam duas vezes.")
k2.metric(f"Captação {JANELA_CURTA[wk]}", E.moeda(ne[f"fluxo_liq_{wk}"].sum()),
          f"{E.br(fluxo_pct * 100)}% do PL", delta_color=sinal_cor(fluxo_pct), border=True,
          chart_data=(merc.fluxo.tail(wk).cumsum() / 1e9).tolist(), chart_type="area",
          help=f"Captação líquida dos fundos não exclusivos nos últimos {wk} pregões, e % do PL inicial. "
               "A linha é a captação acumulada no período.")
k3.metric("Gestoras", f"{META['n_gestoras']:,}".replace(",", "."), border=True,
          help="Gestoras com ao menos uma classe com PL no informe diário.")
k4.metric("Classes de fundos", f"{int((fu_all.pl > 0).sum()):,}".replace(",", "."), border=True,
          help="Classes com PL na data de referência (inclui FIC e exclusivos).")

t_gest, t_peers, t_pen, t_pub, t_exp = st.tabs(
    ["Gestoras", "Monitor vs peers", "Penetração e BMF/BOV", "Radar de publicações", "Exportar e qualidade"])


# =======================================================================================
# 1. Gestoras: AuM e captação
# =======================================================================================
def aba_gestoras() -> None:
    g = T("gestora_snapshot")
    c = st.columns([2.3, 2.0, 2.3, 1.3])
    busca = c[0].text_input("Buscar gestora ou CNPJ", placeholder="ex.: Verde, 12.330.774", key="g_busca")
    w = c[1].selectbox("Janela de captação", JANELAS, index=J_PADRAO, format_func=ROTULO_JANELA.get, key="g_w")
    base_nome = c[2].selectbox("AuM considerado", list(BASES_AUM), key="g_base")
    min_aum = c[3].number_input("AuM mín. (R$ mi)", min_value=0, value=100, step=50, key="g_min")
    if TEM_B3:
        segs = st.pills("Segmento B3 da gestora", SEG_GESTORA, selection_mode="multi", default=SEG_GESTORA,
                        format_func=E.rotulo_seg, key="g_seg") or SEG_GESTORA
    else:
        segs = None

    base = BASES_AUM[base_nome]
    d0 = g[g[base] >= min_aum * 1e6]
    if segs is not None:
        d0 = d0[d0.segmento_b3.isin(segs)]
    col_fluxo = f"fluxo_liq_{w}"

    if TEM_B3:
        E.legenda_segmentos(P)
        st.caption(explicacao_b3())

    a, b = st.columns(2)
    with a, cartao():
        top = d0.nlargest(12, base)
        cores = [E.cor_segmento(s, P) if TEM_B3 else P["cat"][0] for s in (top.segmento_b3 if TEM_B3 else [None] * len(top))]
        E.mostrar(E.barras_h(curto(top.nome_gestor), top[base] / 1e9, P, cor=cores, altura=420,
                             titulo="Maiores gestoras por AuM (R$ bi)"), key="g_c1")
    with b, cartao():
        x = d0.dropna(subset=[col_fluxo])
        sel = pd.concat([x.nlargest(7, col_fluxo), x.nsmallest(7, col_fluxo)]).drop_duplicates("cnpj_gestor")
        sel = sel.sort_values(col_fluxo, ascending=False)
        E.mostrar(E.barras_divergentes(curto(sel.nome_gestor), sel[col_fluxo] / 1e9, P, altura=420,
                                       titulo=f"Maiores captações e saídas ({JANELA_CURTA[w]}, R$ bi)"), key="g_c2")

    d = d0
    if busca:
        digitos = "".join(ch for ch in busca if ch.isdigit())
        m = d.nome_gestor.str.contains(busca, case=False, regex=False, na=False)
        if digitos:
            m |= d.cnpj_gestor.str.contains(digitos, regex=False, na=False)
        d = d[m]
    ordem = st.radio("Ordenar a tabela por", ["AuM", "Captação (R$)", "Captação (%)", "Excesso vs peers"],
                     horizontal=True, key="g_ordem")
    col_ordem = {"AuM": base, "Captação (R$)": col_fluxo, "Captação (%)": f"fluxo_pct_{w}",
                 "Excesso vs peers": f"excesso_pp_{w}"}[ordem]
    d = d.sort_values(col_ordem, ascending=False, na_position="last").reset_index(drop=True)

    mostra = pd.DataFrame({"Gestora": d.nome_gestor})
    if TEM_B3:
        mostra["Segmento B3"] = d.segmento_b3.map(E.rotulo_seg)
    mostra = mostra.assign(**{
        "AuM (R$ bi)": d[base] / 1e9, "Classes": d.n_classes, "Captação líquida (R$ mi)": d[col_fluxo] / 1e6,
        "Captação (% do PL inicial)": d[f"fluxo_pct_{w}"] * 100, "Esperado pelos peers (%)": d[f"esperado_pct_{w}"] * 100,
        "Excesso (p.p.)": d[f"excesso_pp_{w}"], "Grupo principal": d.peer_group_principal})
    st.caption(f"{len(d)} gestoras. Captação e excesso consideram só fundos não exclusivos. "
               "Clique em uma linha para ver a evolução e os fundos.")
    ev = st.dataframe(
        mostra, width="stretch", hide_index=True, height=380, on_select="rerun", selection_mode="single-row",
        key="tbl_gestoras",
        column_config={
            "AuM (R$ bi)": st.column_config.NumberColumn(format="%.1f"),
            "Captação líquida (R$ mi)": st.column_config.NumberColumn(format="%.0f"),
            "Captação (% do PL inicial)": st.column_config.NumberColumn(format="%.1f"),
            "Esperado pelos peers (%)": st.column_config.NumberColumn(format="%.1f"),
            "Excesso (p.p.)": st.column_config.NumberColumn(format="%+.1f")})

    i = linha_selecionada(ev)
    if i is None:
        return
    r = d.iloc[i]
    E.secao(r.nome_gestor, f"{r.cnpj_gestor_fmt} · grupo principal: {r.peer_group_principal}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("AuM total", E.moeda(r.aum_total), border=True)
    m2.metric("AuM ex-FIC", E.moeda(r.aum_ex_fic), border=True)
    pct_w = r[f"fluxo_pct_{w}"]
    m3.metric(f"Captação líquida ({JANELA_CURTA[w]})", E.moeda(nao_nulo(r[col_fluxo])),
              None if pd.isna(pct_w) else f"{E.br(pct_w * 100)}% do PL inicial",
              delta_color="off" if pd.isna(pct_w) else sinal_cor(pct_w), border=True)
    exc = r[f"excesso_pp_{w}"]
    m4.metric("Excesso vs peers", "n/d" if pd.isna(exc) else f"{E.br(exc)} p.p.", border=True,
              help="Captação da gestora menos o que ela captaria se cada fundo captasse como a média ponderada dos "
                   "peers (mesma classe ANBIMA).")
    if TEM_B3 and pd.notna(r.pct_pl_bmf):
        st.caption(f"{E.rotulo_seg(r.segmento_b3)} · {r.pct_pl_bmf * 100:.0f}% do PL (fundos finais) em classes com "
                   f"futuros (BMF) · {r.pct_pl_bov * 100:.0f}% em classes com ações, opções, termo ou aluguel (BOV).")

    s = T("gestora_dia")
    s = s[s.cnpj_gestor == r.cnpj_gestor].sort_values("dt")
    g1, g2 = st.columns(2)
    with g1, cartao():
        f1 = go.Figure()
        f1.add_scatter(x=s.dt, y=s.aum_total / 1e9, name="AuM total", mode="lines", line=dict(color=P["cat"][0], width=2),
                       hovertemplate="%{x|%d/%m/%Y}<br>AuM total: <b>%{y:,.1f} bi</b><extra></extra>")
        f1.add_scatter(x=s.dt, y=s.aum_ex_fic / 1e9, name="AuM ex-FIC", mode="lines",
                       line=dict(color=P["azul2"], width=2),
                       hovertemplate="%{x|%d/%m/%Y}<br>AuM ex-FIC: <b>%{y:,.1f} bi</b><extra></extra>")
        E.mostrar(E.base(f1, P, 330, "AuM (R$ bi)", legenda=True), key="g_d1")
    with g2, cartao():
        acum = s.fluxo_liq.cumsum() / 1e6
        f2 = go.Figure()
        f2.add_scatter(x=s.dt, y=acum.clip(lower=0), mode="lines", line=dict(width=0), fill="tozeroy",
                       fillcolor=E.hex_rgba(P["pos"], 0.28), name="Acima de zero", hoverinfo="skip")
        f2.add_scatter(x=s.dt, y=acum.clip(upper=0), mode="lines", line=dict(width=0), fill="tozeroy",
                       fillcolor=E.hex_rgba(P["neg"], 0.28), name="Abaixo de zero", hoverinfo="skip")
        f2.add_scatter(x=s.dt, y=acum, mode="lines", line=dict(color=P["tinta2"], width=1.6), name="Captação acumulada",
                       hovertemplate="%{x|%d/%m/%Y}<br>Acumulada: <b>%{y:,.0f} mi</b><extra></extra>")
        E.mostrar(E.base(f2, P, 330, "Captação líquida acumulada (R$ mi, fundos não exclusivos)"), key="g_d2")

    fu = T("fundo_snapshot")
    fu = fu[(fu.cnpj_gestor == r.cnpj_gestor) & (fu.pl > 0)].sort_values("pl", ascending=False)
    st.markdown(f"**Fundos da gestora** ({len(fu)} classes com PL)")
    det = pd.DataFrame({"Classe": fu.nome_classe, "Grupo de peers": fu.peer_group})
    if TEM_B3:
        det["Segmento B3"], det["Fatores BMF"] = fu.segmento_b3.map(E.rotulo_seg), fu.fatores_bmf
    det = det.assign(**{
        "PL (R$ mi)": fu.pl / 1e6, "Captação líquida (R$ mi)": fu[f"fluxo_liq_{w}"] / 1e6,
        "Captação (%)": fu[f"fluxo_pct_{w}"] * 100, "Média peers (%)": fu[f"peer_agregado_{w}"] * 100,
        "Excesso (p.p.)": fu[f"excesso_pp_{w}"], "Percentil no grupo": fu[f"pctl_{w}"],
        "Exclusivo": fu.exclusivo, "FIC": fu.is_fic})
    st.dataframe(det, width="stretch", hide_index=True, height=320, column_config={
        "PL (R$ mi)": st.column_config.NumberColumn(format="%.0f"),
        "Captação líquida (R$ mi)": st.column_config.NumberColumn(format="%.1f"),
        "Captação (%)": st.column_config.NumberColumn(format="%.1f"),
        "Média peers (%)": st.column_config.NumberColumn(format="%.1f"),
        "Excesso (p.p.)": st.column_config.NumberColumn(format="%+.1f"),
        "Percentil no grupo": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})


# =======================================================================================
# 2. Monitor de captação vs peers (fundo a fundo)
# =======================================================================================
def aba_peers() -> None:
    fu, pe = T("fundo_snapshot"), T("peers_captacao")

    # Visão geral: onde o dinheiro entra e sai, por tipo de fundo
    grupos_pl = pe[pe.janela == wk].dropna(subset=["agregado"]).nlargest(18, "pl0").peer_group.tolist()
    mat = pe[pe.peer_group.isin(grupos_pl)].pivot(index="peer_group", columns="janela", values="agregado")
    mat = mat.reindex(index=grupos_pl, columns=JANELAS) * 100
    E.secao("Para onde vai o dinheiro", "Captação média de cada grupo de peers (% do PL inicial, ponderada por PL). "
            "Azul = entrada líquida, vermelho = saída. Os 18 maiores grupos.")
    with cartao():
        fig = E.mapa_calor(mat.values, [JANELA_EXTENSO[j] for j in JANELAS], curto(pd.Series(grupos_pl), 38), P,
                           divergente=True, fmt="+.1f", altura=520, rotulo_barra="% do PL",
                           hover="%{y}<br>%{x}: <b>%{z:+.2f}%</b><extra></extra>")
        E.mostrar(fig, key="p_heat")

    E.secao("Fundo a fundo", "Compare cada fundo com a média dos peers do mesmo grupo.")
    c = st.columns([2.8, 1.7, 1.4, 1.4])
    ordem_grupos = fu[fu.pl > 0].groupby("peer_group").pl.sum().sort_values(ascending=False).index.tolist()
    g_padrao = ordem_grupos.index("Multimercados Macro") if "Multimercados Macro" in ordem_grupos else 0
    grupo = c[0].selectbox("Grupo de peers (classificação ANBIMA)", ordem_grupos, index=g_padrao, key="p_grupo")
    wp = c[1].selectbox("Janela", JANELAS, index=J_PADRAO, format_func=ROTULO_JANELA.get, key="p_w")
    pl_min = c[2].number_input("PL inicial mín. (R$ mi)", min_value=1, value=50, step=10, key="p_min")
    com_excl = c[3].checkbox("Incluir fundos exclusivos", value=False, key="p_excl",
                             help="Fundo exclusivo tem 1 cotista: o fluxo vem em blocos e distorce a comparação.")
    segs = (st.pills("Segmento B3 do fundo", SEG_FUNDO, selection_mode="multi", default=SEG_FUNDO,
                     format_func=E.rotulo_seg, key="p_seg") or SEG_FUNDO) if TEM_B3 else None
    x = fu[(fu.peer_group == grupo) & fu[f"base_pct_{wp}"] & (fu[f"pl0_{wp}"] >= pl_min * 1e6)].copy()
    if not com_excl:
        x = x[~x.exclusivo]
    if segs is not None:
        x = x[x.segmento_b3.isin(segs)]
    st_g = pe[(pe.peer_group == grupo) & (pe.janela == wp)]
    st_g = st_g.iloc[0] if len(st_g) else None
    media = None if st_g is None or pd.isna(st_g.agregado) else float(st_g.agregado)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Fundos na tabela", f"{len(x):,}".replace(",", "."), border=True)
    m2.metric("Média dos peers", "n/d" if media is None else f"{E.br(media * 100)}%", border=True,
              help="Média ponderada por PL: captação líquida do grupo dividida pela soma dos PLs iniciais. Base: "
                   "fundos não exclusivos com PL inicial ≥ R$ 1 mi.")
    m3.metric("Mediana", "n/d" if st_g is None or pd.isna(st_g.mediana) else f"{E.br(st_g.mediana * 100)}%", border=True,
              help="Em janelas curtas a mediana costuma ser 0: a maioria dos fundos não tem movimento.")
    m4.metric("p25 a p75", "n/d" if st_g is None or pd.isna(st_g.p25) else
              f"{st_g.p25 * 100:+.0f}% a {st_g.p75 * 100:+.0f}%", border=True)
    if media is None:
        st.warning("Grupo com poucos fundos: sem benchmark confiável. Mostrando só a captação de cada fundo.")

    x = x.sort_values(f"excesso_pp_{wp}", ascending=False, na_position="last")
    tabela, grafico = st.columns([3, 2])
    with tabela:
        t = pd.DataFrame({"Fundo": x.nome_classe, "Gestora": x.nome_gestor})
        if TEM_B3:
            t["Segmento B3"] = x.segmento_b3.map(E.rotulo_seg)
        t = t.assign(**{"PL (R$ mi)": x.pl / 1e6, "Captação líquida (R$ mi)": x[f"fluxo_liq_{wp}"] / 1e6,
                        "Captação (%)": x[f"fluxo_pct_{wp}"] * 100, "Excesso vs média (p.p.)": x[f"excesso_pp_{wp}"],
                        "Percentil": x[f"pctl_{wp}"]})
        st.dataframe(t, width="stretch", hide_index=True, height=470, column_config={
            "PL (R$ mi)": st.column_config.NumberColumn(format="%.0f"),
            "Captação líquida (R$ mi)": st.column_config.NumberColumn(format="%.1f"),
            "Captação (%)": st.column_config.NumberColumn(format="%.1f"),
            "Excesso vs média (p.p.)": st.column_config.NumberColumn(format="%+.1f"),
            "Percentil": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})
    with grafico, cartao():
        if len(x):
            xp = x.assign(pct=(x[f"fluxo_pct_{wp}"] * 100).clip(-100, 300), pl_mi=x.pl / 1e6,
                          fluxo_mi=x[f"fluxo_liq_{wp}"] / 1e6)
            fig = go.Figure()
            for seg in (SEG_FUNDO if TEM_B3 else [None]):
                xs = xp[xp.segmento_b3 == seg] if seg else xp
                if xs.empty:
                    continue
                fig.add_scatter(
                    x=xs.pl_mi, y=xs.pct, mode="markers", name=seg or "Fundos",
                    marker=dict(size=8, color=E.cor_segmento(seg, P) if seg else P["cat"][0], opacity=0.8,
                                line=dict(width=1.5, color=P["superficie"])),
                    customdata=np.stack([xs.nome_classe.str.slice(0, 48), xs.nome_gestor.fillna("").str.slice(0, 32),
                                         xs.fluxo_mi], axis=-1),
                    hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>PL: %{x:,.0f} mi · captação: "
                                  "%{customdata[2]:,.1f} mi (%{y:.1f}%)<extra></extra>")
            if media is not None:
                fig.add_hline(y=media * 100, line_dash="dash", line_width=1.4, line_color=P["tinta2"],
                              annotation_text="média dos peers", annotation_font_color=P["tinta2"],
                              annotation_position="top left")
            E.base(fig, P, 470, "Captação vs tamanho do fundo", grade_x=True, legenda=TEM_B3)
            fig.update_xaxes(type="log", title=dict(text="PL (R$ mi, escala log)", font=dict(size=11.5)))
            fig.update_yaxes(title=dict(text="Captação (% do PL inicial)", font=dict(size=11.5)))
            E.mostrar(fig, key="p_scatter")

    if len(x):
        gg = x.groupby(["cnpj_gestor", "nome_gestor"], as_index=False).agg(
            fundos=("pl", "size"), pl=("pl", "sum"), fluxo=(f"fluxo_liq_{wp}", "sum"), pl0=(f"pl0_{wp}", "sum"))
        gg["pct"] = gg.fluxo / gg.pl0 * 100
        gg["excesso"] = gg.pct - (media or 0) * 100
        gg = gg.sort_values("fluxo", ascending=False)
        st.markdown("**Gestoras neste grupo** (soma dos fundos da tabela acima)")
        st.dataframe(
            pd.DataFrame({"Gestora": gg.nome_gestor, "Fundos": gg.fundos, "PL (R$ mi)": gg.pl / 1e6,
                          "Captação líquida (R$ mi)": gg.fluxo / 1e6, "Captação (%)": gg.pct,
                          "Excesso vs média (p.p.)": gg.excesso}),
            width="stretch", hide_index=True, height=300, column_config={
                "PL (R$ mi)": st.column_config.NumberColumn(format="%.0f"),
                "Captação líquida (R$ mi)": st.column_config.NumberColumn(format="%.1f"),
                "Captação (%)": st.column_config.NumberColumn(format="%.1f"),
                "Excesso vs média (p.p.)": st.column_config.NumberColumn(format="%+.1f")})


# =======================================================================================
# 3. Penetração por produto e segmento B3 (CDA)
# =======================================================================================
def aba_penetracao() -> None:
    if not META.get("cda_mes"):
        st.warning("Sem dados de carteira (CDA).")
        return
    al, pp, pg = T("oportunidade_aluguel"), T("penetracao_produto"), T("penetracao_gestora")
    fu = T("fundo_snapshot")
    st.caption(f"Carteiras de **{mes_br(META['cda_mes'])}**, incluindo posições confidenciais (agregadas). Só fundos "
               "finais (FIC fica de fora: a carteira real está no fundo-alvo). Peers = mesma classe ANBIMA e faixa de PL.")

    # --- A. Segmento B3 ----------------------------------------------------------------
    if TEM_B3:
        E.secao("BMF ou BOV? O que cada tipo de fundo opera", explicacao_b3(html=True))
        fs = fu[(fu.pl > 0) & ~fu.is_fic & fu.segmento_b3.isin(E.SEGMENTOS)]
        modo = st.segmented_control("Medir por", ["PL", "Nº de classes"], default="PL", key="seg_modo") or "PL"
        esq, dir_ = st.columns([2.2, 1])
        grupos = fs.groupby("peer_group").pl.sum().nlargest(14).index
        agg = fs[fs.peer_group.isin(grupos)].groupby(["peer_group", "segmento_b3"]).agg(pl=("pl", "sum"), n=("pl", "size"))
        mat = agg["pl" if modo == "PL" else "n"].unstack(fill_value=0).reindex(index=grupos, columns=E.SEGMENTOS).fillna(0)
        pct = mat.div(mat.sum(axis=1), axis=0) * 100
        with esq, cartao():
            fig = go.Figure()
            for seg in E.SEGMENTOS:
                cor = E.cor_segmento(seg, P)
                fig.add_bar(y=curto(pd.Series(pct.index), 38), x=pct[seg], name=seg, orientation="h",
                            marker=dict(color=cor, line=dict(color=P["superficie"], width=2)),
                            text=[f"{v:.0f}%" if v >= 10 else "" for v in pct[seg]], textposition="inside",
                            insidetextanchor="middle", textangle=0, textfont=dict(color=E.texto_sobre(cor), size=11.5),
                            hovertemplate="%{y}<br>" + seg + ": <b>%{x:.1f}%</b><extra></extra>")
            E.base(fig, P, 520, f"Segmento B3 por grupo de peers (% do {'PL' if modo == 'PL' else 'nº de classes'})",
                   legenda=True, grade_y=False)
            fig.update_layout(barmode="stack", bargap=0.3)
            fig.update_xaxes(range=[0, 100], ticksuffix="%", showgrid=False)
            fig.update_yaxes(autorange="reversed")
            E.mostrar(fig, key="pen_seg")
        with dir_, cartao():
            tot = pd.DataFrame(META["segmentos_b3"]).T
            ordem = [s for s in SEG_FUNDO if s in tot.index]
            tot = tot.loc[ordem]
            st.markdown("**Mercado todo, por segmento**")
            st.dataframe(pd.DataFrame({
                "Segmento": [E.rotulo_seg(s) for s in tot.index], "Classes": tot.classes.astype(int).to_numpy(),
                "PL (R$ bi)": (tot.pl / 1e9).to_numpy(), "% do PL": (tot.pl / tot.pl.sum() * 100).to_numpy()}),
                width="stretch", hide_index=True, height=250, column_config={
                    "PL (R$ bi)": st.column_config.NumberColumn(format="%.0f"),
                    "% do PL": st.column_config.NumberColumn(format="%.1f")})
            st.caption("Só entram fundos finais e exclusivos incluídos. FIC e classes sem carteira aparecem à parte.")

    # --- B. Aluguel de ações -----------------------------------------------------------
    E.secao("Aluguel de ações: muita ação em carteira, pouco aluguel",
            "Exposição = ações em carteira + ações cedidas. Utilização = cedidas ÷ exposição. Potencial = o que a gestora "
            "cederia se usasse aluguel como seus peers, menos o que cede hoje.")
    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    exp_min = c1.number_input("Exposição mínima em ações (R$ mi)", min_value=1, value=20, step=10, key="a_min")
    so_zero = c2.checkbox("Só quem não aluga nada", value=False, key="a_zero")
    busca_a = c3.text_input("Buscar gestora", key="a_busca")
    a = al[al.exposicao_acoes >= exp_min * 1e6]
    if so_zero:
        a = a[a.sem_aluguel]
    if busca_a:
        a = a[a.nome_gestor.str.contains(busca_a, case=False, regex=False, na=False)]
    a = a.sort_values("potencial_doador_rs", ascending=False)
    with cartao():
        fig = go.Figure()
        alvo = a[a.potencial_doador_rs > 0]
        for nome, df_, cor in (("Já aluga, mas abaixo dos peers", alvo[~alvo.sem_aluguel], P["cat"][0]),
                               ("Não aluga nada", alvo[alvo.sem_aluguel], P["cat"][1])):
            if df_.empty:
                continue
            fig.add_scatter(
                x=df_.exposicao_acoes / 1e6, y=df_.potencial_doador_rs / 1e6, mode="markers", name=nome,
                marker=dict(size=9, color=cor, opacity=0.78, line=dict(width=1.5, color=P["superficie"])),
                customdata=np.stack([df_.nome_gestor.str.slice(0, 40), df_.util_doador * 100, df_.util_peers * 100],
                                    axis=-1),
                hovertemplate="<b>%{customdata[0]}</b><br>Exposição: %{x:,.0f} mi · potencial: %{y:,.0f} mi<br>"
                              "Utilização: %{customdata[1]:.1f}% (peers %{customdata[2]:.1f}%)<extra></extra>")
        top = alvo.head(6)
        fig.add_scatter(x=top.exposicao_acoes / 1e6, y=top.potencial_doador_rs / 1e6, mode="text", showlegend=False,
                        text=curto(top.nome_gestor.str.split().str[:1].str.title(), 14),
                        textposition=["top center", "bottom center", "top left", "bottom right", "top right",
                                      "bottom left"][:len(top)],
                        textfont=dict(size=11, color=P["tinta"]), hoverinfo="skip")
        E.base(fig, P, 430, "Onde há mais espaço para aluguel: exposição em ações x potencial", grade_x=True,
               legenda=True)
        potencias = [0.01, 0.1, 1, 10, 100, 1000, 10000, 100000]
        nomes = ["0,01", "0,1", "1", "10", "100", "1 mil", "10 mil", "100 mil"]
        fig.update_xaxes(type="log", title=dict(text="Exposição em ações (R$ mi, escala log)", font=dict(size=11.5)),
                         tickvals=potencias, ticktext=nomes)
        fig.update_yaxes(type="log", title=dict(text="Potencial de aluguel (R$ mi, log)", font=dict(size=11.5)),
                         tickvals=potencias, ticktext=nomes)
        E.mostrar(fig, key="pen_alu")
        st.caption("Procure o alto à direita: muita ação e muito espaço. Laranja = ainda não aluga nada.")
    st.dataframe(
        pd.DataFrame({
            "Gestora": a.nome_gestor, "Potencial (R$ mi)": a.potencial_doador_rs / 1e6,
            "Exposição (R$ mi)": a.exposicao_acoes / 1e6, "Cedido (R$ mi)": a.doador_vl / 1e6,
            "Utilização (%)": a.util_doador * 100, "Peers (%)": a.util_peers * 100,
            "Tomado (R$ mi)": a.tomador_vl / 1e6, "Classes": a.n_classes_acoes}),
        width="stretch", hide_index=True, height=340, column_config={
            **{c: st.column_config.NumberColumn(format="%.1f") for c in ["Utilização (%)", "Peers (%)"]},
            **{c: st.column_config.NumberColumn(format="%.0f") for c in [
                "Potencial (R$ mi)", "Exposição (R$ mi)", "Cedido (R$ mi)", "Tomado (R$ mi)"]}})

    # --- C. Produtos -------------------------------------------------------------------
    E.secao("Penetração por produto vs peers", "Peso de cada produto no PL dos fundos finais da gestora, comparado ao "
            "que seus peers têm.")
    c1, c2, c3 = st.columns([2, 1.3, 1.6])
    prod = c1.selectbox("Produto (tabela)", list(ROTULO_PRODUTO), format_func=ROTULO_PRODUTO.get, key="pp_prod")
    pl_min_g = c2.number_input("PL mínimo da gestora (R$ mi)", min_value=1, value=200, step=100, key="pp_min")
    vis = c3.segmented_control("Mapa de calor", ["Peso na carteira", "Gap vs peers"], default="Peso na carteira",
                               key="pp_vis") or "Peso na carteira"
    top_g = pg[pg.pl_base >= pl_min_g * 1e6].nlargest(20, "pl_base")
    campo = "peso" if vis == "Peso na carteira" else "gap_pp"
    mat = pp[pp.cnpj_gestor.isin(top_g.cnpj_gestor)].merge(top_g[["cnpj_gestor", "nome_gestor"]], on="cnpj_gestor")
    mat = mat.pivot(index="nome_gestor", columns="produto", values=campo).reindex(
        index=top_g.nome_gestor, columns=list(ROTULO_PRODUTO))
    mat = mat * 100 if campo == "peso" else mat
    with cartao():
        rotulos_x = [PRODUTO_CURTO[k] for k in mat.columns]
        fig = E.mapa_calor(mat.values, rotulos_x, curto(pd.Series(mat.index), 30), P, divergente=campo == "gap_pp",
                           fmt=".0f" if campo == "peso" else "+.1f", altura=640,
                           rotulo_barra="% do PL" if campo == "peso" else "p.p.",
                           hover="%{y}<br>%{x}: <b>%{z:.2f}</b><extra></extra>",
                           esconder_abaixo=0.5 if campo == "peso" else 0.25,
                           titulo=("Peso de cada produto no PL (%), 20 maiores gestoras" if campo == "peso" else
                                   "Gap vs peers (p.p.): azul = tem mais que os peers, vermelho = tem menos"))
        E.mostrar(fig, key="pen_heat")
    if campo == "gap_pp":
        st.caption("No gap, azul = a gestora tem MAIS desse produto que seus peers; vermelho = tem MENOS (espaço para vender).")

    q = pp[(pp.produto == prod) & (pp.pl_base >= pl_min_g * 1e6)].merge(
        pg[["cnpj_gestor", "nome_gestor"]], on="cnpj_gestor").sort_values("gap_rs", ascending=False)
    st.markdown(f"**{ROTULO_PRODUTO[prod]}: gestoras com maior espaço vs peers**")
    st.dataframe(
        pd.DataFrame({
            "Gestora": q.nome_gestor, "PL dos fundos (R$ mi)": q.pl_base / 1e6, "Peso na carteira (%)": q.peso * 100,
            "Peso dos peers (%)": q.peso_peers * 100, "Gap (p.p.)": q.gap_pp, "Gap (R$ mi)": q.gap_rs / 1e6,
            "Adoção (% do PL em fundos que usam)": q.adocao * 100, "Adoção dos peers (%)": q.adocao_peers * 100}),
        width="stretch", hide_index=True, height=300, column_config={
            "PL dos fundos (R$ mi)": st.column_config.NumberColumn(format="%.0f"),
            "Gap (R$ mi)": st.column_config.NumberColumn(format="%.0f"),
            "Gap (p.p.)": st.column_config.NumberColumn(format="%+.2f"),
            "Peso na carteira (%)": st.column_config.NumberColumn(format="%.2f"),
            "Peso dos peers (%)": st.column_config.NumberColumn(format="%.2f"),
            "Adoção (% do PL em fundos que usam)": st.column_config.NumberColumn(format="%.0f"),
            "Adoção dos peers (%)": st.column_config.NumberColumn(format="%.0f")})
    st.caption("Gap > 0: a gestora tem menos desse produto do que seus peers. Gap em R$ = esse déficit × PL.")


# =======================================================================================
# 4. Radar de publicações
# =======================================================================================
def aba_radar() -> None:
    pub, pos, tk = T("publicacoes"), T("posicoes_acoes"), T("dim_ticker")
    ultima = pub.data_entrega.max()

    rec = pub[(pub.tema == "Recompra de ações") & (pub.data_entrega >= ultima - pd.Timedelta(days=30))
              & (pub.evento_recompra == "Aprovação / andamento do programa")]
    rec = rec.sort_values(["vl_carteiras", "data_entrega"], ascending=False, na_position="last").drop_duplicates(
        "cnpj_companhia").head(6)
    E.secao("Recompras recentes", "Programas aprovados ou em andamento nos últimos 30 dias, ordenados pela exposição das "
            "carteiras de fundos nas ações da companhia.")
    if rec.empty:
        st.info("Nenhuma recompra identificada nos últimos 30 dias.")
    else:
        colunas = st.columns(3)
        for i, (_, r) in enumerate(rec.iterrows()):
            with colunas[i % 3], cartao():
                st.markdown(f'<div class="cartao-t">{str(r.companhia).title()}</div>'
                            f'<div class="cartao-s">{data_br(r.data_entrega)} · {r.categoria}</div>', unsafe_allow_html=True)
                st.badge(EVENTO_CURTO.get(r.evento_recompra, r.evento_recompra), color="blue")
                if pd.notna(r.n_gestoras):
                    st.markdown(f'<div class="cartao-n"><b>{int(r.n_gestoras)}</b> gestoras com posição · '
                                f'<b>{E.moeda(r.vl_carteiras)}</b> em carteiras</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="cartao-n">Sem posição identificada nas carteiras</div>', unsafe_allow_html=True)
                st.link_button("Abrir documento", r.link, width="stretch")

    E.secao("Todas as publicações", "Recompra é identificada pelo texto do assunto (heurística): confira o documento. "
            "Clique em uma linha para ver quem tem a ação em carteira.")
    TEMAS_PADRAO = ["Fato relevante", "Recompra de ações", "Assembleia", "Aviso aos acionistas", "Comunicado ao mercado"]
    c1, c2, c3 = st.columns([1.6, 2.6, 2])
    periodo = c1.date_input("Período de entrega", value=(ultima - pd.Timedelta(days=14), ultima), max_value=ultima,
                            key="r_per")
    temas = c2.multiselect("Tema", sorted(pub.tema.unique()), default=[t for t in TEMAS_PADRAO if t in set(pub.tema)],
                           key="r_tema")
    busca_p = c3.text_input("Buscar companhia ou assunto", key="r_busca")
    c4, c5 = st.columns([1.4, 3])
    so_carteira = c4.checkbox("Só companhias com posição nas carteiras", value=False, key="r_cart")
    sub = c5.multiselect("Recompra: tipo de evento", sorted(pub.evento_recompra.dropna().unique()), key="r_sub")

    p = pub[pub.tema.isin(temas)]
    if len(periodo) == 2:
        p = p[(p.data_entrega >= pd.Timestamp(periodo[0])) & (p.data_entrega <= pd.Timestamp(periodo[1]))]
    if busca_p:
        p = p[p.companhia.str.contains(busca_p, case=False, regex=False, na=False)
              | p.assunto.str.contains(busca_p, case=False, regex=False, na=False)]
    if so_carteira:
        p = p[p.n_gestoras.notna()]
    if sub:
        p = p[p.evento_recompra.isin(sub)]
    p = p.sort_values(["data_entrega", "vl_carteiras"], ascending=[False, False], na_position="last").reset_index(drop=True)

    st.caption(f"{len(p)} publicações.")
    ev3 = st.dataframe(
        pd.DataFrame({
            "Data": p.data_entrega.dt.date, "Companhia": p.companhia, "Tema": p.tema,
            "Documento": p.especie.fillna(""), "Evento": p.evento_recompra.fillna(""),
            "Assunto": p.assunto.fillna("").str.replace("||", " • ", regex=False).str.slice(0, 160),
            "Gestoras com posição": p.n_gestoras, "Em carteiras (R$ mi)": p.vl_carteiras / 1e6, "Link": p.link}),
        width="stretch", hide_index=True, height=380, on_select="rerun", selection_mode="single-row", key="tbl_pub",
        column_config={
            "Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Em carteiras (R$ mi)": st.column_config.NumberColumn(format="%.0f"),
            "Gestoras com posição": st.column_config.NumberColumn(format="%.0f"),
            "Link": st.column_config.LinkColumn(display_text="abrir")})
    k = linha_selecionada(ev3)
    if k is None:
        return
    r = p.iloc[k]
    E.secao(str(r.companhia), f"{r.tema} · {data_br(r.data_entrega)}")
    st.write(str(r.assunto).replace("||", "\n\n- "))
    tickers = tk[tk.cnpj_companhia == r.cnpj_companhia].ticker.tolist()
    h = pos[pos.ticker.isin(tickers) & pos.produto.isin(["acoes", "aluguel_doador"])]
    if h.empty:
        st.info("Nenhuma gestora com posição identificada por ticker nesta companhia "
                f"(tickers: {', '.join(tickers) or 'não mapeados'}).")
        return
    h = (h.groupby(["cnpj_gestor", "nome_gestor"], as_index=False).agg(vl=("vl", "sum"), qt=("qt", "sum"),
                                                                      classes=("n_classes", "sum"))
         .sort_values("vl", ascending=False))
    st.markdown(f"**Gestoras com posição em {', '.join(tickers)}**: {len(h)} gestoras, "
                f"{E.moeda(h.vl.sum())} (carteiras de {mes_br(META.get('cda_mes_posicoes', META['cda_mes']))})")
    st.dataframe(pd.DataFrame({"Gestora": h.nome_gestor, "Posição (R$ mi)": h.vl / 1e6, "Quantidade": h.qt,
                               "Classes": h.classes}),
                 width="stretch", hide_index=True, height=300,
                 column_config={"Posição (R$ mi)": st.column_config.NumberColumn(format="%.1f"),
                                "Quantidade": st.column_config.NumberColumn(format="%.0f")})
    cob = META.get("cda_ticker_cobertura_pct", {}).get(META.get("cda_mes_posicoes", ""), None)
    st.caption("Piso, não total: posições sob sigilo (até 90 dias) vêm agregadas, sem ticker."
               + ("" if cob is None else f" Neste mês, {cob:.0f}% da exposição em ações tem ticker."))


# =======================================================================================
# 5. Exportação e qualidade dos dados
# =======================================================================================
def aba_exportar() -> None:
    E.secao("Arquivos", f"Gerados a cada atualização na pasta do projeto: {SAIDAS}")
    arquivos = [("CVM_Radar.xlsx", "Planilha Excel (todas as tabelas)",
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                ("salesforce_gestoras.csv", "CSV Salesforce: gestoras", "text/csv"),
                ("salesforce_fundos.csv", "CSV Salesforce: fundos", "text/csv")]
    colunas = st.columns(3)
    for col, (nome, rotulo, mime) in zip(colunas, arquivos):
        with col, cartao():
            if (SAIDAS / nome).exists():
                st.markdown(f"**{rotulo}**")
                st.caption(f"{nome} · {(SAIDAS / nome).stat().st_size / 1e6:.1f} MB")
                st.download_button("Baixar", arquivo(nome, VERSAO), file_name=nome, mime=mime, key=nome, width="stretch")
            else:
                st.info(f"{nome} ainda não foi gerado (rode a etapa `exportar`).")
    st.caption("CSV do Salesforce: chave = CNPJ da gestora; valores em R$, percentuais em pontos, UTF-8, ponto decimal.")

    E.secao("Qualidade dos dados")
    q = T("qualidade_dia")
    q = q[q.util]
    fig = go.Figure(go.Bar(x=q.dt, y=q.cobertura * 100, marker=dict(color=P["cat"][0], cornerradius=2),
                           hovertemplate="%{x|%d/%m/%Y}: <b>%{y:.1f}%</b> dos fundos<extra></extra>"))
    fig.add_hline(y=COBERTURA_MIN_DIA * 100, line_dash="dash", line_width=1.4, line_color=P["tinta2"],
                  annotation_text="limite de dia fechado", annotation_position="bottom left",
                  annotation_font_color=P["tinta2"])
    E.base(fig, P, 280, "Cobertura do informe diário (% dos fundos típicos que reportaram)")
    fig.update_yaxes(range=[80, 101], ticksuffix="%")
    with cartao():
        E.mostrar(fig, key="q_cob")
    st.caption("Os últimos dias sempre aparecem incompletos: parte dos fundos entrega com 2 a 4 dias de atraso. "
               f"A base usa o último dia com ≥{COBERTURA_MIN_DIA:.0%}; o PL de quem ainda não entregou é mantido por até 5 pregões.")

    q1, q2, q3 = st.columns(3)
    q1.metric("PL sem gestora identificada", f"{META['pl_sem_gestora_pct']:.3f}%", border=True)
    q2.metric("Carteiras que fecham com o PL (±5%)", f"{META.get('cda_classes_composicao_ok_pct', 0):.0f}% das classes",
              border=True)
    q3.metric("Classes com erro tratado", f"{META['anomalias_pl_classes']}", border=True)
    cob = META.get("cda_ticker_cobertura_pct", {})
    if cob:
        st.caption("Exposição em ações identificada por ticker, por mês do CDA: "
                   + " · ".join(f"{m}: {c:.0f}%" for m, c in cob.items())
                   + f". O cruzamento por ticker usa {META.get('cda_mes_posicoes')}.")

    an = T("anomalias_pl")
    with st.expander(f"Erros de preenchimento tratados ({len(an)} observações)"):
        st.write("PL que pulou sem captação que o explique (ex.: fundo de R$ 0,15 bi informando R$ 824 bi) é "
                 "descartado; captação/resgate do tamanho do PL com o PL parado é zerado. Os maiores:")
        top = an.sort_values("pl_informado", ascending=False).head(50)
        st.dataframe(
            pd.DataFrame({"Data": top.dt.dt.date, "Tipo": top.tipo, "Classe": top.nome_classe, "Gestora": top.nome_gestor,
                          "PL informado (R$ mi)": top.pl_informado / 1e6,
                          "PL válido anterior (R$ mi)": top.pl_valido_anterior / 1e6,
                          "Fluxo informado (R$ mi)": top.fluxo_liq_informado / 1e6}),
            width="stretch", hide_index=True, height=300,
            column_config={c: st.column_config.NumberColumn(format="%.1f") for c in [
                "PL informado (R$ mi)", "PL válido anterior (R$ mi)", "Fluxo informado (R$ mi)"]})

    with st.expander("Como ler os números"):
        st.markdown("""
- **Segmento B3**: **BMF** = a classe tem posição em futuros (juros, câmbio, índice, commodities). **BOV** = renda variável direta
  (ações, units, BDR, ações cedidas) ≥ 0,5% do PL, ou qualquer posição em opções, termo ou aluguel tomador. **BMF + BOV** = os dois.
  **Sem BMF/BOV** = renda fixa, crédito, cotas. Vem do CDA mensal (inclui posições confidenciais, sem o contrato). A gestora é BMF/BOV
  quando ≥ 5% do PL dos seus fundos finais está em classes do segmento.
- **AuM total**: soma do PL das classes (informe diário). Fundos de fundos contam duas vezes. **AuM ex-FIC** retira as classes de cotas
  (registro ou ≥95% do PL em cotas de outros fundos).
- **Captação líquida** = captação − resgates. **% do PL inicial** = captação líquida ÷ PL do dia anterior à janela. Classes que captaram
  mais de 3× o PL inicial (lançamentos) e PL inicial < R$ 1 mi ficam fora das bases em %.
- **Fundos exclusivos** ficam fora de peers e de captação % (um cotista só gera fluxo em blocos), mas entram no AuM.
- **Média dos peers**: captação líquida do grupo ÷ PL inicial do grupo (mesma classe ANBIMA, não exclusivos). **Esperado** (gestora) =
  quanto ela teria captado se cada fundo captasse como a média do seu grupo. **Excesso** = captação − esperado, em p.p.
- **Penetração**: peso do produto no PL dos fundos finais da gestora, vs peers (classe ANBIMA + faixa de PL). **Aluguel**: exposição =
  ações em carteira + ações cedidas; utilização = cedidas ÷ exposição.
- **Limitações**: o informe diário tem 2 a 4 dias de defasagem; carteiras (CDA) são mensais e posições sob sigilo só têm ticker após
  até 90 dias; a CVM contém erros de preenchimento (tratados acima, mas podem sobrar casos pequenos).
        """)


with t_gest:
    aba_gestoras()
with t_peers:
    aba_peers()
with t_pen:
    aba_penetracao()
with t_pub:
    aba_radar()
with t_exp:
    aba_exportar()
