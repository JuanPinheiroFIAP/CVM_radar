"""Caminhos e parâmetros de negócio do pipeline."""
import os
from pathlib import Path

BASE_URL = "https://dados.cvm.gov.br/dados"

PROJETO = Path(__file__).resolve().parent.parent

# Os dados ficam FORA da pasta do projeto: o projeto está no OneDrive e ZIPs/Parquets
# grandes não devem ser sincronizados com a nuvem. Troque com a variável CVM_DATA_DIR
# (no servidor, aponte para um disco local).
DADOS = Path(os.environ.get("CVM_DATA_DIR", Path.home() / "cvm_data"))
# Bases INTERNAS (clientes, prospects) e os resultados do cruzamento com a CVM. Sensível: fica fora do
# projeto e fora do OneDrive. O código se recusa a gravar em pasta sob OneDrive (ver cruzar_clientes.py).
INTERNOS = Path(os.environ.get("CVM_INTERNOS_DIR", DADOS / "internos"))

RAW = DADOS / "raw"          # arquivos como baixados da CVM (não confiáveis: só leitura)
PARQUET = DADOS / "parquet"  # camada limpa: fatos e dimensões
MARTS = DADOS / "marts"      # tabelas finais, lidas pelo app
# Saídas para olhar e carregar no CRM (CSVs do Salesforce + planilha Excel). Ficam DENTRO do projeto,
# visíveis na pasta: são poucos MB e reescritos a cada carga. Se o arquivo estiver aberto no Excel
# durante a atualização, o pipeline avisa e pula (não derruba a carga).
SAIDAS = PROJETO / "saidas"
LOGS = DADOS / "logs"

# --- Parâmetros de negócio -------------------------------------------------------------
JANELAS = (1, 5, 21, 63, 252)      # em pregões: dia, semana, mês, trimestre, ano
PL_MIN_PEERS = 1_000_000           # classes menores não entram nas estatísticas de peers
FLUXO_MAX_X_PL0 = 3.0              # fluxo > 3x o PL inicial é lançamento/aporte de estruturação: sai da base em %
MIN_FUNDOS_GRUPO = 10              # grupo de peers menor que isso não gera benchmark
COBERTURA_MIN_DIA = 0.95           # dia só é "fechado" se >=95% dos fundos típicos reportaram
FIC_PCT_COTAS = 0.95               # >=95% do PL em cotas de outros fundos => tratado como FIC
FFILL_PL_DIAS = 5                  # classe que atrasou o informe mantém o último PL por até 5 pregões
CDA_COMPLETO_MIN = 0.90            # mês do CDA só é "completo" com >=90% das classes do melhor mês
ACOES_MIN_OPORTUNIDADE = 20e6      # gestora só é "oportunidade de aluguel" com >= R$ 20 mi em ações

# Segmento da B3 (BMF = futuros; BOV = ações à vista, BDR, aluguel, termo e opções)
BOV_MIN_PCT_PL = 0.005             # renda variável direta >= 0,5% do PL marca BOV (abaixo disso é resíduo)
SEGMENTO_GESTORA_MIN_PCT = 0.05    # gestora é BMF/BOV se >= 5% do PL (fundos finais) está em classes do segmento

# Erros de preenchimento do informe (ex.: fundo de R$ 0,2 bi que informa R$ 824 bi por um dia)
ANOMALIA_FRACAO = 0.95             # PL não explicado por fluxos acima de 95% do maior PL => descarta
ANOMALIA_MIN_PL = 10e6             # só avalia classes com PL >= R$ 10 mi (abaixo disso é ruído)
ANOMALIA_DIAS_MAX = 10             # "erro" que dura mais que isso é tratado como mudança real de patamar

ROTULO_PRODUTO = {
    "acoes": "Ações", "bdr": "BDR", "aluguel_doador": "Aluguel de ações (doador)",
    "aluguel_tomador": "Aluguel de ações (tomador)", "opcoes": "Opções", "futuros": "Futuros",
    "termo": "Termo", "swaps": "Swaps", "titulos_publicos": "Títulos públicos",
    "compromissadas": "Compromissadas", "credito_corporativo": "Crédito corporativo (debêntures, CRI/CRA)",
    "titulos_bancarios": "Títulos bancários (CDB, LF)", "cotas_fundos": "Cotas de fundos", "exterior": "Exterior"}

FAIXAS_PL = [0, 50e6, 200e6, 1e9, float("inf")]
ROTULOS_FAIXA = ["< 50 mi", "50-200 mi", "200 mi-1 bi", "> 1 bi"]
