"""Orquestra o pipeline: baixa -> normaliza em Parquet -> constrói os marts -> exporta CSVs.

    python run_pipeline.py                        # tudo (uso diário)
    python run_pipeline.py --so marts exportar    # só recalcula as tabelas finais
    python run_pipeline.py --meses-informe 26     # carga histórica maior (primeira vez)
    python run_pipeline.py --sem-download         # reprocessa o que já está em disco
"""
import argparse
import logging
import sys
import time
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # permite rodar com `python -I`

from cvm_pipeline import cadastro, cda, informe, ipe  # noqa: E402
from cvm_pipeline.common import ultimos_meses  # noqa: E402
from cvm_pipeline.config import LOGS  # noqa: E402

ETAPAS = ["baixar", "ingerir", "marts", "exportar"]
log = logging.getLogger("cvm")


def configurar_log() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    sys.stdout.reconfigure(encoding="utf-8")
    console = logging.StreamHandler(sys.stdout)
    arquivo = RotatingFileHandler(LOGS / "pipeline.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    for h in (console, arquivo):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--so", nargs="+", choices=ETAPAS, default=ETAPAS, help="etapas a executar")
    ap.add_argument("--meses-informe", type=int, default=14, help="meses do Informe Diário (>=14 para ter janela de 12m)")
    ap.add_argument("--meses-cda", type=int, default=5,
                    help="meses do CDA: o mais recente vem incompleto e o cruzamento por ticker precisa de meses "
                         "mais antigos, já sem posições confidenciais")
    ap.add_argument("--anos-publicacoes", type=int, default=2)
    ap.add_argument("--anos-fca", type=int, default=4, help="anos do FCA (mapa ticker -> companhia)")
    ap.add_argument("--sem-download", action="store_true", help="pula o download mesmo se 'baixar' estiver ativa")
    ap.add_argument("--refazer", action="store_true",
                    help="reprocessa os Parquets mesmo que estejam em dia (use depois de mudar a lógica de ingestão)")
    args = ap.parse_args()

    configurar_log()
    meses_inf, meses_cda = ultimos_meses(args.meses_informe), ultimos_meses(args.meses_cda)
    hoje = date.today()
    anos = list(range(hoje.year - args.anos_publicacoes + 1, hoje.year + 1))
    anos_fca = list(range(hoje.year - args.anos_fca + 1, hoje.year + 1))

    def baixar():
        cadastro.baixar_cadastro()
        informe.baixar_informe(meses_inf)
        cda.baixar_cda(meses_cda)
        ipe.baixar_publicacoes(anos, anos_fca)

    def ingerir():
        cadastro.construir_dim_classe()
        informe.ingerir_informe(meses_inf, args.refazer)
        cda.ingerir_cda(meses_cda, args.refazer)
        ipe.ingerir_publicacoes(anos)
        ipe.ingerir_tickers(anos_fca)

    def marts():
        from cvm_pipeline import marts as m
        m.construir_tudo()

    def exportar():
        from cvm_pipeline import excel, exportar as e
        e.gerar()
        excel.gerar()

    passos = {"baixar": baixar, "ingerir": ingerir, "marts": marts, "exportar": exportar}
    try:
        for etapa in ETAPAS:
            if etapa not in args.so or (etapa == "baixar" and args.sem_download):
                continue
            t0 = time.time()
            log.info("=== %s ===", etapa)
            passos[etapa]()
            log.info("=== %s ok (%.0fs) ===", etapa, time.time() - t0)
    except Exception:
        log.exception("pipeline falhou")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
