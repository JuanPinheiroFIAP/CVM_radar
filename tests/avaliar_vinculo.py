"""Mede a qualidade do casamento por nome com bases fictícias de resposta conhecida.

Uso: python tests/avaliar_vinculo.py [pasta-temporaria]
Para cada tipo de deformação do nome (sem 'LTDA', só a marca, erro de digitação...) mostra quantos vínculos o
sistema aceitou, quantos desses estavam certos (precisão) e quantos clientes ficaram com o vínculo certo (cobertura).
Serve para recalibrar `limites` no config.toml.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from cvm_pipeline import prospeccao as P  # noqa: E402
from dados_ficticios import gerar  # noqa: E402


def avaliar(pasta: Path, limites: P.Limites | None = None, quiet: bool = False) -> pd.DataFrame:
    verdade = gerar(pasta)
    res = P.executar(pasta, P.carregar_config(pasta / "config.toml"), lim=limites)
    mt = res["vinculos"]
    verdade["norm"] = verdade.nome.map(P.normalizar_nome)
    # Um nome normalizado pode ser a resposta de mais de uma gestora (ex.: a mesma marca): vale qualquer uma delas
    certas = verdade.groupby("norm").cnpj_gestor.agg(set)
    m = mt.drop_duplicates("norm").set_index("norm")
    v = verdade.merge(m[["cnpj_gestor", "confianca", "score"]], left_on="norm", right_index=True, suffixes=("", "_pred"))
    v["aceito"] = v.confianca.isin(["alta", "média"])
    v["certo"] = [p in certas[n] for p, n in zip(v.cnpj_gestor_pred, v.norm)]
    v["tem_resposta"] = v.cnpj_gestor != ""
    com = v[v.tem_resposta]
    tab = com.groupby("variante").agg(nomes=("nome", "size"), aceitos=("aceito", "sum"),
                                       certos_entre_aceitos=("certo", lambda s: int((s & com.loc[s.index, "aceito"]).sum())))
    tab["precisão"] = (tab.certos_entre_aceitos / tab.aceitos).round(3)
    tab["cobertura"] = (tab.certos_entre_aceitos / tab.nomes).round(3)
    fora = v[~v.tem_resposta]
    falsos = int(fora.aceito.sum())
    if not quiet:
        print(tab.to_string())
        print(f"\nTOTAL com resposta: {len(com)} nomes | aceitos {int(com.aceito.sum())} | "
              f"precisão {int((com.certo & com.aceito).sum()) / max(int(com.aceito.sum()), 1):.3f} | "
              f"cobertura {int((com.certo & com.aceito).sum()) / len(com):.3f}")
        print(f"empresas fora da CVM: {len(fora)} | vínculos aceitos por engano: {falsos}")
        print("confiança (nomes com resposta):", com.confianca.value_counts().to_dict())
    return v


def avaliar_listas(pasta: Path, unir_por_grupo: bool | str = "auto") -> dict:
    """Compara as três listas com a verdade (no nível da gestora): precisão = quantos dos listados deviam estar;
    cobertura = quantos dos que deviam estar foram listados. O erro que mais custa é listar quem JÁ opera BOV."""
    verdade = gerar(pasta)
    cfg = P.carregar_config(pasta / "config.toml")
    cfg["opcoes"]["unir_por_grupo"] = unir_por_grupo
    res = P.executar(pasta, cfg)
    v = verdade[verdade.cnpj_gestor != ""]
    opera = lambda base: set(v[v.base == base].cnpj_gestor)  # noqa: E731
    bmf, bov, inst = opera("BMF"), opera("BOV"), opera("INST")
    pros = set(v[(v.base == "PROSPECT") & (v.marca == "S")].cnpj_gestor)
    certo = {"bmf_sem_bov": bmf - bov, "inst_sem_bov": inst - bov, "prospects": pros - bov}
    out = {}
    for lista, esperado in certo.items():
        df = res["listas"][lista]
        previsto = set(df[df.gestoras.map(len) > 0].gestoras.explode().dropna())
        acertos = previsto & esperado
        ja_opera_bov = previsto & bov
        com_aviso = set(df[df.aviso_bov & (df.gestoras.map(len) > 0)].gestoras.explode().dropna())
        out[lista] = {"listados_com_gestora": len(previsto), "esperados": len(esperado),
                      "precisao": round(len(acertos) / max(len(previsto), 1), 3),
                      "cobertura": round(len(acertos) / max(len(esperado), 1), 3),
                      "ja_operam_bov": len(ja_opera_bov), "desses_com_aviso": len(ja_opera_bov & com_aviso),
                      "corretos_com_aviso": len(acertos & com_aviso)}
    return out


if __name__ == "__main__":
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="cvm_aval_"))
    if len(sys.argv) > 2 and sys.argv[2] == "listas":
        for unir in (True, False):
            print(f"\n=== listas, unir_por_grupo={unir} ===")
            for lista, m in avaliar_listas(base, unir).items():
                print(f"  {lista:13s} {m}")
    else:
        avaliar(base)
