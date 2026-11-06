"""Cruza as bases internas (clientes BMF/BOV, institucional, prospects) com os dados públicos da CVM.

Objetivo: listar quem o time de BOV pode abordar, ordenado por AuM:
  * clientes que operam BMF e NÃO operam BOV;
  * clientes institucionais que NÃO operam BOV;
  * prospects.

As bases internas não têm chave em comum com a CVM (a de BMF/BOV não tem CNPJ; a de prospects só tem nome),
então o vínculo é feito por CNPJ quando existe (base institucional) e por NOME nos demais casos, sempre com
nível de confiança e uma aba de revisão para o que ficar duvidoso. O que o analista corrigir à mão
(`de_para_manual.csv`) vale nas próximas execuções.

Tudo local: nada é enviado para fora, e nenhum nome de cliente vai para o log (só contagens).
"""
import csv
import logging
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from .config import MARTS, PARQUET

log = logging.getLogger("cvm")

# ---------------------------------------------------------------------------------------
# Nomes
# ---------------------------------------------------------------------------------------
ABREVIACOES = {"ADM": "ADMINISTRACAO", "ADMIN": "ADMINISTRACAO", "GEST": "GESTAO", "INV": "INVESTIMENTOS",
               "INVEST": "INVESTIMENTOS", "PART": "PARTICIPACOES", "PARTIC": "PARTICIPACOES", "FDO": "FUNDO"}
# Palavras que não identificam a empresa (forma jurídica, "gestão de recursos", "fundo de investimento"...).
# O que sobra é o "núcleo" do nome, que é o que de fato compara.
GENERICAS = {
    "LTDA", "SA", "ME", "EPP", "EIRELI", "CIA", "COMPANHIA", "SS", "SOCIEDADE", "ANONIMA", "LIMITADA",
    "DE", "DA", "DO", "DAS", "DOS", "E", "EM", "A", "O", "PARA", "COM", "NO", "NA",
    "GESTORA", "GESTAO", "RECURSOS", "ADMINISTRACAO", "ADMINISTRADORA", "CARTEIRAS", "CARTEIRA", "VALORES",
    "MOBILIARIOS", "DTVM", "CCTVM", "CTVM", "INVESTIMENTO", "INVESTIMENTOS", "ASSET", "MANAGEMENT", "AM",
    "FUNDO", "FUNDOS", "FIF", "FIM", "FIA", "FIC", "FIDC", "FIP", "FII", "FI", "CLASSE", "CI", "CIC",
    "RESPONSABILIDADE", "COTAS", "COTA",
}
TOKENS_FUNDO = {"FUNDO", "FUNDOS", "FIF", "FIM", "FIA", "FIC", "FIDC", "FIP", "FII", "FI", "CLASSE", "FICFIDC"}


