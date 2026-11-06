"""Cruza as bases internas (clientes BMF/BOV, institucional, prospects) com a CVM e lista quem o time de BOV pode
abordar, ordenado por AuM.

    python cruzar_clientes.py --modelo      1ª vez: cria a pasta de dados internos, o config.toml e o de_para_manual.csv
    python cruzar_clientes.py --checar      confere arquivos e colunas SEM mostrar nenhum dado
    python cruzar_clientes.py               roda o cruzamento e gera saida\\Prospeccao_BOV.xlsx

Os dados internos ficam FORA do projeto e do OneDrive (padrão: %USERPROFILE%\\cvm_data\\internos). Nada sai da máquina e
nenhum nome de cliente vai para o log. Antes: atualizar_dados.bat (precisa dos dados públicos da CVM).
"""
import argparse
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # permite `python -I`

from cvm_pipeline import prospeccao as P  # noqa: E402
from cvm_pipeline import prospeccao_saida as S  # noqa: E402
from cvm_pipeline.config import INTERNOS, PROJETO  # noqa: E402

MODELO = PROJETO / "config_internos.exemplo.toml"


def _recusar_onedrive(pasta: Path, permitir: bool) -> None:
    if "onedrive" in str(pasta.resolve()).lower() and not permitir:
        sys.exit(f"RECUSADO: a pasta de dados internos ({pasta}) está dentro do OneDrive, que sincroniza com a nuvem.\n"
                 "Use outra pasta (variável CVM_INTERNOS_DIR ou --pasta). Se for mesmo o que você quer: --permitir-onedrive.")


def criar_modelo(pasta: Path) -> None:
    (pasta / "entrada").mkdir(parents=True, exist_ok=True)
    (pasta / "saida").mkdir(exist_ok=True)
    cfg = pasta / "config.toml"
    if not cfg.exists():
        shutil.copy(MODELO, cfg)
    manual = pasta / "de_para_manual.csv"
    if not manual.exists():
        manual.write_text("nome;cnpj_gestor\n", encoding="utf-8")
    print(f"Pasta pronta: {pasta}\n"
          f"  1) Coloque seus arquivos (.xlsx ou .csv) em: {pasta / 'entrada'}\n"
          f"  2) Abra {cfg} e troque os nomes das colunas pelos cabeçalhos das suas planilhas\n"
          "  3) python cruzar_clientes.py --checar     (confere arquivos e colunas, sem mostrar dados)\n"
          "  4) python cruzar_clientes.py              (gera saida\\Prospeccao_BOV.xlsx)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pasta", help=f"pasta dos dados internos (padrão: {INTERNOS})")
    ap.add_argument("--config", help="config.toml (padrão: <pasta>\\config.toml)")
    ap.add_argument("--aum", choices=["aum_total", "aum_ex_fic", "aum_nao_exclusivo"], help="AuM usado para ordenar")
    ap.add_argument("--modelo", action="store_true", help="cria a pasta, o config.toml e o de_para_manual.csv")
    ap.add_argument("--checar", action="store_true", help="confere arquivos e colunas sem mostrar dados")
    ap.add_argument("--permitir-onedrive", action="store_true", help="aceita pasta dentro do OneDrive (não recomendado)")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    pasta = Path(args.pasta) if args.pasta else INTERNOS
    _recusar_onedrive(pasta, args.permitir_onedrive)
    if args.modelo:
        criar_modelo(pasta)
        return 0
    cfg_path = Path(args.config) if args.config else pasta / "config.toml"
    if not cfg_path.exists():
        sys.exit(f"Não achei {cfg_path}. Rode primeiro: python cruzar_clientes.py --modelo")
    cfg = P.carregar_config(cfg_path)
    if args.aum:
        cfg.setdefault("opcoes", {})["aum"] = args.aum

    if args.checar:
        print("\n".join(P.checar(cfg, pasta)))
        return 0

    (pasta / "saida").mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("cvm")
    log.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout),
              logging.FileHandler(pasta / "saida" / "cruzamento.log", encoding="utf-8")):  # só contagens, nunca nomes
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(h)
    try:
        res = P.executar(pasta, cfg)
        destino = S.gravar(res, pasta / "saida", cfg.get("opcoes", {}).get("aum", "aum_ex_fic"))
    except (ValueError, FileNotFoundError, PermissionError) as erro:
        sys.exit(f"ERRO: {erro}")
    r = res["resumo"]
    print(f"\nPronto: {destino}\n"
          f"  Abordagem BOV: {r['listas']['abordagem']} clientes ({r['listas_com_aum']['abordagem']} com AuM da CVM)\n"
          f"  Para conferir: {len(res['revisao'])} vínculos em 'Revisar vínculos' "
          "(corrija em de_para_manual.csv e rode de novo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
