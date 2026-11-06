"""Testes da limpeza de erros do informe diário. Rodar: python tests/test_anomalias.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cvm_pipeline.marts import filtrar_anomalias  # noqa: E402

DIAS = 30
IDX = pd.bdate_range("2026-01-05", periods=DIAS)


def serie(valores: dict[int, float], base: float) -> list[float]:
    return [valores.get(i, base) for i in range(DIAS)]


def rodar(pl: dict[str, list[float]], cap: dict[str, dict[int, float]] | None = None):
    cols = list(pl)
    plf = pd.DataFrame(pl, index=IDX)
    capf = pd.DataFrame(0.0, index=IDX, columns=cols)
    for c, v in (cap or {}).items():
        for i, x in v.items():
            capf.iloc[i, cols.index(c)] = x
    resf = pd.DataFrame(0.0, index=IDX, columns=cols)
    return filtrar_anomalias(plf, capf, resf)


def test_pl_digitado_errado_e_descartado():
    pl, _, _, an = rodar({"A": serie({10: 800e9}, 150e6)})
    assert np.isnan(pl["A"].iloc[10]) and pl["A"].drop(pl.index[10]).notna().all()
    assert list(an.tipo) == ["pl"]


def test_salto_explicado_por_captacao_passa():
    pl, cap, _, an = rodar({"B": serie({i: 2.52e9 for i in range(10, DIAS)}, 20e6)}, {"B": {10: 2.5e9}})
    assert an.empty and pl["B"].notna().all() and cap["B"].iloc[10] == 2.5e9


def test_captacao_falsa_com_pl_parado_zera_o_fluxo():
    pl, cap, _, an = rodar({"C": [351e6] * DIAS}, {"C": {4: 351e6}})
    assert list(an.tipo) == ["fluxo"] and cap["C"].iloc[4] == 0.0 and pl["C"].notna().all()


def test_erro_persistente_vira_novo_patamar():
    pl, _, _, an = rodar({"D": serie({i: 3000e6 for i in range(5, DIAS)}, 100e6)})
    assert pl["D"].iloc[5:15].isna().all()      # 10 pregões descartados...
    assert pl["D"].iloc[15:].eq(3000e6).all()   # ...depois aceito como mudança real
    assert (an.tipo == "pl").sum() == 10


def test_volatilidade_normal_nao_e_anomalia():
    rng = np.random.default_rng(0)
    pl = 500e6 * np.cumprod(1 + rng.normal(0, 0.01, DIAS))
    _, _, _, an = rodar({"E": list(pl)})
    assert an.empty


def test_pl_negativo_pequeno_nao_dispara():
    _, _, _, an = rodar({"F": serie({}, -1049e6)})
    assert an.empty


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok  ", t.__name__)
    print(f"{len(testes)} testes passaram")