def normalizar_nome(s) -> str:
    """MAIÚSCULAS, sem acento nem pontuação, abreviações comuns expandidas ('S/A' -> 'SA')."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s.replace("&", " E ")).strip()
    s = re.sub(r"\bS A\b", "SA", s)
    return " ".join(ABREVIACOES.get(t, t) for t in s.split())


_GENERICAS_LONGAS = [g for g in GENERICAS if len(g) >= 6]


@lru_cache(maxsize=None)
def _generica(t: str) -> bool:
    """Palavra genérica, ou quase igual a uma (erro de digitação: 'RECRUSOS' ~ 'RECURSOS')."""
    return t in GENERICAS or (len(t) >= 6 and any(abs(len(t) - len(g)) <= 1 and fuzz.ratio(t, g) >= 85
                                                 for g in _GENERICAS_LONGAS))


def nucleo(norm: str) -> str:
    """Nome sem palavras genéricas ('VERDE ASSET MANAGEMENT SA' -> 'VERDE'). Se sobrar nada, o próprio nome."""
    tokens = [t for t in norm.split() if not _generica(t)]
    return " ".join(tokens) if tokens else norm


def parece_fundo(norm: str) -> bool:
    return any(t in TOKENS_FUNDO for t in norm.split())


def _cpf_valido(c: str) -> bool:
    if len(c) != 11 or c == c[0] * 11:
        return False
    for n in (9, 10):
        if (sum(int(c[i]) * (n + 1 - i) for i in range(n)) * 10) % 11 % 10 != int(c[n]):
            return False
    return True


def _cnpj_valido(c: str) -> bool:
    if len(c) != 14 or c == c[0] * 14:
        return False
    pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for p, pos in ((pesos, 12), ([6] + pesos, 13)):
        r = sum(int(c[i]) * w for i, w in enumerate(p)) % 11
        if (0 if r < 2 else 11 - r) != int(c[pos]):
            return False
    return True


def cnpj_digitos(x) -> str:
    """CNPJ só com dígitos e 14 posições (o Excel come os zeros à esquerda). CPF e lixo viram ''.
    Número curto só é descartado se for um CPF válido e não um CNPJ válido (o CNPJ do BB tem 6 zeros na frente)."""
    d = re.sub(r"\D", "", str(x)) if x is not None else ""
    if len(d) < 3 or len(d) > 14:
        return ""
    if len(d) <= 11 and _cpf_valido(d.zfill(11)) and not _cnpj_valido(d.zfill(14)):
        return ""
    return d.zfill(14)


def limpar_codigo(x) -> str:
    """Código/conta como texto limpo ('123.0' do Excel vira '123')."""
    s = str(x).strip() if x is not None and not (isinstance(x, float) and np.isnan(x)) else ""
    return re.sub(r"\.0+$", "", s.lstrip("'"))


# ---------------------------------------------------------------------------------------
# Leitura das bases internas
# ---------------------------------------------------------------------------------------
def ler_tabela(caminho: Path, aba: str | None = None) -> pd.DataFrame:
    """Lê .xlsx/.xls/.csv como texto. No CSV detecta separador e codificação (UTF-8 ou Windows-1252)."""
    if caminho.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(caminho, sheet_name=aba or 0, dtype=str, keep_default_na=False)
    else:
        bruto = caminho.read_bytes()
        for enc in ("utf-8-sig", "cp1252"):
            try:
                texto = bruto.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            sep = csv.Sniffer().sniff(texto[:5000], delimiters=";,\t|").delimiter
        except csv.Error:
            sep = ";"
        df = pd.read_csv(caminho, sep=sep, encoding=enc, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _coluna(df: pd.DataFrame, nome: str, fonte: str, campo: str) -> pd.Series:
    achadas = {c.strip().upper(): c for c in df.columns}
    if nome.strip().upper() not in achadas:
        raise ValueError(f"[{fonte}] não achei a coluna '{nome}' ({campo}). Colunas do arquivo: {list(df.columns)}")
    return df[achadas[nome.strip().upper()]]


def _abrir(cfg: dict, secao: str, pasta: Path) -> pd.DataFrame:
    s = cfg[secao]
    caminho = pasta / "entrada" / s["arquivo"]
    if not caminho.exists():
        raise FileNotFoundError(f"[{secao}] arquivo não encontrado: {caminho}")
    return ler_tabela(caminho, s.get("aba"))


def carregar_entidades(cfg: dict, pasta: Path) -> pd.DataFrame:
    """Une as quatro bases numa tabela só (uma linha por conta/entrada): origem, grupo, nome, conta, cnpj."""
    partes = []

    def clientes(df: pd.DataFrame, secao: str, origem: pd.Series | str) -> pd.DataFrame:
        s = cfg[secao]
        return pd.DataFrame({
            "origem": origem,
            "grupo": _coluna(df, s["grupo"], secao, "grupo").map(limpar_codigo) if s.get("grupo") else "",
            "nome": _coluna(df, s["cliente"], secao, "cliente").astype(str).str.strip(),
            "conta": _coluna(df, s["conta"], secao, "conta").map(limpar_codigo) if s.get("conta") else "",
            "cnpj": ""})

    if "clientes" in cfg:  # um arquivo só, com coluna de segmento
        s = cfg["clientes"]
        df = _abrir(cfg, "clientes", pasta)
        seg = _coluna(df, s["segmento"], "clientes", "segmento").astype(str).str.strip().str.upper()
        bmf = {v.upper() for v in s.get("valores_bmf", ["BMF"])}
        bov = {v.upper() for v in s.get("valores_bov", ["BOV", "BOVESPA"])}
        origem = seg.map(lambda v: "BMF" if v in bmf else "BOV" if v in bov else "")
        c = clientes(df, "clientes", origem)
        descartadas = int((c.origem == "").sum())
        if descartadas:
            log.warning("clientes: %d linhas com segmento fora de %s/%s foram ignoradas", descartadas, bmf, bov)
        partes.append(c[c.origem != ""])
    else:
        for secao, origem in (("bmf", "BMF"), ("bov", "BOV")):
            if secao in cfg:
                partes.append(clientes(_abrir(cfg, secao, pasta), secao, origem))

    if "institucional" in cfg:
        s, df = cfg["institucional"], _abrir(cfg, "institucional", pasta)
        partes.append(pd.DataFrame({
            "origem": "INST", "grupo": "", "conta": "",
            "nome": _coluna(df, s["nome"], "institucional", "nome").astype(str).str.strip(),
            "cnpj": _coluna(df, s["cnpj"], "institucional", "cnpj").map(cnpj_digitos)}))

    if "prospects" in cfg:
        s, df = cfg["prospects"], _abrir(cfg, "prospects", pasta)
        p = pd.DataFrame({"origem": "PROSPECT", "grupo": "", "conta": "", "cnpj": "",
                          "nome": _coluna(df, s["nome"], "prospects", "nome").astype(str).str.strip()})
        if s.get("marca"):
            valores = {v.strip().upper() for v in s.get("valores_marca", ["S", "SIM", "PROSPECT"])}
            p = p[_coluna(df, s["marca"], "prospects", "marca").astype(str).str.strip().str.upper().isin(valores)]
        partes.append(p)

    ent = pd.concat(partes, ignore_index=True)
    ent = ent[ent.nome.str.len() > 0].copy()
    ent["norm"] = ent.nome.map(normalizar_nome)
    ent = ent[ent.norm != ""].copy()
    ent["nucleo"] = ent.norm.map(nucleo)
    return ent.reset_index(drop=True)


# ---------------------------------------------------------------------------------------
# Universo CVM
# ---------------------------------------------------------------------------------------
@dataclass
class Universo:
    gestoras: pd.DataFrame        # cnpj_gestor, nome, norm, nucleo
    fundos: pd.DataFrame          # cnpj_gestor, nome, norm, nucleo (classes ativas)
    por_cnpj: dict                # cnpj (gestora, classe ou fundo) -> (cnpj_gestor, tipo)
    por_norm: dict                # nome normalizado -> {cnpj_gestor}
    por_nucleo: dict              # núcleo do nome da gestora -> {cnpj_gestor}
    nome_gestora: dict            # cnpj_gestor -> nome
    por_palavra: dict             # palavra do núcleo -> {cnpj_gestor} (para achar gestoras com nome parecido)


def montar_universo(dim: pd.DataFrame) -> Universo:
    """Monta o que dá para casar: gestoras e classes de fundos da CVM, por CNPJ e por nome."""
    d = dim[dim.cnpj_gestor.notna()].copy()
    g = d.drop_duplicates("cnpj_gestor")[["cnpj_gestor", "nome_gestor"]].rename(columns={"nome_gestor": "nome"})
    g = g[g.nome.notna()].assign(norm=lambda t: t.nome.map(normalizar_nome))
    g["nucleo"] = g.norm.map(nucleo)
    f = d[d.ativa][["cnpj_gestor", "nome_classe"]].rename(columns={"nome_classe": "nome"})
    f = f[f.nome.notna()].drop_duplicates("nome").assign(norm=lambda t: t.nome.map(normalizar_nome))
    f["nucleo"] = f.norm.map(nucleo)

    por_cnpj: dict = {}
    for col, tipo in (("cnpj_fundo", "fundo"), ("cnpj_classe", "classe")):
        if col in d:
            por_cnpj.update({c: (gg, tipo) for c, gg in zip(d[col], d.cnpj_gestor) if isinstance(c, str) and c})
    por_cnpj.update({c: (c, "gestora") for c in g.cnpj_gestor})  # CNPJ da própria gestora vale mais

    por_norm: dict = {}
    for df in (g, f):
        for n, c in zip(df.norm, df.cnpj_gestor):
            por_norm.setdefault(n, set()).add(c)
    por_nucleo: dict = {}
    por_palavra: dict = {}
    for n, c in zip(g.nucleo, g.cnpj_gestor):
        por_nucleo.setdefault(n, set()).add(c)
        for t in n.split():
            por_palavra.setdefault(t, set()).add(c)
    return Universo(g.reset_index(drop=True), f.reset_index(drop=True), por_cnpj, por_norm, por_nucleo,
                    dict(zip(g.cnpj_gestor, g.nome)), por_palavra)


# ---------------------------------------------------------------------------------------
# Casamento por nome
# ---------------------------------------------------------------------------------------
@dataclass
class Limites:
    alta: float = 95.0      # nota mínima para vínculo automático confiável
    media: float = 88.0     # nota mínima para vínculo usado, mas marcado para conferir
    baixa: float = 80.0     # abaixo disso nem sugere
    margem: float = 4.0     # diferença mínima para o segundo colocado (outra gestora); senão é ambíguo
    peso_ordem: float = 0.7 # peso da nota "ordem das palavras ignorada" (o resto é a nota de "subconjunto")


def _nota(q: str, c: str, lim: Limites) -> float:
    return lim.peso_ordem * fuzz.token_sort_ratio(q, c) + (1 - lim.peso_ordem) * fuzz.token_set_ratio(q, c)


def _candidatos(q: str, df: pd.DataFrame, nucleos: list[str], lim: Limites, tipo: str) -> dict:
    """Melhores gestoras para o núcleo `q` dentro de um conjunto (gestoras ou classes): {cnpj_gestor: (nota, nome, tipo)}."""
    indices = set()
    for scorer in (fuzz.token_sort_ratio, fuzz.token_set_ratio):
        for _, _, i in process.extract(q, nucleos, scorer=scorer, limit=10, score_cutoff=lim.baixa - 5):
            indices.add(i)
    achados: dict = {}
    for i in indices:
        nota = _nota(q, nucleos[i], lim)
        g = df.cnpj_gestor.iat[i]
        if nota >= lim.baixa and (g not in achados or nota > achados[g][0]):
            achados[g] = (nota, df.nome.iat[i], tipo)
    return achados


def _fracas(norm: str, nuc: str, uni: Universo, lim: Limites, nucleos_g: list[str], nucleos_f: list[str]) -> list[str]:
    """Todas as gestoras plausíveis para um nome que NÃO foi vinculado com segurança: notas a partir de 70 e, para nome
    de 1 ou 2 palavras ('BTG', 'FIRST'), toda gestora que contém essas palavras. Alimenta o alerta 'já opera BOV'."""
    piso = Limites(alta=lim.alta, media=lim.media, baixa=70.0, margem=lim.margem, peso_ordem=lim.peso_ordem)
    achados = _candidatos(nuc, uni.gestoras, nucleos_g, piso, "gestora")
    if parece_fundo(norm):
        achados |= {g: v for g, v in _candidatos(nuc, uni.fundos, nucleos_f, piso, "fundo").items() if g not in achados}
    gs = {g for g, _ in sorted(achados.items(), key=lambda kv: -kv[1][0])[:15]}
    palavras = nuc.split()
    if 1 <= len(palavras) <= 2:
        comuns = set.intersection(*(uni.por_palavra.get(t, set()) for t in palavras))
        if 0 < len(comuns) <= 8:
            gs |= comuns
    return sorted(gs)


def _casar_um(norm: str, nuc: str, cnpj: str, uni: Universo, lim: Limites, manual: dict, nucleos_g: list[str],
              nucleos_f: list[str]) -> dict:
    r = _casar_um_base(norm, nuc, cnpj, uni, lim, manual, nucleos_g, nucleos_f)
    r["cand_gestoras"] = [] if r["confianca"] in ("alta", "média", "ignorado") or not nuc else         _fracas(norm, nuc, uni, lim, nucleos_g, nucleos_f)
    return r


def _casar_um_base(norm: str, nuc: str, cnpj: str, uni: Universo, lim: Limites, manual: dict, nucleos_g: list[str],
                   nucleos_f: list[str]) -> dict:
    r = dict(norm=norm, cnpj=cnpj, cnpj_gestor="", nome_cvm="", vinculo="", score=0.0, confianca="sem vínculo",
             candidatos="")
    if norm in manual:
        alvo = manual[norm]
        if alvo == "IGNORAR":
            return {**r, "confianca": "ignorado"}
        if alvo in uni.nome_gestora:
            return {**r, "cnpj_gestor": alvo, "nome_cvm": uni.nome_gestora[alvo], "vinculo": "manual", "score": 100.0,
                    "confianca": "alta"}
    if cnpj and cnpj in uni.por_cnpj:
        g, tipo = uni.por_cnpj[cnpj]
        return {**r, "cnpj_gestor": g, "nome_cvm": uni.nome_gestora.get(g, ""), "vinculo": f"cnpj ({tipo})",
                "score": 100.0, "confianca": "alta"}

    def unico(gs: set, vinculo: str) -> dict | None:
        if len(gs) == 1:
            g = next(iter(gs))
            return {**r, "cnpj_gestor": g, "nome_cvm": uni.nome_gestora.get(g, ""), "vinculo": vinculo,
                    "score": 100.0, "confianca": "alta"}
        return None
    if norm in uni.por_norm and (hit := unico(uni.por_norm[norm], "nome exato")):
        return hit
    if len(nuc) >= 3 and nuc in uni.por_nucleo and (hit := unico(uni.por_nucleo[nuc], "núcleo exato")):
        # Nome curto que bate com UMA gestora, mas está contido no nome de outras ("SAFRA ASSET", "WEALTH"): em
        # geral são empresas do mesmo grupo. Vale, mas fica marcado para conferir e lista as concorrentes.
        palavras = nuc.split()
        parentes = set.intersection(*(uni.por_palavra.get(t, set()) for t in palavras)) - {hit["cnpj_gestor"]}
        if parentes and len(palavras) <= 2:
            hit["confianca"] = "média"
            hit["candidatos"] = " | ".join(f"{uni.nome_gestora[g]} [{g}]" for g in sorted(parentes)[:3])
        return hit

    achados = _candidatos(nuc, uni.gestoras, nucleos_g, lim, "gestora")
    if parece_fundo(norm) or not achados or max(v[0] for v in achados.values()) < lim.media:
        for g, v in _candidatos(nuc, uni.fundos, nucleos_f, lim, "fundo").items():
            if g not in achados or v[0] > achados[g][0]:
                achados[g] = v
    if not achados:
        return r
    ordem = sorted(achados.items(), key=lambda kv: -kv[1][0])
    (g1, (s1, n1, t1)) = ordem[0]
    s2 = ordem[1][1][0] if len(ordem) > 1 else 0.0
    r["candidatos"] = " | ".join(f"{v[1]} [{g}] {v[0]:.0f}" for g, v in ordem[:3])
    r.update(cnpj_gestor=g1, nome_cvm=uni.nome_gestora.get(g1, n1), vinculo=f"nome aproximado ({t1})", score=round(s1, 1))
    if s1 - s2 < lim.margem and s2 >= lim.baixa:
        r["confianca"] = "ambígua"
    elif s1 >= lim.alta:
        r["confianca"] = "alta"
    elif s1 >= lim.media:
        r["confianca"] = "média"
    else:
        r["confianca"] = "baixa"
    return r


def casar(chaves: pd.DataFrame, uni: Universo, lim: Limites, manual: dict) -> pd.DataFrame:
    """Vincula cada (nome, cnpj) distinto de `chaves` (colunas norm, nucleo, cnpj) a uma gestora da CVM."""
    nucleos_g, nucleos_f = uni.gestoras.nucleo.tolist(), uni.fundos.nucleo.tolist()
    linhas = [_casar_um(n, k, c, uni, lim, manual, nucleos_g, nucleos_f)
              for n, k, c in chaves[["norm", "nucleo", "cnpj"]].itertuples(index=False)]
    return pd.DataFrame(linhas)


# ---------------------------------------------------------------------------------------
# Consolidação: quem é o mesmo cliente nas várias bases
# ---------------------------------------------------------------------------------------
class _Uniao:
    def __init__(self, n: int):
        self.pai = list(range(n))

    def achar(self, i: int) -> int:
        while self.pai[i] != i:
            self.pai[i] = self.pai[self.pai[i]]
            i = self.pai[i]
        return i

    def unir(self, a: int, b: int) -> None:
        ra, rb = self.achar(a), self.achar(b)
        if ra != rb:
            self.pai[rb] = ra


def grupos_valem_nas_duas_bases(ent: pd.DataFrame, minimo: float = 0.7) -> tuple[bool, dict]:
    """Os códigos de grupo da base BMF e da BOV são os mesmos? Para códigos presentes nas duas bases, compara os
    nomes: se a maioria for parecida, o código vale; se não, unir por grupo juntaria clientes sem relação."""
    g = ent[(ent.grupo != "") & ent.origem.isin(["BMF", "BOV"])]
    bmf, bov = g[g.origem == "BMF"], g[g.origem == "BOV"]
    comuns = sorted(set(bmf.grupo) & set(bov.grupo))
    if not comuns:
        return True, {"codigos_em_comum": 0, "com_nomes_parecidos": None}
    nomes_bmf = bmf.groupby("grupo").nucleo.agg(list)
    nomes_bov = bov.groupby("grupo").nucleo.agg(list)
    parecidos = sum(max(fuzz.token_set_ratio(a, b) for a in nomes_bmf[c] for b in nomes_bov[c]) >= 85 for c in comuns)
    taxa = parecidos / len(comuns)
    return taxa >= minimo, {"codigos_em_comum": len(comuns), "com_nomes_parecidos": round(taxa, 3)}


def consolidar(ent: pd.DataFrame, mt: pd.DataFrame, aceitar: set, unir_grupo: bool) -> pd.DataFrame:
    """Anexa o vínculo CVM a cada entrada e agrupa as que são o mesmo cliente (mesmo grupo, mesma gestora, mesmo
    CNPJ ou mesmo nome). Devolve `ent` com `cliente_id`, `cnpj_gestor_ok` e `confianca`."""
    e = ent.merge(mt, on=["norm", "cnpj"], how="left")
    e["cnpj_gestor_ok"] = e.cnpj_gestor.where(e.confianca.isin(aceitar), "")
    uniao = _Uniao(len(e))
    vistos: dict = {}
    for i, (grupo, g, cnpj, norm, nuc) in enumerate(zip(e.grupo, e.cnpj_gestor_ok, e.cnpj, e.norm, e.nucleo)):
        chaves = [f"N:{norm}"]
        if unir_grupo and grupo:
            chaves.append(f"G:{grupo}")
        if g:
            chaves.append(f"C:{g}")
        if cnpj:
            chaves.append(f"J:{cnpj}")
        if len(nuc) >= 6:
            chaves.append(f"K:{nuc}")
        for k in chaves:
            if k in vistos:
                uniao.unir(vistos[k], i)
            else:
                vistos[k] = i
    e["cliente_id"] = [uniao.achar(i) for i in range(len(e))]
    return e


# ---------------------------------------------------------------------------------------
# Clientes consolidados, enriquecimento com a CVM e listas
# ---------------------------------------------------------------------------------------
ORDEM_CONFIANCA = {"alta": 0, "média": 1, "ambígua": 2, "baixa": 3, "sem vínculo": 4, "ignorado": 5}


def _juntar(valores, n: int = 5, sep: str = ", ") -> str:
    vistos = list(dict.fromkeys(v for v in valores if v))
    return sep.join(vistos[:n]) + (f" (+{len(vistos) - n})" if len(vistos) > n else "")


def montar_clientes(e: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por cliente consolidado, com o que ele opera conosco."""
    linhas = []
    for cid, g in e.groupby("cliente_id", sort=False):
        bmf, bov = g[g.origem == "BMF"], g[g.origem == "BOV"]
        ordem_nome = g.assign(p=g.origem.map({"BMF": 0, "BOV": 0, "INST": 1, "PROSPECT": 2})).sort_values("p")
        gest = list(dict.fromkeys(x for x in g.cnpj_gestor_ok if x))
        pior = min(g.confianca.fillna("sem vínculo"), key=lambda c: -ORDEM_CONFIANCA.get(c, 9)) if not gest else \
            max((c for c in g.loc[g.cnpj_gestor_ok != "", "confianca"]), key=lambda c: ORDEM_CONFIANCA.get(c, 9))
        linhas.append({
            "cliente_id": cid, "cliente": ordem_nome.nome.iloc[0], "outros_nomes": _juntar(ordem_nome.nome.iloc[1:], 3, " | "),
            "grupos": _juntar(g.grupo), "opera_bmf": len(bmf) > 0, "opera_bov": len(bov) > 0,
            "institucional": bool((g.origem == "INST").any()), "prospect": bool((g.origem == "PROSPECT").any()),
            "n_contas_bmf": int(bmf.conta.replace("", np.nan).nunique() or len(bmf)),
            "n_contas_bov": int(bov.conta.replace("", np.nan).nunique() or len(bov)),
            "contas_bmf": _juntar(bmf.conta), "contas_bov": _juntar(bov.conta), "cnpj_inst": _juntar(g.cnpj, 2),
            "gestoras": gest, "confianca": pior})
    return pd.DataFrame(linhas)


