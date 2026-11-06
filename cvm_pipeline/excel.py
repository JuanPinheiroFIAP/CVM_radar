"""Planilha Excel para olhar os dados.

Os CSVs do Salesforce não abrem bem no Excel em português (separador vírgula, UTF-8 sem BOM):
aqui estão as mesmas informações formatadas, com filtros, cabeçalho fixo e links clicáveis.
"""
import json
import logging

import pandas as pd
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import MARTS, ROTULO_PRODUTO, SAIDAS

log = logging.getLogger("cvm")

# Cores da paleta do dashboard: azul 700 (cabeçalho), azul 450 (positivo), azul 250 (barras), vermelho (negativo)
AZUL_ESCURO, AZUL, AZUL_CLARO, VERMELHO = "0D366B", "2A78D6", "86B6EF", "E34948"
TEMAS_RADAR = ["Fato relevante", "Recompra de ações", "Assembleia", "Aviso aos acionistas",
               "Comunicado ao mercado", "OPA", "Oferta pública", "Proventos e eventos"]

BI, MI, INT, PCT, PP, DATA = "#,##0.0", "#,##0;-#,##0;0", "#,##0", "0.0%", "+0.0;-0.0;0.0", "dd/mm/yyyy"
ROTULO_JANELA = {1: "1d", 5: "5d", 21: "21d", 63: "63d", 252: "12m"}


def _ler(nome: str) -> pd.DataFrame:
    return pd.read_parquet(MARTS / f"{nome}.parquet")


def _escrever(w: pd.ExcelWriter, nome: str, df: pd.DataFrame, formatos: dict | None = None,
              barras: tuple = (), sinais: tuple = (), link: str | None = None, destaque: tuple = ()) -> None:
    df.to_excel(w, sheet_name=nome, index=False)
    ws, n = w.sheets[nome], len(df) + 1
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor=AZUL_ESCURO)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(df.shape[1])}{n}"
    for j, col in enumerate(df.columns, start=1):
        letra = get_column_letter(j)
        amostra = int(df[col].head(300).astype(str).str.len().max()) if len(df) else 0
        ws.column_dimensions[letra].width = min(max(len(str(col)) * 0.9 + 2, amostra + 2, 10), 46)
        if formatos and col in formatos:
            for (cel,) in ws.iter_rows(min_row=2, max_row=n, min_col=j, max_col=j):
                cel.number_format = formatos[col]
        if col in barras:
            ws.conditional_formatting.add(f"{letra}2:{letra}{n}",
                                          DataBarRule(start_type="min", end_type="max", color=AZUL_CLARO))
        if col in destaque:  # célula preenchida fica amarela (alertas)
            ws.conditional_formatting.add(f"{letra}2:{letra}{n}", FormulaRule(
                formula=[f"LEN({letra}2)>0"], fill=PatternFill(bgColor="FFF2CC", fill_type="solid")))
        if col in sinais:
            for op, cor in (("greaterThan", AZUL), ("lessThan", VERMELHO)):
                ws.conditional_formatting.add(f"{letra}2:{letra}{n}",
                                              CellIsRule(operator=op, formula=["0"], font=Font(color=cor)))
    if link and link in df.columns:
        j = list(df.columns).index(link) + 1
        for i, url in enumerate(df[link], start=2):
            if isinstance(url, str) and url.startswith("http"):
                cel = ws.cell(row=i, column=j)
                cel.hyperlink, cel.value, cel.font = url, "abrir", Font(color=AZUL, underline="single")


