"""Gera bases internas FICTÍCIAS para testar o cruzamento: nomes PÚBLICOS da CVM, deformados do jeito que
sistemas internos costumam deformar (sem 'LTDA', abreviado, só a marca, erro de digitação, nome de fundo em
vez da gestora). Como a resposta certa é conhecida, dá para medir precisão e cobertura do casamento por nome.

Uso: python tests/dados_ficticios.py <pasta-de-saida>      (precisa ter rodado atualizar_dados.bat antes)
Escreve <pasta>/entrada/*.csv|xlsx, <pasta>/config.toml e <pasta>/verdade.csv (nome -> cnpj_gestor, variante).
"""
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from cvm_pipeline.config import MARTS, PARQUET  # noqa: E402


def trocar_letras(palavra: str, rng: random.Random) -> str:
    i = rng.randrange(1, len(palavra) - 2)
    return palavra[:i] + palavra[i + 1] + palavra[i] + palavra[i + 2:]


def variantes(nome: str, fundo: str | None, rng: random.Random) -> dict[str, str]:
    sem_forma = re.sub(r"\s+(LTDA\.?|S\.?\s?/?A\.?|EIRELI)\s*$", "", nome.strip(), flags=re.I).strip(" .,-")
    tokens = sem_forma.split()
    v = {
        "exata": nome,
        "sem_forma_juridica": sem_forma,
        "gestora_abreviada": re.sub(r"GEST[ÃA]O DE RECURSOS", "GESTORA", re.sub(r"ASSET MANAGEMENT", "AM", sem_forma, flags=re.I),
                                    flags=re.I),
        "duas_palavras": " ".join(tokens[:2]) if len(tokens) > 2 else sem_forma,
        "so_a_marca": tokens[0] if tokens else sem_forma,
    }
    longas = [t for t in tokens if len(t) >= 6]
    if longas:
        alvo = rng.choice(longas)
        v["erro_de_digitacao"] = sem_forma.replace(alvo, trocar_letras(alvo, rng), 1)
    if fundo:
        v["nome_de_fundo"] = fundo
    return v