def enriquecer(cli: pd.DataFrame, gestoras: pd.DataFrame, aluguel: pd.DataFrame | None) -> pd.DataFrame:
    """Traz da CVM o AuM e o perfil das gestoras vinculadas (soma quando um cliente tem mais de uma)."""
    base = gestoras.copy()
    if aluguel is not None:
        base = base.merge(aluguel[["cnpj_gestor", "exposicao_acoes", "doador_vl", "util_doador", "util_peers",
                                   "potencial_doador_rs"]], on="cnpj_gestor", how="left")
    pares = cli[["cliente_id", "gestoras"]].explode("gestoras").dropna(subset=["gestoras"])
    pares = pares.merge(base, left_on="gestoras", right_on="cnpj_gestor", how="left")
    aditivas = [c for c in ("aum_total", "aum_ex_fic", "aum_nao_exclusivo", "n_classes", "exposicao_acoes", "doador_vl",
                            "potencial_doador_rs", "fluxo_liq_63") if c in pares]
    soma = pares.groupby("cliente_id")[aditivas].sum(min_count=1)
    principal = pares.sort_values("aum_total", ascending=False).drop_duplicates("cliente_id").set_index("cliente_id")
    descr = [c for c in ("nome_gestor", "cnpj_gestor_fmt", "peer_group_principal", "segmento_b3", "pct_pl_bmf",
                         "pct_pl_bov", "fluxo_pct_63", "excesso_pp_63", "util_doador", "util_peers") if c in principal]
    cvm = soma.join(principal[descr])
    cvm["n_gestoras_cvm"] = pares.groupby("cliente_id").gestoras.nunique()
    out = cli.merge(cvm, left_on="cliente_id", right_index=True, how="left")
    for col in ("aum_total", "aum_ex_fic", "aum_nao_exclusivo"):   # garante as colunas mesmo sem nenhum vínculo
        if col not in out:
            out[col] = np.nan
    out["tem_cvm"] = out.aum_total.notna()
    return out