def _leia_me(w: pd.ExcelWriter, meta: dict) -> None:
    br = lambda d: pd.Timestamp(d).strftime("%d/%m/%Y")  # noqa: E731
    linhas = [
        ("CVM Radar: tabelas para consulta", "titulo"),
        (f"Dados públicos da CVM. Informe diário até {br(meta['data_ref'])} (há dados parciais de "
         f"{br(meta['ultima_data'])}); carteiras (CDA) de {meta.get('cda_mes', 'n/d')}; gerado em "
         f"{pd.Timestamp(meta['gerado_em']).strftime('%d/%m/%Y %H:%M')}.", None),
        ("", None),
        ("Abas", "negrito"),
        ("Gestoras: AuM, segmento B3 (BMF/BOV) e captação por gestora.", None),
        ("Fundos: uma linha por classe com PL, segmento B3, captação e comparação com os peers.", None),
        ("Peers: captação média por grupo ANBIMA e janela.", None),
        ("Aluguel: gestoras com muita ação em carteira e pouco aluguel.", None),
        ("Penetração - peso e gap: peso de cada produto no PL dos fundos finais e diferença para os peers.", None),
        ("Publicações: fatos relevantes, recompras, assembleias e afins dos últimos 30 dias.", None),
        ("", None),
        ("Como ler", "negrito"),
        ("Segmento B3: BMF = tem posição em futuros (juros, câmbio, índice, commodities). BOV = ações, BDR, aluguel, "
         "termo ou opções (renda variável direta >= 0,5% do PL). BMF + BOV = os dois. Sem BMF/BOV = renda fixa, "
         "crédito, cotas. FIC = fundo de cotas (a carteira real está no fundo-alvo). Vem do CDA do mês indicado.", None),
        ("AuM total soma o PL das classes; AuM ex-FIC retira fundos de cotas (evita dupla contagem).", None),
        ("Captação líquida = captação - resgates. % = captação líquida / PL do dia anterior à janela. "
         "Janelas em pregões: 21 (~1 mês), 63 (~3 meses), 252 (~12 meses).", None),
        ("Excesso (p.p.) = captação menos o que a gestora/fundo captaria se rendesse a média ponderada dos peers "
         "(mesma classe ANBIMA, só fundos não exclusivos).", None),
        ("Limitações: o informe diário chega com 2 a 4 dias de atraso; carteiras são mensais e posições sob sigilo "
         "(até 90 dias) vêm agregadas, sem ticker; a CVM tem erros de preenchimento (tratados, mas podem sobrar casos).",
         None),
    ]
    ws = w.book.create_sheet("Leia-me", 0)
    ws.column_dimensions["A"].width = 130
    ws.sheet_view.showGridLines = False
    for i, (texto, estilo) in enumerate(linhas, start=1):
        cel = ws.cell(row=i, column=1, value=texto)
        cel.alignment = Alignment(wrap_text=True, vertical="top")
        if estilo == "titulo":
            cel.font = Font(bold=True, size=16, color=AZUL_ESCURO)
        elif estilo == "negrito":
            cel.font = Font(bold=True, color=AZUL_ESCURO)


