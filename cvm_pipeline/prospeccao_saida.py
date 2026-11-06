"""Saída do cruzamento: planilha Prospeccao_BOV.xlsx e o arquivo de correções para o analista preencher."""
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .excel import BI, INT, MI, PCT, PP, _escrever

log = logging.getLogger("cvm")

AUM_ROTULO = {"aum_total": "AuM total", "aum_ex_fic": "AuM ex-FIC", "aum_nao_exclusivo": "AuM não exclusivo"}
VINCULO_ROTULO = {"alta": "alta", "média": "média: conferir", "ambígua": "ambígua: conferir", "baixa": "fraca: conferir",
                  "sem vínculo": "sem vínculo na CVM", "ignorado": "ignorado"}


def _sim_nao(s: pd.Series) -> pd.Series:
    return s.map({True: "Sim", False: "Não"})


def tabela_lista(df: pd.DataFrame, aum: str, com_motivo: bool) -> tuple[pd.DataFrame, dict]:
    """Colunas legíveis para o time de BOV, com os formatos de cada uma."""
    d = {"Posição": np.arange(1, len(df) + 1), "Cliente": df.cliente}
    if com_motivo:
        d["Por que abordar"] = df.motivos
    d["Atenção"] = np.where(df.parecido_bov != "", "Nome parecido com conta BOV: " + df.parecido_bov,
                            np.where(df.bov_fraco_mesma_gestora, "Possível conta BOV (vínculo fraco com a mesma gestora): conferir", ""))
    d.update({"Grupo(s)": df.grupos, "Opera BMF": _sim_nao(df.opera_bmf), "Institucional": _sim_nao(df.institucional),
              "Prospect": _sim_nao(df.prospect), "Contas BMF": df.contas_bmf, "CNPJ (base institucional)": df.cnpj_inst,
              "Gestora na CVM": df.get("nome_gestor"), "CNPJ da gestora": df.get("cnpj_gestor_fmt"),
              "Vínculo com a CVM": df.confianca.map(VINCULO_ROTULO),
              f"{AUM_ROTULO.get(aum, aum)} (R$ bi)": df[aum] / 1e9})
    outros = [c for c in ("aum_total", "aum_ex_fic", "aum_nao_exclusivo") if c != aum and c in df]
    for c in outros:
        d[f"{AUM_ROTULO[c]} (R$ bi)"] = df[c] / 1e9
    d["Classes de fundos"] = df.get("n_classes")
    if "segmento_b3" in df:
        d["Segmento B3 (CVM)"] = df.segmento_b3
        d["% PL em BMF (CVM)"] = df.pct_pl_bmf
        d["% PL em BOV (CVM)"] = df.pct_pl_bov
    if "exposicao_acoes" in df:
        d["Exposição em ações (R$ mi)"] = df.exposicao_acoes / 1e6
        d["Aluguel: utilização"] = df.get("util_doador")
        d["Aluguel: potencial (R$ mi)"] = df.potencial_doador_rs / 1e6
    if "fluxo_liq_63" in df:
        d["Captação 63d (R$ mi)"] = df.fluxo_liq_63 / 1e6
        d["Captação 63d (%)"] = df.fluxo_pct_63
        d["Excesso vs peers 63d (p.p.)"] = df.excesso_pp_63
    d["Grupo de peers"] = df.get("peer_group_principal")
    d["Outros nomes do cliente"] = df.outros_nomes
    tab = pd.DataFrame(d)
    fmt = {f"{AUM_ROTULO[c]} (R$ bi)": BI for c in AUM_ROTULO} | {
        "Classes de fundos": INT, "% PL em BMF (CVM)": PCT, "% PL em BOV (CVM)": PCT, "Exposição em ações (R$ mi)": INT,
        "Aluguel: utilização": PCT, "Aluguel: potencial (R$ mi)": INT, "Captação 63d (R$ mi)": MI, "Captação 63d (%)": PCT,
        "Excesso vs peers 63d (p.p.)": PP}
    return tab, fmt


