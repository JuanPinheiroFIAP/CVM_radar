"""Testes do cruzamento com as bases internas. Tudo FICTÍCIO: nenhum dado real de cliente.

Rodar: python tests/test_prospeccao.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from cvm_pipeline import prospeccao as P  # noqa: E402

# --- CVM fictícia -----------------------------------------------------------------------
A, B, C, D, E_ = "11111111000111", "22222222000122", "33333333000133", "44444444000144", "55555555000155"
DIM = pd.DataFrame({
    "cnpj_gestor": [A, A, B, C, C, D, E_],
    "nome_gestor": ["ALFA GESTAO DE RECURSOS LTDA"] * 2 + ["BETA ASSET MANAGEMENT S.A."] + ["GAMA CAPITAL INVESTIMENTOS LTDA"] * 2
                   + ["DELTA INVESTIMENTOS LTDA", "EPSILON PARTNERS GESTORA DE RECURSOS LTDA"],
    "nome_classe": ["ALFA HORIZONTE FIF CLASSE DE INVESTIMENTO MULTIMERCADO RESPONSABILIDADE LIMITADA",
                    "ALFA ACOES FUNDO DE INVESTIMENTO", "BETA MACRO FUNDO DE INVESTIMENTO MULTIMERCADO",
                    "GAMA FUNDO DE INVESTIMENTO MULTIMERCADO", "GAMA CREDITO FIF", "DELTA RENDA FIXA FIF",
                    "EPSILON LONG SHORT FIF"],
    "cnpj_classe": ["10000000000101", "10000000000102", "10000000000103", "10000000000104", "10000000000105",
                    "10000000000106", "10000000000107"],
})
DIM["cnpj_fundo"], DIM["ativa"] = DIM.cnpj_classe, True
GEST = pd.DataFrame({
    "cnpj_gestor": [A, B, C, D, E_], "nome_gestor": DIM.drop_duplicates("cnpj_gestor").nome_gestor.tolist(),
    "cnpj_gestor_fmt": ["a", "b", "c", "d", "e"], "aum_total": [5e9, 8e9, 3e9, 2e9, 6e9],
    "aum_ex_fic": [4e9, 7e9, 2.5e9, 1.5e9, 5e9], "aum_nao_exclusivo": [4e9] * 5, "n_classes": [10, 20, 5, 4, 8],
    "peer_group_principal": ["Multimercados Macro"] * 5, "segmento_b3": ["BMF + BOV"] * 5,
    "pct_pl_bmf": [0.5] * 5, "pct_pl_bov": [0.3] * 5, "fluxo_liq_63": [1e8] * 5, "fluxo_pct_63": [0.02] * 5,
    "excesso_pp_63": [1.0] * 5})

CONFIG = {
    "bmf": {"arquivo": "bmf.csv", "grupo": "COD_GRUPO", "cliente": "NOME_CLIENTE", "conta": "CONTA"},
    "bov": {"arquivo": "bov.csv", "grupo": "COD_GRUPO", "cliente": "NOME_CLIENTE", "conta": "CONTA"},
    "institucional": {"arquivo": "inst.csv", "cnpj": "CNPJ", "nome": "NOME"},
    "prospects": {"arquivo": "prospects.csv", "nome": "NOME", "marca": "PROSPECT", "valores_marca": ["S"]},
    "opcoes": {"aum": "aum_ex_fic"}}


def escrever_bases(pasta: Path) -> None:
    (pasta / "entrada").mkdir(parents=True)
    # ';' e acento em Windows-1252: o formato mais comum de exportação interna
    def csv(nome: str, texto: str) -> None:
        (pasta / "entrada" / nome).write_bytes(texto.strip().encode("cp1252"))
    csv("bmf.csv", """COD_GRUPO;NOME_CLIENTE;CONTA
1;ALFA GESTÃO DE RECURSOS LTDA;1001
2;BETA ASSET MANAGEMENT;2001
3;GAMA CAPITAL;3001
4;DELTA INVESTIMENTOS;4001
5;HELIOS AGRO S/A;5001""")
    csv("bov.csv", """COD_GRUPO;NOME_CLIENTE;CONTA
2;BETA ASSET MANAGEMENT S.A.;2002
30;GAMA FUNDO DE INVESTIMENTO MULTIMERCADO;3002
9;ZETA PARTICIPAÇÕES;9001""")
    csv("inst.csv", """CNPJ;NOME
44.444.444/0001-44;DELTA INV LTDA
22222222000122;BETA
99999999000199;OMEGA PREVIDÊNCIA""")
    csv("prospects.csv", """NOME;PROSPECT