def marcar_atencao(cli: pd.DataFrame, e: pd.DataFrame, corte: float = 88.0) -> pd.DataFrame:
    """Alerta contra o erro mais caro: listar para abordagem alguém que JÁ opera BOV com outra grafia do nome.
    Marca o candidato quando (a) tem nome parecido com o de uma conta BOV de OUTRO cliente consolidado ou (b) uma
    conta BOV tem vínculo fraco (ambíguo/baixo) com a mesma gestora da CVM."""
    bov = e[e.origem == "BOV"][["cliente_id", "nucleo", "nome"]].drop_duplicates(["cliente_id", "nucleo"]).reset_index(drop=True)
    bov_nuc = bov.nucleo.tolist()
    fracos = set(e[(e.origem == "BOV") & ~e.confianca.isin(["alta", "média", "ignorado"])].cand_gestoras.explode().dropna())
    candidatos = set(cli[~cli.opera_bov & (cli.opera_bmf | cli.institucional | cli.prospect)].cliente_id)
    parecido: dict = {}
    for cid, nuc in zip(e.cliente_id, e.nucleo):
        if cid not in candidatos or cid in parecido or len(nuc) < 4 or not bov_nuc:
            continue
        for _, _, i in process.extract(nuc, bov_nuc, scorer=fuzz.token_sort_ratio, limit=3, score_cutoff=corte):
            if bov.cliente_id.iat[i] != cid:
                parecido[cid] = bov.nome.iat[i]
                break
    out = cli.copy()
    out["parecido_bov"] = out.cliente_id.map(parecido).fillna("")
    out["bov_fraco_mesma_gestora"] = out.gestoras.map(lambda gs: any(g in fracos for g in gs))
    out["aviso_bov"] = out.parecido_bov.ne("") | out.bov_fraco_mesma_gestora
    return out