def gerar(pasta: Path, n_clientes: int = 400, n_prospects: int = 150, n_ruido: int = 80, seed: int = 7) -> pd.DataFrame:
    rng = random.Random(seed)
    dim = pd.read_parquet(PARQUET / "dim_classe.parquet")
    gest = pd.read_parquet(MARTS / "gestora_snapshot.parquet")
    pool = gest[gest.aum_total >= 5e7].cnpj_gestor.tolist()
    rng.shuffle(pool)
    clientes, prospects = pool[:n_clientes], pool[n_clientes:n_clientes + n_prospects]
    nome_g = dim.drop_duplicates("cnpj_gestor").set_index("cnpj_gestor").nome_gestor
    fundos = dim[dim.ativa].groupby("cnpj_gestor").agg(nome=("nome_classe", list), cnpj=("cnpj_classe", list))

    def fundo_de(g: str) -> tuple[str | None, str | None]:
        if g not in fundos.index:
            return None, None
        i = rng.randrange(len(fundos.loc[g, "nome"]))
        return fundos.loc[g, "nome"][i], fundos.loc[g, "cnpj"][i]

    linhas_bmf, linhas_bov, inst, pros, verdade = [], [], [], [], []
    conta = 100000
    for k, g in enumerate(clientes, start=1):
        segmentos = rng.choice([("BMF",), ("BOV",), ("BMF", "BOV")])
        for seg in segmentos:
            for _ in range(rng.randint(1, 2)):
                nome_fundo, _ = fundo_de(g)
                tipo, nome = rng.choice(list(variantes(nome_g[g], nome_fundo, rng).items()))
                conta += 1
                (linhas_bmf if seg == "BMF" else linhas_bov).append({"COD_GRUPO": str(k), "NOME_CLIENTE": nome, "CONTA": str(conta)})
                verdade.append({"nome": nome, "cnpj_gestor": g, "variante": tipo, "base": seg, "grupo": str(k), "marca": ""})
        if rng.random() < 0.3:  # parte dos clientes também está na base institucional, com CNPJ (gestora ou fundo)
            nome_fundo, cnpj_fundo = fundo_de(g)
            usa_fundo = cnpj_fundo is not None and rng.random() < 0.4
            tipo, nome = ("cnpj_de_fundo", nome_fundo) if usa_fundo else ("cnpj_da_gestora", nome_g[g])
            inst.append({"CNPJ": cnpj_fundo if usa_fundo else g, "NOME": nome})
            verdade.append({"nome": nome, "cnpj_gestor": g, "variante": tipo, "base": "INST", "grupo": str(k), "marca": ""})
    palavras = ["ALFA", "BRAVO", "CERRADO", "DOURADO", "ESTRELA", "FRONTEIRA", "GAUCHO", "HORIZONTE", "ITAPEMA", "JACARANDA",
                "KALUNGA", "LITORAL", "MANTIQUEIRA", "NORDESTE", "OCEANO", "PAMPA", "QUARTZO", "RIBEIRAO", "SERTAO", "TROPICAL"]
    for _ in range(n_ruido):  # empresas que NÃO existem na CVM (hedge de empresa de verdade): não podem casar
        nome = f"{rng.choice(['COMERCIAL', 'AGRO', 'USINA', 'TRANSPORTES', 'INDUSTRIA'])} {rng.choice(palavras)} {rng.choice(palavras)} S/A"
        conta += 1
        linhas_bmf.append({"COD_GRUPO": str(10000 + _), "NOME_CLIENTE": nome, "CONTA": str(conta)})
        verdade.append({"nome": nome, "cnpj_gestor": "", "variante": "empresa_fora_da_cvm", "base": "BMF", "grupo": str(10000 + _), "marca": ""})
    for g in prospects:
        tipo, nome = rng.choice([kv for kv in variantes(nome_g[g], None, rng).items() if kv[0] != "so_a_marca"])
        marca = "S" if rng.random() < 0.8 else "N"
        pros.append({"NOME": nome, "PROSPECT": marca})
        verdade.append({"nome": nome, "cnpj_gestor": g, "variante": tipo, "base": "PROSPECT", "grupo": "", "marca": marca})

    (pasta / "entrada").mkdir(parents=True, exist_ok=True)
    for nome_arq, linhas in (("clientes_bmf.csv", linhas_bmf), ("clientes_bov.csv", linhas_bov), ("prospects.csv", pros)):
        pd.DataFrame(linhas).to_csv(pasta / "entrada" / nome_arq, sep=";", index=False, encoding="cp1252", errors="replace")
    pd.DataFrame(inst).to_excel(pasta / "entrada" / "institucional.xlsx", index=False)   # um em Excel, para testar a leitura
    (pasta / "config.toml").write_text(
        '[bmf]\narquivo = "clientes_bmf.csv"\ngrupo = "COD_GRUPO"\ncliente = "NOME_CLIENTE"\nconta = "CONTA"\n\n'
        '[bov]\narquivo = "clientes_bov.csv"\ngrupo = "COD_GRUPO"\ncliente = "NOME_CLIENTE"\nconta = "CONTA"\n\n'
        '[institucional]\narquivo = "institucional.xlsx"\ncnpj = "CNPJ"\nnome = "NOME"\n\n'
        '[prospects]\narquivo = "prospects.csv"\nnome = "NOME"\nmarca = "PROSPECT"\nvalores_marca = ["S"]\n\n'
        '[opcoes]\naum = "aum_ex_fic"\n', encoding="utf-8")
    v = pd.DataFrame(verdade)
    v.to_csv(pasta / "verdade.csv", sep=";", index=False, encoding="utf-8-sig")
    return v


if __name__ == "__main__":
    destino = Path(sys.argv[1])
    v = gerar(destino)
    print(f"gerado em {destino}: {len(v)} nomes ({v.variante.value_counts().to_dict()})")