EPSILON PARTNERS;S
ALFA GESTAO;N
GAMA CAPITAL;S
KAPPA SECURITIES;S""")


def rodar() -> dict:
    with tempfile.TemporaryDirectory() as t:
        pasta = Path(t)
        escrever_bases(pasta)
        return P.executar(pasta, CONFIG, uni=P.montar_universo(DIM), gestoras=GEST, aluguel=None)


def nomes(df: pd.DataFrame) -> set:
    return {P.normalizar_nome(n).split()[0] for n in df.cliente}


# --- testes ---------------------------------------------------------------------------------
def test_normalizacao():
    assert P.normalizar_nome("  Gestão & Recursos S/A. ") == "GESTAO E RECURSOS SA"
    assert P.normalizar_nome("Verde Asset Management S.A.") == "VERDE ASSET MANAGEMENT SA"
    assert P.nucleo("VERDE ASSET MANAGEMENT SA") == "VERDE"
    assert P.nucleo("GESTORA DE RECURSOS LTDA") == "GESTORA DE RECURSOS LTDA"  # só genéricas: mantém o nome
    assert P.cnpj_digitos("44.444.444/0001-44") == "44444444000144" and P.cnpj_digitos("4444444000144") == "04444444000144"
    assert P.cnpj_digitos("123.456.789-09") == ""                       # CPF válido: não é empresa
    assert P.cnpj_digitos("191") == "00000000000191"                    # CNPJ do BB que perdeu os zeros no Excel
    assert P.cnpj_digitos("00.000.000/0001-91") == "00000000000191" and P.cnpj_digitos("") == ""
    assert P.limpar_codigo("123.0") == "123" and P.limpar_codigo(" 007 ") == "007"


def test_vinculo_por_cnpj_nome_e_fundo():
    r = rodar()["vinculos"].set_index("norm")
    # "DELTA INV LTDA" -> abreviação expandida na normalização; casa pelo CNPJ da base institucional
    assert r.loc["DELTA INVESTIMENTOS LTDA", "cnpj_gestor"] == D
    assert r.loc["DELTA INVESTIMENTOS LTDA", "vinculo"].startswith("cnpj")
    assert r.loc["GAMA CAPITAL", "cnpj_gestor"] == C                                   # núcleo exato
    assert r.loc["GAMA FUNDO DE INVESTIMENTO MULTIMERCADO", "cnpj_gestor"] == C        # nome de fundo -> gestora
    assert r.loc["HELIOS AGRO SA", "confianca"] == "sem vínculo"                       # fora da CVM


def test_listas():
    res = rodar()["listas"]
    # GAMA opera BOV por outro nome (um fundo dela) e BETA opera BOV: saem das listas de abordagem
    assert nomes(res["bmf_sem_bov"]) == {"ALFA", "DELTA", "HELIOS"}
    assert nomes(res["inst_sem_bov"]) == {"DELTA", "OMEGA"}
    assert nomes(res["prospects"]) == {"EPSILON", "KAPPA"}          # ALFA (marca N) e GAMA (já BOV) ficam de fora
    assert nomes(res["abordagem"]) == {"ALFA", "DELTA", "HELIOS", "OMEGA", "EPSILON", "KAPPA"}


def test_situacao_de_todos_os_clientes():
    cli = rodar()["clientes"].set_index("cliente").situacao
    assert cli["BETA ASSET MANAGEMENT"] == "Opera BMF e BOV"           # BMF e BOV, mesmo grupo
    assert cli["GAMA CAPITAL"] == "Opera BMF e BOV"                    # BOV por um fundo dela: mesma gestora
    assert cli["ZETA PARTICIPAÇÕES"] == "Opera só BOV"
    assert cli["ALFA GESTÃO DE RECURSOS LTDA"] == "BMF sem BOV"
    assert cli["OMEGA PREVIDÊNCIA"] == "Institucional sem BOV" and cli["KAPPA SECURITIES"] == "Prospect"


def test_aum_e_ordenacao():
    ab = rodar()["listas"]["abordagem"].set_index("cliente")
    assert ab.loc["ALFA GESTÃO DE RECURSOS LTDA", "aum_ex_fic"] == 4e9
    assert ab.filter(like="EPSILON", axis=0).aum_ex_fic.iloc[0] == 5e9
    assert pd.isna(ab.loc["HELIOS AGRO S/A", "aum_ex_fic"])                           # sem AuM: vai para o fim
    ordem = rodar()["listas"]["abordagem"].aum_ex_fic.tolist()
    assert ordem[:3] == sorted(ordem[:3], reverse=True) and pd.isna(ordem[-1])
    motivos = rodar()["listas"]["abordagem"].set_index("cliente").motivos
    assert motivos.loc["DELTA INVESTIMENTOS"] == "BMF sem BOV + Institucional sem BOV"


def test_de_para_manual_corrige_vinculo():
    with tempfile.TemporaryDirectory() as t:
        pasta = Path(t)
        escrever_bases(pasta)
        (pasta / "de_para_manual.csv").write_text("nome;cnpj_gestor\nHELIOS AGRO S/A;55555555000155\n"
                                                  "Omega Previdência;IGNORAR\n", encoding="utf-8")
        res = P.executar(pasta, CONFIG, uni=P.montar_universo(DIM), gestoras=GEST, aluguel=None)
    v = res["vinculos"].set_index("norm")
    assert v.loc["HELIOS AGRO SA", "cnpj_gestor"] == E_ and v.loc["HELIOS AGRO SA", "vinculo"] == "manual"
    assert v.loc["OMEGA PREVIDENCIA", "confianca"] == "ignorado"


def test_grupo_com_codigos_diferentes_nas_duas_bases_nao_une():
    ent = pd.DataFrame({"origem": ["BMF", "BOV"] * 3, "grupo": ["1", "1", "2", "2", "3", "3"],
                        "nome": ["x"] * 6, "norm": ["XEROX BRASIL", "ZEBRA FOODS", "LOJAS ALFA", "BANCO OMEGA", "MINAS TECH", "SANTOS FUTEBOL"]})
    ent["nucleo"] = ent.norm
    valem, diag = P.grupos_valem_nas_duas_bases(ent)
    assert not valem and diag["codigos_em_comum"] == 3 and diag["com_nomes_parecidos"] == 0


def test_coluna_ausente_da_erro_claro():
    with tempfile.TemporaryDirectory() as t:
        pasta = Path(t)
        escrever_bases(pasta)
        cfg = {**CONFIG, "bmf": {**CONFIG["bmf"], "cliente": "NOME_QUE_NAO_EXISTE"}}
        try:
            P.carregar_entidades(cfg, pasta)
        except ValueError as e:
            assert "NOME_QUE_NAO_EXISTE" in str(e) and "COD_GRUPO" in str(e)   # mostra as colunas que existem
        else:
            raise AssertionError("deveria falhar")


def test_alerta_quando_o_bov_tem_nome_ambiguo_da_mesma_gestora():
    """A gestora está na lista BMF sem BOV, mas a conta BOV 'ALFA' é marca de DUAS gestoras (vínculo ambíguo) e pode ser
    dela: a lista mantém o cliente, porém com alerta para conferir antes de abordar."""
    dim = pd.DataFrame({
        "cnpj_gestor": [A, "66666666000166"], "nome_gestor": ["ALFA GESTAO DE RECURSOS LTDA", "ALFA ASSET MANAGEMENT S.A."],
        "nome_classe": ["ALFA MACRO FIF", "ALFA PART FIF"], "cnpj_classe": ["10000000000101", "10000000000108"]})
    dim["cnpj_fundo"], dim["ativa"] = dim.cnpj_classe, True
    gest = GEST[GEST.cnpj_gestor == A]
    with tempfile.TemporaryDirectory() as t:
        pasta = Path(t)
        (pasta / "entrada").mkdir()
        (pasta / "entrada" / "bmf.csv").write_text(
            "COD_GRUPO;NOME_CLIENTE;CONTA\n1;ALFA GESTAO DE RECURSOS LTDA;1001\n", encoding="utf-8")
        (pasta / "entrada" / "bov.csv").write_text(
            "COD_GRUPO;NOME_CLIENTE;CONTA\n7;ALFA;7001\n8;OUTRA EMPRESA QUALQUER SA;8001\n", encoding="utf-8")
        cfg = {"bmf": CONFIG["bmf"], "bov": CONFIG["bov"], "opcoes": {"aum": "aum_ex_fic"}}
        res = P.executar(pasta, cfg, uni=P.montar_universo(dim), gestoras=gest, aluguel=None)
    lista = res["listas"]["bmf_sem_bov"]
    assert len(lista) == 1 and bool(lista.aviso_bov.iloc[0]) and bool(lista.bov_fraco_mesma_gestora.iloc[0])


def test_erro_de_digitacao_em_palavra_generica_nao_derruba_o_vinculo():
    """'RECRUSOS' (erro de 'RECURSOS') não pode virar uma palavra distintiva do nome."""
    assert P.nucleo(P.normalizar_nome("YARDS ASSET GESTORA DE RECRUSOS")) == P.nucleo(P.normalizar_nome("YARDS ASSET GESTORA DE RECURSOS")) == "YARDS"


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok  ", t.__name__)
    print(f"{len(testes)} testes passaram")