def listas(cli: pd.DataFrame, aum: str) -> dict[str, pd.DataFrame]:
    def ordenar(df: pd.DataFrame) -> pd.DataFrame:
        return df.sort_values([aum, "cliente"], ascending=[False, True], na_position="last").reset_index(drop=True)
    bmf_sem = cli[cli.opera_bmf & ~cli.opera_bov]
    inst_sem = cli[cli.institucional & ~cli.opera_bov]
    pros = cli[cli.prospect & ~cli.opera_bov]
    motivos = pd.DataFrame({"BMF sem BOV": cli.opera_bmf & ~cli.opera_bov,
                            "Institucional sem BOV": cli.institucional & ~cli.opera_bov,
                            "Prospect": cli.prospect & ~cli.opera_bov})
    unica = cli[motivos.any(axis=1)].copy()
    unica["motivos"] = motivos[motivos.any(axis=1)].apply(lambda r: " + ".join(k for k, v in r.items() if v), axis=1)
    return {"abordagem": ordenar(unica), "bmf_sem_bov": ordenar(bmf_sem), "inst_sem_bov": ordenar(inst_sem),
            "prospects": ordenar(pros)}


def revisao(mt: pd.DataFrame, ent: pd.DataFrame) -> pd.DataFrame:
    """Vínculos para o analista conferir: com candidato, mas sem confiança suficiente (ou só 'média')."""
    r = mt[mt.confianca.isin(["média", "ambígua", "baixa"])].copy()
    origem = ent.groupby("norm").agg(origens=("origem", lambda x: ", ".join(sorted(set(x)))), nome=("nome", "first"))
    r = r.merge(origem, left_on="norm", right_index=True, how="left")
    r["ordem"] = r.confianca.map(ORDEM_CONFIANCA)
    return r.sort_values(["ordem", "score"], ascending=[True, False]).drop(columns="ordem").reset_index(drop=True)