def gerar() -> None:
    meta = json.loads((MARTS / "meta.json").read_text(encoding="utf-8"))
    janelas = [w for w in (21, 63, 252) if w in meta["janelas_ok"]]
    SAIDAS.mkdir(parents=True, exist_ok=True)
    destino, tmp = SAIDAS / "CVM_Radar.xlsx", SAIDAS / "CVM_Radar.tmp.xlsx"
    com_b3 = "segmentos_b3" in meta

    with pd.ExcelWriter(tmp, engine="openpyxl") as w:
        # --- Gestoras ---------------------------------------------------------------------
        g = _ler("gestora_snapshot").sort_values("aum_total", ascending=False)
        d = {"Gestora": g.nome_gestor, "CNPJ": g.cnpj_gestor_fmt}
        if com_b3:
            d.update({"Segmento B3": g.segmento_b3, "% PL em BMF": g.pct_pl_bmf, "% PL em BOV": g.pct_pl_bov})
        d.update({"AuM total (R$ bi)": g.aum_total / 1e9, "AuM ex-FIC (R$ bi)": g.aum_ex_fic / 1e9,
                  "Classes": g.n_classes, "Grupo principal": g.peer_group_principal})
        fmt = {"AuM total (R$ bi)": BI, "AuM ex-FIC (R$ bi)": BI, "Classes": INT, "% PL em BMF": PCT, "% PL em BOV": PCT}
        sinais = []
        for j in janelas:
            s = ROTULO_JANELA[j]
            d[f"Captação {s} (R$ mi)"] = g[f"fluxo_liq_{j}"] / 1e6
            d[f"Captação {s} (%)"] = g[f"fluxo_pct_{j}"]
            d[f"Excesso {s} (p.p.)"] = g[f"excesso_pp_{j}"]
            fmt.update({f"Captação {s} (R$ mi)": MI, f"Captação {s} (%)": PCT, f"Excesso {s} (p.p.)": PP})
            sinais += [f"Captação {s} (R$ mi)", f"Captação {s} (%)", f"Excesso {s} (p.p.)"]
        _escrever(w, "Gestoras", pd.DataFrame(d), fmt, barras=("AuM total (R$ bi)",), sinais=tuple(sinais))

        # --- Fundos -----------------------------------------------------------------------
        f = _ler("fundo_snapshot")
        f = f[(f.pl > 0) & f.cnpj_gestor.notna()].sort_values("pl", ascending=False)
        cnpj = f.cnpj_classe.str.replace(r"^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$", r"\1.\2.\3/\4-\5", regex=True)
        d = {"Classe": f.nome_classe, "Gestora": f.nome_gestor, "CNPJ da classe": cnpj, "Grupo de peers": f.peer_group}
        if com_b3:
            d.update({"Segmento B3": f.segmento_b3, "Fatores BMF": f.fatores_bmf, "Renda variável (% PL)": f.pct_rv})
        d.update({"PL (R$ mi)": f.pl / 1e6, "Cotistas": f.cotistas})
        fmt = {"PL (R$ mi)": INT, "Cotistas": INT, "Renda variável (% PL)": PCT}
        sinais = []
        for j in janelas:
            s = ROTULO_JANELA[j]
            d[f"Captação {s} (R$ mi)"] = f[f"fluxo_liq_{j}"] / 1e6
            d[f"Captação {s} (%)"] = f[f"fluxo_pct_{j}"]
            d[f"Excesso {s} (p.p.)"] = f[f"excesso_pp_{j}"]
            d[f"Percentil {s}"] = f[f"pctl_{j}"]
            fmt.update({f"Captação {s} (R$ mi)": "#,##0.0;-#,##0.0;0.0", f"Captação {s} (%)": PCT,
                        f"Excesso {s} (p.p.)": PP, f"Percentil {s}": "0.00"})
            sinais += [f"Captação {s} (R$ mi)", f"Captação {s} (%)", f"Excesso {s} (p.p.)"]
        d.update({"Exclusivo": f.exclusivo.map({True: "Sim", False: "Não"}),
                  "FIC": f.is_fic.map({True: "Sim", False: "Não"})})
        _escrever(w, "Fundos", pd.DataFrame(d), fmt, barras=("PL (R$ mi)",), sinais=tuple(sinais))

        # --- Peers ------------------------------------------------------------------------
        pe = _ler("peers_captacao")
        ref = pe[pe.janela == (63 if 63 in janelas else janelas[0])].set_index("peer_group")
        d = {"Grupo (ANBIMA)": ref.index, "Fundos na base": ref.n.to_numpy(), "PL inicial (R$ bi)": (ref.pl0 / 1e9).to_numpy()}
        fmt = {"Fundos na base": INT, "PL inicial (R$ bi)": BI}
        for j in janelas:
            s = ROTULO_JANELA[j]
            d[f"Média {s}"] = pe[pe.janela == j].set_index("peer_group").agregado.reindex(ref.index).to_numpy()
            fmt[f"Média {s}"] = PCT
        d.update({"Mediana": ref.mediana.to_numpy(), "p25": ref.p25.to_numpy(), "p75": ref.p75.to_numpy()})
        fmt.update({"Mediana": PCT, "p25": PCT, "p75": PCT})
        peers = pd.DataFrame(d).sort_values("PL inicial (R$ bi)", ascending=False)
        _escrever(w, "Peers", peers, fmt, barras=("PL inicial (R$ bi)",),
                  sinais=tuple(c for c in peers.columns if c.startswith("Média")))

        # --- Aluguel, penetração, publicações -------------------------------------------------
        if (MARTS / "oportunidade_aluguel.parquet").exists():
            a = _ler("oportunidade_aluguel").sort_values("potencial_doador_rs", ascending=False)
            al = pd.DataFrame({
                "Gestora": a.nome_gestor, "Potencial (R$ mi)": a.potencial_doador_rs / 1e6,
                "Exposição em ações (R$ mi)": a.exposicao_acoes / 1e6, "Cedido em aluguel (R$ mi)": a.doador_vl / 1e6,
                "Utilização": a.util_doador, "Utilização dos peers": a.util_peers,
                "Tomado em aluguel (R$ mi)": a.tomador_vl / 1e6, "Classes com ações": a.n_classes_acoes,
                "Oportunidade": a.oportunidade.map({True: "Sim", False: "Não"})})
            _escrever(w, "Aluguel", al, {"Potencial (R$ mi)": INT, "Exposição em ações (R$ mi)": INT,
                                         "Cedido em aluguel (R$ mi)": INT, "Tomado em aluguel (R$ mi)": INT,
                                         "Utilização": PCT, "Utilização dos peers": PCT},
                      barras=("Potencial (R$ mi)",))

            pp = _ler("penetracao_produto").merge(_ler("penetracao_gestora")[["cnpj_gestor", "nome_gestor"]], on="cnpj_gestor")
            pp["produto"] = pp.produto.map(ROTULO_PRODUTO)
            ordem = [ROTULO_PRODUTO[k] for k in ROTULO_PRODUTO]
            for aba, campo, formato in (("Penetração - peso", "peso", PCT), ("Penetração - gap", "gap_pp", PP)):
                larga = pp.pivot(index=["nome_gestor", "pl_base"], columns="produto", values=campo)[ordem].reset_index()
                larga = larga.sort_values("pl_base", ascending=False).rename(
                    columns={"nome_gestor": "Gestora", "pl_base": "PL dos fundos (R$ mi)"})
                larga["PL dos fundos (R$ mi)"] = larga["PL dos fundos (R$ mi)"] / 1e6
                _escrever(w, aba, larga, {"PL dos fundos (R$ mi)": INT, **{c: formato for c in ordem}},
                          barras=("PL dos fundos (R$ mi)",), sinais=tuple(ordem) if campo == "gap_pp" else ())

        if (MARTS / "publicacoes.parquet").exists():
            pub = _ler("publicacoes")
            pub = pub[(pub.data_entrega >= pub.data_entrega.max() - pd.Timedelta(days=30)) & pub.tema.isin(TEMAS_RADAR)]
            pub = pub.sort_values(["data_entrega", "vl_carteiras"], ascending=[False, False])
            pb = pd.DataFrame({
                "Data": pub.data_entrega.dt.date, "Companhia": pub.companhia, "Tema": pub.tema,
                "Documento": pub.especie, "Evento de recompra": pub.evento_recompra,
                "Assunto": pub.assunto.fillna("").str.replace("||", " • ", regex=False).str.slice(0, 300),
                "Gestoras com posição": pub.n_gestoras, "Em carteiras (R$ mi)": pub.vl_carteiras / 1e6,
                "Link": pub.link})
            _escrever(w, "Publicações", pb, {"Data": DATA, "Gestoras com posição": INT, "Em carteiras (R$ mi)": INT},
                      link="Link")
        _leia_me(w, meta)

    try:
        tmp.replace(destino)
        log.info("CVM_Radar.xlsx gerado em %s", destino)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        log.warning("CVM_Radar.xlsx está aberto no Excel. Feche-o e rode: python run_pipeline.py --so exportar")
