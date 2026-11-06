"""Identidade visual do CVM Radar: paleta, CSS e helpers de gráfico.

Paleta: a referência validada do guia de dataviz (hues categóricos em ordem fixa, sequencial azul,
divergente azul <-> vermelho com cinza no meio), nos dois modos. As cores seguem a ENTIDADE, nunca o
ranking: BMF é sempre azul, BOV sempre laranja, BMF + BOV sempre verde-água.
"""
import numpy as np
import plotly.graph_objects as go
import streamlit as st

FONTE = "Segoe UI, system-ui, -apple-system, sans-serif"

CLARO = dict(
    modo="light", pagina="#f9f9f7", superficie="#fcfcfb", tinta="#0b0b0b", tinta2="#52514e", suave="#898781",
    grade="#e1e0d9", eixo="#c3c2b7", neutro="#f0efec", borda="rgba(11,11,11,0.10)",
    cat=["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    pos="#2a78d6", neg="#e34948", azul2="#9ec5f4",
    seq=["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])   # azul 100 -> 700
ESCURO = dict(
    modo="dark", pagina="#0d0d0d", superficie="#1a1a19", tinta="#ffffff", tinta2="#c3c2b7", suave="#898781",
    grade="#2c2c2a", eixo="#383835", neutro="#383835", borda="rgba(255,255,255,0.10)",
    cat=["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
    pos="#3987e5", neg="#e66767", azul2="#9ec5f4",
    seq=["#104281", "#184f95", "#1c5cab", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"])   # escuro -> claro

# Segmento da B3: cor e marcador por entidade (o texto sempre acompanha: cor nunca é o único canal)
SEGMENTOS = ["BMF", "BOV", "BMF + BOV", "Sem BMF/BOV"]
SEG_SLOT = {"BMF": 0, "BOV": 1, "BMF + BOV": 2}
SEG_EMOJI = {"BMF": "🟦", "BOV": "🟧", "BMF + BOV": "🟩", "Sem BMF/BOV": "⬜", "FIC": "⬛", "Sem CDA": "⬜",
             "Sem CDA / só FIC": "⬛"}


def paleta() -> dict:
    """Paleta do modo atual do Streamlit (claro/escuro); claro se ainda não souber."""
    try:
        return ESCURO if st.context.theme.type == "dark" else CLARO
    except Exception:
        return CLARO


def cor_segmento(seg: str | None, p: dict) -> str:
    if seg in SEG_SLOT:
        return p["cat"][SEG_SLOT[seg]]
    return p["eixo"] if seg in ("FIC", "Sem CDA", "Sem CDA / só FIC") else p["suave"]


def hex_rgba(cor: str, alfa: float) -> str:
    h = cor.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alfa})"


def texto_sobre(cor: str) -> str:
    """Tinta clara ou escura, a que contrasta mais com a cor de fundo da barra."""
    c = [int(cor.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#ffffff" if lum < 0.22 else "#0b0b0b"


def rotulo_seg(seg) -> str:
    return f"{SEG_EMOJI.get(seg, '')} {seg}".strip() if isinstance(seg, str) else ""


# --- formatação pt-BR --------------------------------------------------------------------
def br(x: float, casas: int = 1) -> str:
    return f"{x:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def moeda(v: float, casas: int = 1) -> str:
    """R$ 14,2 tri / R$ 2,6 bi / R$ 350 mi"""
    a = abs(v)
    if a >= 1e12:
        return f"R$ {br(v / 1e12, casas)} tri"   # espaços não quebráveis: "R$ 5,3 bi" não quebra linha
    if a >= 1e9:
        return f"R$ {br(v / 1e9, casas)} bi"
    return f"R$ {br(v / 1e6, 0 if a >= 1e8 else casas)} mi"


# --- CSS ---------------------------------------------------------------------------------
def aplicar_css(p: dict) -> None:
    st.markdown(f"""
<style>
.block-container {{ padding-top: 1.3rem; padding-bottom: 3rem; max-width: 1480px; }}
header[data-testid="stHeader"] {{ background: transparent; }}
h1, h2, h3, h4 {{ letter-spacing: -0.015em; }}

/* cabeçalho */
.hero {{ position: relative; overflow: hidden; color: #fff; border-radius: 18px; padding: 26px 32px 22px;
         margin: 0 0 18px; background: linear-gradient(118deg, #0d366b 0%, #1c5cab 56%, #2a78d6 100%); }}
.hero:after {{ content: ""; position: absolute; right: -70px; top: -90px; width: 340px; height: 340px;
               border-radius: 50%; background: radial-gradient(closest-side, rgba(255,255,255,.20), rgba(255,255,255,0)); }}
.hero h1 {{ font-size: 2.15rem; font-weight: 700; margin: 0; padding: 0; color: #fff; line-height: 1.15; }}
.hero .sub {{ font-size: 1.02rem; color: rgba(255,255,255,.86); margin: 6px 0 0; }}
.hero .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 15px; position: relative; z-index: 1; }}
.chip {{ font-size: .8rem; padding: 4px 12px; border-radius: 999px; color: #fff;
         background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.26); }}

/* cartões e tiles */
div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 14px; background: {p['superficie']};
         border-color: {p['borda']}; }}
div[data-testid="stMetricValue"], div[data-testid="stMetricValue"] * {{ font-weight: 650; letter-spacing: -0.02em;
         font-size: clamp(1.45rem, 2.1vw, 2.3rem); }}
div[data-testid="stMetricLabel"] {{ color: {p['tinta2']}; }}

/* abas */
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {p['grade']}; }}
.stTabs [data-baseweb="tab"] {{ height: 46px; padding: 0 18px; font-weight: 600; font-size: 1rem; }}
.stTabs [aria-selected="true"] {{ color: {p['pos']}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {p['pos']}; height: 3px; }}

/* seções */
.secao {{ margin: 22px 0 8px; }}
.secao h3 {{ margin: 0; padding: 0; font-size: 1.25rem; font-weight: 650; }}
.secao p {{ margin: 3px 0 0; color: {p['tinta2']}; font-size: .92rem; }}
.leg {{ display: inline-flex; align-items: center; gap: 6px; margin-right: 18px; font-size: .88rem; color: {p['tinta2']}; }}
.leg i {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; }}
.cartao-t {{ font-weight: 650; font-size: 1.02rem; line-height: 1.25; margin: 0 0 2px; }}
.cartao-s {{ color: {p['tinta2']}; font-size: .86rem; margin: 0 0 8px; }}
.cartao-n {{ font-size: .95rem; margin: 6px 0 10px; }}
.cartao-n b {{ font-weight: 650; }}
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
</style>""", unsafe_allow_html=True)


def hero(titulo: str, subtitulo: str, chips: list[str]) -> None:
    fichas = "".join(f'<span class="chip">{c}</span>' for c in chips)
    st.markdown(f'<div class="hero"><h1>{titulo}</h1><div class="sub">{subtitulo}</div>'
                f'<div class="chips">{fichas}</div></div>', unsafe_allow_html=True)


def secao(titulo: str, legenda: str | None = None) -> None:
    sub = f"<p>{legenda}</p>" if legenda else ""
    st.markdown(f'<div class="secao"><h3>{titulo}</h3>{sub}</div>', unsafe_allow_html=True)


def legenda_segmentos(p: dict, incluir_sem: bool = True) -> None:
    itens = [(s, cor_segmento(s, p)) for s in SEGMENTOS if incluir_sem or s != "Sem BMF/BOV"]
    st.markdown("".join(f'<span class="leg"><i style="background:{c}"></i>{s}</span>' for s, c in itens),
                unsafe_allow_html=True)


# --- gráficos ----------------------------------------------------------------------------
def base(fig: go.Figure, p: dict, altura: int = 320, titulo: str | None = None, grade_x: bool = False,
         grade_y: bool = True, legenda: bool = False) -> go.Figure:
    """Estilo comum: fundo transparente, grade em fio recessivo, eixos discretos, fonte do sistema."""
    topo = 78 if (titulo and legenda) else 46 if (titulo or legenda) else 10   # título e legenda não se sobrepõem
    fig.update_layout(
        height=altura, margin=dict(l=6, r=10, t=topo, b=6), separators=",.",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONTE, color=p["tinta2"], size=12), showlegend=legenda, bargap=0.38,
        title=dict(text=titulo, x=0, xanchor="left", y=0.97, font=dict(size=14.5, color=p["tinta"])) if titulo else None,
        legend=dict(orientation="h", x=0, xanchor="left", y=1.0, yanchor="bottom", bgcolor="rgba(0,0,0,0)",
                    font=dict(size=12, color=p["tinta2"])),
        hoverlabel=dict(bgcolor=p["superficie"], bordercolor=p["eixo"], font=dict(family=FONTE, size=12, color=p["tinta"])))
    fig.update_xaxes(showgrid=grade_x, gridcolor=p["grade"], gridwidth=1, zeroline=False, showline=False,
                     ticks="", tickfont=dict(color=p["suave"], size=11.5), automargin=True)
    fig.update_yaxes(showgrid=grade_y, gridcolor=p["grade"], gridwidth=1, zeroline=False, showline=False,
                     ticks="", tickfont=dict(color=p["suave"], size=11.5), automargin=True)
    return fig


def mostrar(fig: go.Figure, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False}, key=key)


def barras_h(rotulos, valores, p: dict, cor: str | None = None, fmt: str = ",.0f", altura: int = 360,
             titulo: str | None = None, extra=None) -> go.Figure:
    """Barras horizontais de uma cor só (magnitude de itens nominais), valor na ponta."""
    fig = go.Figure(go.Bar(
        y=list(rotulos), x=list(valores), orientation="h", customdata=extra,
        marker=dict(color=cor or p["cat"][0], cornerradius=4),
        text=[br(v, 0 if fmt.endswith(".0f") else 1) for v in valores], textposition="outside",
        cliponaxis=False, textfont=dict(color=p["tinta2"], size=11.5),
        hovertemplate="%{y}<br><b>%{x:" + fmt + "}</b><extra></extra>"))
    base(fig, p, altura, titulo, grade_x=False, grade_y=False)
    fig.update_xaxes(visible=False, range=[0, max(valores) * 1.2 if len(valores) else 1])
    fig.update_yaxes(autorange="reversed")
    return fig


def barras_divergentes(rotulos, valores, p: dict, altura: int = 360, titulo: str | None = None,
                       unidade: str = "bi") -> go.Figure:
    """Positivo azul, negativo vermelho (par divergente do guia), valor na ponta."""
    cores = [p["pos"] if v >= 0 else p["neg"] for v in valores]
    fig = go.Figure(go.Bar(
        y=list(rotulos), x=list(valores), orientation="h", marker=dict(color=cores, cornerradius=4),
        text=[f"{br(v, 1)} {unidade}" for v in valores], textposition="outside", cliponaxis=False,
        textfont=dict(color=p["tinta2"], size=11.5),
        hovertemplate="%{y}<br><b>%{x:,.1f} " + unidade + "</b><extra></extra>"))
    base(fig, p, altura, titulo, grade_x=False, grade_y=False)
    lo, hi = (min(min(valores), 0), max(max(valores), 0)) if len(valores) else (0, 1)
    fig.update_xaxes(visible=False, range=[lo * 1.45 if lo < 0 else 0, hi * 1.45 if hi > 0 else 0])
    fig.update_yaxes(autorange="reversed")
    fig.add_vline(x=0, line_width=1, line_color=p["eixo"])
    return fig


def mapa_calor(z, x, y, p: dict, divergente: bool, fmt: str = ".1f", altura: int = 460, titulo: str | None = None,
               rotulo_barra: str | None = None, hover: str = "%{y}<br>%{x}: <b>%{z:.2f}</b><extra></extra>",
               esconder_abaixo: float | None = None) -> go.Figure:
    """Divergente (polaridade, centrado em 0, cinza no meio) ou sequencial azul (magnitude). Célula separada por
    2px da superfície; escala limitada ao percentil 95 para um outlier não apagar o resto. Com `esconder_abaixo`,
    células de valor pequeno ficam sem número (menos ruído)."""
    z = np.asarray(z, dtype=float)
    if divergente:
        lim = float(np.nanpercentile(np.abs(z), 95)) or 1.0
        escala, zmin, zmax = [[0, p["neg"]], [0.5, p["neutro"]], [1, p["pos"]]], -lim, lim
    else:
        zmax = float(np.nanpercentile(z, 95)) or 1.0
        n = len(p["seq"]) - 1
        escala, zmin = [[i / n, c] for i, c in enumerate(p["seq"])], 0.0
    texto = {}
    if esconder_abaixo is not None:
        formatado = np.vectorize(lambda v: "" if np.isnan(v) or abs(v) < esconder_abaixo
                                 else format(v, fmt).replace(".", ","))(z)
        texto = dict(text=formatado, texttemplate="%{text}")
    else:
        texto = dict(texttemplate="%{z:" + fmt + "}")
    fig = go.Figure(go.Heatmap(
        z=z, x=list(x), y=list(y), colorscale=escala, zmin=zmin, zmax=zmax, xgap=2, ygap=2,
        textfont=dict(size=11, family=FONTE), hovertemplate=hover, **texto,
        colorbar=dict(thickness=9, len=0.85, outlinewidth=0, title=dict(text=rotulo_barra or "", side="right"),
                      tickfont=dict(color=p["suave"], size=11))))
    base(fig, p, altura, titulo, grade_x=False, grade_y=False)
    fig.update_layout(margin=dict(l=6, r=10, t=118 if titulo else 70, b=6))   # espaço para título + rótulos das colunas
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(side="top", showgrid=False, tickangle=-40 if len(list(x)) > 6 else 0)
    return fig