# ---------------------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------------------
def carregar_config(caminho: Path) -> dict:
    with open(caminho, "rb") as f:
        return tomllib.load(f)


def checar(cfg: dict, pasta: Path) -> list[str]:
    """Confere arquivos e colunas das bases internas SEM mostrar nenhum valor: só nomes de coluna e contagens.
    Serve para validar o config.toml com as bases reais antes de rodar."""
    linhas = []
    for secao in ("bmf", "bov", "clientes", "institucional", "prospects"):
        if secao not in cfg:
            continue
        s = cfg[secao]
        caminho = pasta / "entrada" / s["arquivo"]
        if not caminho.exists():
            linhas.append(f"[{secao}] ARQUIVO NÃO ENCONTRADO: {caminho}")
            continue
        try:
            df = ler_tabela(caminho, s.get("aba"))
        except Exception as erro:  # noqa: BLE001 - só o tipo do erro, nunca o conteúdo
            linhas.append(f"[{secao}] erro ao ler {caminho.name}: {type(erro).__name__}")
            continue
        linhas.append(f"[{secao}] {caminho.name}: {len(df)} linhas; colunas do arquivo: {list(df.columns)}")
        for campo in ("grupo", "cliente", "conta", "segmento", "cnpj", "nome", "marca"):
            if not s.get(campo):
                continue
            try:
                col = _coluna(df, s[campo], secao, campo).astype(str).str.strip()
            except ValueError:
                linhas.append(f"     ✗ {campo} = '{s[campo]}': essa coluna NÃO existe no arquivo")
                continue
            extra = f", {int(col.map(cnpj_digitos).ne('').sum())} com CNPJ válido" if campo == "cnpj" else ""
            linhas.append(f"     ✓ {campo} = '{s[campo]}': {int((col != '').sum())} preenchidas, {col.nunique()} distintas{extra}")
    return linhas