def _leia_me(w: pd.ExcelWriter, resumo: dict) -> None:
    from openpyxl.styles import Alignment, Font
    linhas = [
        ("Prospecção BOV: clientes e prospects cruzados com a CVM", "titulo"),
        ("ARQUIVO COM DADOS INTERNOS. Não copie para pastas sincronizadas (OneDrive) nem compartilhe fora do time.", "negrito"),
        ("", None),
        ("Abas", "negrito"),
        ("Abordagem BOV: todos os candidatos numa lista só, sem repetir, ordenados por AuM. A coluna 'Por que abordar' diz a origem.", None),
        ("BMF sem BOV: clientes com conta BMF e nenhuma conta BOV.", None),
        ("Institucional sem BOV: clientes da base institucional sem conta BOV.", None),
        ("Prospects: quem a mesa marcou como prospect e não tem conta BOV.", None),
        ("Revisar vínculos: nomes que o sistema ligou à CVM com pouca certeza. Corrija e rode de novo (veja abaixo).", None),
        ("Resumo: contagens do cruzamento (quantos nomes, quantos vínculos de cada tipo).", None),
        ("", None),
        ("Como ler", "negrito"),
        ("AuM vem do informe diário da CVM (fundos abertos). Não inclui FIDC, FII nem FIP, nem carteira administrada. "
         "Cliente sem AuM é empresa que não aparece como gestora na CVM (ex.: empresa que só faz hedge) ou ficou sem vínculo.", None),
        ("Um cliente é a junção de tudo que é a mesma pessoa: mesmo código de grupo, mesmo CNPJ, mesmo nome ou a mesma gestora na CVM. "
         "Por isso um cliente que opera BOV por um fundo da mesma gestora NÃO aparece como 'BMF sem BOV'.", None),
        ("'Vínculo com a CVM': alta = CNPJ ou nome praticamente idêntico; média/ambígua = confira antes de usar o AuM; "
         "sem vínculo = não achei gestora com esse nome.", None),
        ("'Atenção' (amarelo): o cliente parece já operar BOV com outra grafia do nome, ou uma conta BOV tem vínculo fraco com "
         "a mesma gestora. Confira ANTES de abordar: é o erro que mais custa.", None),
        ("O segmento B3 (BMF/BOV), o % do PL em cada um e a exposição em ações são a visão PÚBLICA da CVM sobre a gestora, "
         "não o que ela opera conosco. Cliente com muita ação na CVM e sem conta BOV é a melhor oportunidade.", None),
        ("", None),
        ("Corrigindo vínculos", "negrito"),
        ("1) Abra 'de_para_para_preencher.csv' (mesma pasta). 2) Na coluna cnpj_gestor, ponha o CNPJ certo da gestora, ou IGNORAR "
         "se o cliente não tem gestora na CVM. 3) Salve como 'de_para_manual.csv' na pasta principal dos dados internos. "
         "4) Rode o cruzamento de novo: as correções passam a valer para sempre.", None),
    ]
    ws = w.book.create_sheet("Leia-me", 0)
    ws.column_dimensions["A"].width = 135
    ws.sheet_view.showGridLines = False
    for i, (texto, estilo) in enumerate(linhas, start=1):
        c = ws.cell(row=i, column=1, value=texto)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if estilo == "titulo":
            c.font = Font(bold=True, size=16, color="0D366B")
        elif estilo == "negrito":
            c.font = Font(bold=True, color="0D366B")


def _tabela_resumo(resumo: dict) -> pd.DataFrame:
    linhas = [("Linhas lidas por base", ", ".join(f"{k}: {v}" for k, v in resumo["linhas_por_base"].items())),
              ("Nomes distintos", resumo["nomes_distintos"]),
              ("Vínculos por confiança", ", ".join(f"{k}: {v}" for k, v in resumo["vinculos_por_confianca"].items())),
              ("Vínculos por tipo", ", ".join(f"{k}: {v}" for k, v in resumo["vinculos_por_tipo"].items())),
              ("Clientes consolidados", resumo["clientes_consolidados"]),
              ("Unir por código de grupo", "sim" if resumo["unir_por_grupo"] else "não"),
              ("Códigos de grupo nas duas bases / com nomes parecidos",
               f"{resumo['diagnostico_grupos']['codigos_em_comum']} / {resumo['diagnostico_grupos']['com_nomes_parecidos']}"),
              ("AuM usado para ordenar", AUM_ROTULO.get(resumo["aum_coluna"], resumo["aum_coluna"]))]
    for k, n in resumo["listas"].items():
        linhas.append((f"Lista '{k}': clientes (com AuM / com atenção)",
                       f"{n} ({resumo['listas_com_aum'][k]} / {resumo['listas_com_aviso_bov'][k]})"))
    return pd.DataFrame(linhas, columns=["Indicador", "Valor"])


def tabela_revisao(rev: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Nome na sua base": rev.nome, "Base(s)": rev.origens, "Confiança": rev.confianca.map(VINCULO_ROTULO),
        "Nota": rev.score, "Gestora sugerida (CVM)": rev.nome_cvm, "CNPJ sugerido": rev.cnpj_gestor,
        "Outras possibilidades": rev.candidatos, "CNPJ correto (preencher)": ""})


def gravar(res: dict, pasta_saida: Path, aum: str) -> Path:
    """Escreve Prospeccao_BOV.xlsx e de_para_para_preencher.csv em `pasta_saida`."""
    pasta_saida.mkdir(parents=True, exist_ok=True)
    destino, tmp = pasta_saida / "Prospeccao_BOV.xlsx", pasta_saida / "Prospeccao_BOV.tmp.xlsx"
    ls = res["listas"]
    aum_col = f"{AUM_ROTULO.get(aum, aum)} (R$ bi)"
    with pd.ExcelWriter(tmp, engine="openpyxl") as w:
        for aba, chave, motivo in (("Abordagem BOV", "abordagem", True), ("BMF sem BOV", "bmf_sem_bov", False),
                                   ("Institucional sem BOV", "inst_sem_bov", False), ("Prospects", "prospects", False)):
            tab, fmt = tabela_lista(ls[chave], aum, motivo)
            _escrever(w, aba, tab, fmt, barras=(aum_col,), destaque=("Atenção",))
        rev = tabela_revisao(res["revisao"])
        _escrever(w, "Revisar vínculos", rev, {"Nota": "0.0"})
        _tabela_resumo(res["resumo"]).to_excel(w, sheet_name="Resumo", index=False)
        ws = w.sheets["Resumo"]
        ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 52, 110
        _leia_me(w, res["resumo"])
    try:
        tmp.replace(destino)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise PermissionError(f"{destino.name} está aberto no Excel. Feche-o e rode de novo.") from None
    rev = res["revisao"]
    pd.DataFrame({"nome": rev.nome, "cnpj_gestor": "", "sugestao": rev.nome_cvm, "cnpj_sugerido": rev.cnpj_gestor,
                  "confianca": rev.confianca, "nota": rev.score}).drop_duplicates("nome").to_csv(
        pasta_saida / "de_para_para_preencher.csv", sep=";", index=False, encoding="utf-8-sig")
    return destino