def ler_manual(caminho: Path) -> dict:
    """de_para_manual.csv: colunas `nome` (como está na sua base) e `cnpj_gestor` (14 dígitos, ou IGNORAR)."""
    if not caminho.exists():
        return {}
    m = ler_tabela(caminho)
    if not {"nome", "cnpj_gestor"} <= set(m.columns):
        raise ValueError(f"{caminho.name} precisa ter as colunas 'nome' e 'cnpj_gestor'. Tem: {list(m.columns)}")
    out = {}
    for nome, alvo in zip(m.nome, m.cnpj_gestor):
        alvo = alvo.strip()
        if alvo:
            out[normalizar_nome(nome)] = "IGNORAR" if alvo.upper() == "IGNORAR" else cnpj_digitos(alvo)
    return out


def carregar_cvm() -> tuple[Universo, pd.DataFrame, pd.DataFrame | None]:
    if not (PARQUET / "dim_classe.parquet").exists() or not (MARTS / "gestora_snapshot.parquet").exists():
        raise FileNotFoundError("Faltam os dados da CVM. Rode antes: atualizar_dados.bat")
    uni = montar_universo(pd.read_parquet(PARQUET / "dim_classe.parquet"))
    gest = pd.read_parquet(MARTS / "gestora_snapshot.parquet")
    alu = MARTS / "oportunidade_aluguel.parquet"
    return uni, gest, pd.read_parquet(alu) if alu.exists() else None


def executar(pasta: Path, config: dict, uni: Universo | None = None, gestoras: pd.DataFrame | None = None,
             aluguel: pd.DataFrame | None = None, lim: Limites | None = None) -> dict:
    """Roda o cruzamento inteiro e devolve as tabelas (e um resumo só com contagens)."""
    op = config.get("opcoes", {})
    aum = op.get("aum", "aum_ex_fic")
    aceitar = set(op.get("aceitar", ["alta", "média"]))
    lim = lim or Limites(**{k: v for k, v in op.get("limites", {}).items()})
    if uni is None:
        uni, gestoras, aluguel = carregar_cvm()
    ent = carregar_entidades(config, pasta)
    manual = ler_manual(pasta / "de_para_manual.csv")

    chaves = ent[["norm", "nucleo", "cnpj"]].drop_duplicates().reset_index(drop=True)
    mt = casar(chaves, uni, lim, manual)

    unir = op.get("unir_por_grupo", "auto")
    valem, diag_grupo = grupos_valem_nas_duas_bases(ent)
    unir_grupo = valem if unir == "auto" else bool(unir)

    e = consolidar(ent, mt, aceitar, unir_grupo)
    cli = marcar_atencao(enriquecer(montar_clientes(e), gestoras, aluguel), e)
    saidas = listas(cli, aum)

    por_origem = ent.groupby("origem").size().to_dict()
    resumo = {
        "linhas_por_base": por_origem,
        "nomes_distintos": int(len(chaves)),
        "vinculos_por_confianca": mt.confianca.value_counts().to_dict(),
        "vinculos_por_tipo": mt[mt.vinculo != ""].vinculo.str.replace(r" \(.*\)", "", regex=True).value_counts().to_dict(),
        "clientes_consolidados": int(len(cli)),
        "unir_por_grupo": unir_grupo, "diagnostico_grupos": diag_grupo,
        "listas": {k: int(len(v)) for k, v in saidas.items()},
        "listas_com_aum": {k: int(v.tem_cvm.sum()) for k, v in saidas.items()},
        "listas_com_aviso_bov": {k: int(v.aviso_bov.sum()) for k, v in saidas.items()},
        "aum_coluna": aum}
    log.info("cruzamento: %s", resumo)
    return {"clientes": cli, "vinculos": mt, "entidades": e, "listas": saidas, "revisao": revisao(mt, ent),
            "resumo": resumo}
