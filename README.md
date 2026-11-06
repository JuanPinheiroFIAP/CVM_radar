# CVM Radar

Pipeline e dashboard com **dados públicos da CVM** (dados.cvm.gov.br). Python + Parquet + Streamlit, sem banco de dados.

| Entregável | Onde |
|---|---|
| AuM e captação líquida por gestora e por fundo, para o CRM | aba *Gestoras*; arquivos em `saidas\` |
| Monitor de captação vs peers (ex.: Multimercado Macro) | aba *Monitor vs peers* |
| Penetração por produto vs peers, aluguel de ações e **segmento BMF/BOV** | aba *Penetração e BMF/BOV* |
| Fatos relevantes, recompras, assembleias, cruzados com quem tem a ação | aba *Radar de publicações* |

O pipeline e o dashboard usam só dados públicos. O cruzamento com as bases internas (prospecção BOV) roda à parte, com os dados fora deste projeto: ver "Prospecção BOV" abaixo.

## Os arquivos para olhar (`saidas\`, dentro do projeto)

| Arquivo | Para quê |
|---|---|
| `CVM_Radar.xlsx` | **Para olhar no Excel**: gestoras, fundos, peers, aluguel, penetração e publicações, com filtros, cabeçalho fixo, links e aba "Leia-me" |
| `salesforce_gestoras.csv` | Carga no Salesforce: 1 linha por gestora (chave: CNPJ) |
| `salesforce_fundos.csv` | Carga no Salesforce: 1 linha por classe de fundo |

São reescritos a cada atualização. Se estiverem abertos no Excel, o pipeline avisa e pula (feche e rode `python run_pipeline.py --so exportar`). Os CSVs usam vírgula e UTF-8: abrem melhor pelo Excel em *Dados > De Texto/CSV*; para olhar, use o `.xlsx`.

## Rodar

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
atualizar_dados.bat                      rem baixa e calcula tudo (~1 min na primeira vez)
abrir_dashboard.bat                      rem http://localhost:8501
```

- `atualizar_dados.bat --meses-informe 26` faz uma carga histórica maior. Mínimo útil: 14 meses (a janela de 12 meses precisa de 253 pregões).
- `python run_pipeline.py --so marts exportar` recalcula só as tabelas finais (~20 s), sem baixar.
- `python run_pipeline.py --refazer` reprocessa os Parquets mesmo que estejam em dia. **Use depois de mudar a lógica de ingestão** (a ingestão só compara datas de arquivo).
- Os downloads são incrementais: só baixa o que mudou no servidor da CVM.
- `streamlit run app\streamlit_app.py` abre só nesta máquina. O tema (claro/escuro automático) está em `.streamlit\config.toml`.

### No servidor (máquina que fica ligada)

1. Copie o projeto, crie o `.venv` e instale os requisitos (acima). O `.venv` não é portável: recrie na máquina nova.
2. Agende a atualização diária, depois das 07:00 (a CVM publica de madrugada):
   ```bat
   schtasks /create /tn "CVM Radar - atualizar" /tr "\"C:\caminho\CVM\atualizar_dados.bat\"" /sc daily /st 07:30
   ```
3. Dashboard sempre no ar: agende `abrir_dashboard.bat` "ao iniciar o sistema". Ele escuta na **rede interna** (porta 8501) e **não tem login**: só dados públicos, mas mantenha em rede interna.
4. Os dados brutos e limpos (~370 MB) ficam em `%USERPROFILE%\cvm_data`, **fora do OneDrive** de propósito. Para outro disco: variável de ambiente `CVM_DATA_DIR`. (Só as saídas ficam na pasta do projeto.)
5. Logs: `cvm_data\logs\pipeline.log`. O pipeline devolve código de saída 1 se falhar.

## Estrutura

```
run_pipeline.py          orquestrador (baixar -> ingerir -> marts -> exportar)
cruzar_clientes.py       cruza as bases internas com a CVM (prospecção BOV): --modelo, --checar, execução
config_internos.exemplo.toml   modelo de configuração das bases internas (só nomes de coluna)
cvm_pipeline/
  config.py              caminhos e TODOS os parâmetros de negócio (limites, janelas, filtros, BMF/BOV)
  download.py            download incremental (Last-Modified/tamanho)
  cadastro.py            registro de fundos (Res. 175): classe -> fundo -> gestora, grupo de peers
  informe.py             informe diário: PL, captação, resgates por classe e dia
  cda.py                 carteiras: posição por classe e produto, ticker das ações, contrato dos futuros
  ipe.py                 publicações das companhias (IPE) e ticker -> CNPJ (FCA)
  marts.py               limpeza, captação, peers, BMF/BOV, penetração, aluguel, cruzamentos
  exportar.py            CSVs para o Salesforce
  excel.py               planilha CVM_Radar.xlsx
  prospeccao.py          cruzamento com as bases internas: leitura, vínculo por CNPJ/nome, consolidação, listas, alertas
  prospeccao_saida.py    planilha Prospeccao_BOV.xlsx e arquivo de correções
app/streamlit_app.py     dashboard (layout e dados)
app/estilo.py            identidade visual: paleta claro/escuro, CSS, gráficos
tests/                   testes (limpeza de anomalias, cruzamento) e geração de bases fictícias para avaliar o vínculo
```

Camadas em `cvm_data\`: `raw\` (como veio da CVM) → `parquet\` (limpo) → `marts\` (tabelas finais que o app lê).

## Como os números são calculados

- **AuM total**: soma do PL das classes (informe diário). **AuM ex-FIC** retira fundos de cotas (flag do registro ou ≥95% do PL em cotas de outros fundos), evitando dupla contagem: ~40% do PL do mercado é FIC.
- **Captação líquida** = captação − resgates. **% do PL inicial** = captação líquida ÷ PL do dia anterior à janela (1, 5, 21, 63, 252 pregões).
- **Peers** = classe ANBIMA (ex.: *Multimercados Macro*), só fundos **não exclusivos**. **Média dos peers** = captação do grupo ÷ PL inicial do grupo (ponderada por PL). A mediana é só referência: em janelas curtas costuma ser 0.
- **Esperado / excesso (gestora)**: quanto a gestora teria captado se cada fundo captasse como a média do seu grupo; excesso = captação − esperado. Ajusta pelo mix de classes da gestora.
- **Penetração**: peso de cada produto (títulos públicos, crédito, ações, opções, futuros, swaps, exterior, aluguel...) no PL dos fundos finais da gestora, vs peers (classe ANBIMA + faixa de PL). **Aluguel**: exposição = ações em carteira + ações cedidas; utilização = cedidas ÷ exposição; potencial = quanto cederia se usasse como os peers.

### Segmento B3: BMF ou BOV

Classificação de cada classe pelas posições do CDA do mês (inclui as confidenciais, que vêm agregadas):

| Rótulo | Regra |
|---|---|
| **BMF** | tem posição em **futuros** (qualquer contrato). O fator (juros, câmbio, índice, commodities) vem do contrato: DI1/DAP = juros, DOL/WDO/DDI = câmbio, IND/WIN = índice, BGI/milho/café = commodities |
| **BOV** | renda variável direta (ações, units, BDR, ações cedidas) ≥ 0,5% do PL, **ou** qualquer posição em opções, termo ou aluguel tomador |
| **BMF + BOV** | os dois |
| **Sem BMF/BOV** | renda fixa, crédito, cotas |
| **FIC** | fundo de cotas: a carteira real está no fundo-alvo, não entra na classificação |

A **gestora** é BMF/BOV quando ≥ 5% do PL dos seus fundos finais está em classes do segmento (e mostra `% PL em BMF` e `% PL em BOV`). Limites em `config.py` (`BOV_MIN_PCT_PL`, `SEGMENTO_GESTORA_MIN_PCT`).

Limites desta classificação: é a **posição no fim do mês**, não o que o fundo operou; ~60% das classes com futuros têm isso só no arquivo confidencial (sem o contrato), então o fator aparece como "não detalhado"; opções contam como BOV por serem, na maioria, sobre ações (opções de Ibovespa/dólar/flexíveis ficam junto, são poucas); swaps não entram no rótulo. O Perfil Mensal da CVM não ajuda: o campo de nocional de derivativos vem vazio para quase todos os fundos.

## Limitações (leia antes de usar o número)

- **Defasagem**: o informe diário chega com 2 a 4 dias de atraso e os últimos dias estão sempre incompletos. O snapshot usa o último dia com ≥95% dos fundos (`COBERTURA_MIN_DIA` em `config.py`; com 0,90 sai ~1 dia mais fresco, com mais ruído).
- **Erros de preenchimento da CVM são frequentes** (PL de R$ 0,15 bi virando R$ 824 bi por um dia; "captação" igual ao PL com o PL parado). `filtrar_anomalias` descarta o PL ou zera o fluxo; a lista fica na aba *Exportar e qualidade*. Podem sobrar casos pequenos.
- **Carteiras (CDA) são mensais**, e posições sob sigilo (até 90 dias) vêm só agregadas por tipo, sem ticker. As métricas por *produto* usam o mês mais recente completo (inclui o confidencial). O cruzamento *quem tem a ação* usa o mês mais recente com ≥50% da exposição identificada por ticker, e é um **piso**, não o total.
- **Recompra** não tem categoria própria no IPE: é detectada por texto do assunto (heurística, com subtipo aprovação/encerramento). Confira o documento.
- Mapa ticker → companhia vem do FCA; para casos em que o FCA traz código inválido há `OVERRIDE_TICKERS` em `ipe.py`.
- Gestora = gestora cadastrada no **fundo** (registro_fundo). Fundos de fundos de outra gestora contam no AuM total dela, mas não no ex-FIC.

## Prospecção BOV: cruzar as suas bases com a CVM

Lista, por AuM, quem o time de BOV pode abordar: clientes que operam **BMF e não BOV**, **institucionais sem BOV** e **prospects**.
Roda na sua máquina. Os dados internos ficam em `%USERPROFILE%\cvm_data\internos`, **fora do projeto e do OneDrive** (o programa recusa uma pasta sob OneDrive). Nada sai da máquina e nenhum nome de cliente vai para o log.

```bat
python cruzar_clientes.py --modelo     rem 1ª vez: cria a pasta (entrada\, saida\), o config.toml e o de_para_manual.csv
                                       rem copie suas planilhas (.xlsx ou .csv) para ...\internos\entrada\
                                       rem abra ...\internos\config.toml e troque os nomes das colunas pelos cabeçalhos reais
python cruzar_clientes.py --checar     rem confere arquivos e colunas SEM mostrar nenhum dado (só nomes de coluna e contagens)
python cruzar_clientes.py              rem gera ...\internos\saida\Prospeccao_BOV.xlsx
```

**O que cada base precisa ter** (os nomes das colunas você informa no `config.toml`; modelo comentado em `config_internos.exemplo.toml`):

| Base | Colunas | Observação |
|---|---|---|
| Clientes BMF e clientes BOV | código do grupo, nome do cliente, número da conta | um arquivo por segmento (blocos `[bmf]` e `[bov]`) **ou** um arquivo com coluna de segmento (bloco `[clientes]`) |
| Institucional | CNPJ, nome | é a ponte mais confiável com a CVM (casa por CNPJ) |
| Prospects | nome (+ coluna que marca prospect, opcional) | sem a coluna de marca, toda linha é prospect |

**Como o vínculo com a CVM é feito.** As bases de clientes não têm CNPJ, então o vínculo é por **nome**, nesta ordem: CNPJ (institucional) → nome exato → núcleo do nome (sem "LTDA", "Gestão de Recursos" etc.) → nome aproximado. Cada vínculo recebe uma confiança: **alta**, **média** (usado, mas confira), **ambígua**, **fraca** ou **sem vínculo** (empresa que não é gestora na CVM). Nome de fundo é ligado à gestora dele. Tudo que é o mesmo cliente (mesmo código de grupo, CNPJ, nome ou gestora na CVM) vira um cliente só: quem opera BOV por um fundo da mesma gestora **não** aparece como "BMF sem BOV". O código de grupo só é usado se valer nas duas bases (`unir_por_grupo = "auto"` confere isso).

**A planilha `Prospeccao_BOV.xlsx`**: *Abordagem BOV* (lista única, sem repetir, por AuM), *BMF sem BOV*, *Institucional sem BOV*, *Prospects*, *Revisar vínculos*, *Resumo* e *Leia-me*. Cada cliente traz o AuM da CVM, o segmento B3 (BMF/BOV) da gestora, a exposição em ações, a oportunidade de aluguel e a captação recente. A coluna **Atenção** (amarela) avisa quando o cliente parece já operar BOV com outra grafia do nome: confira antes de abordar.

**Corrigindo vínculos.** Abra `saida\de_para_para_preencher.csv`, preencha `cnpj_gestor` (ou `IGNORAR` se o cliente não tem gestora na CVM), salve como `de_para_manual.csv` na pasta principal dos dados internos e rode de novo. As correções valem para sempre.

**Qualidade medida** (bases fictícias feitas com nomes públicos da CVM, deformados como sistemas internos deformam, com resposta conhecida; `python tests/avaliar_vinculo.py <pasta> listas`):

| Medida | Resultado |
|---|---|
| Vínculos aceitos que estão certos | 98,7% (os de confiança "alta": 99,9%) |
| Nomes ligados à gestora certa | 86,7% (100% com CNPJ, nome exato ou nome de fundo; 38% quando só a marca, que é ambígua por natureza) |
| Empresas fora da CVM ligadas por engano | 0 de 80 |
| Listas com código de grupo válido: precisão | 99% (BMF sem BOV), 98% (institucional), 99% (prospects) |
| Sem o código de grupo (pior caso) | ~11% da lista BMF sem BOV já opera BOV com outra grafia, e **todos** esses vêm com "Atenção" |

**Limites.** O AuM vem do informe diário (fundos abertos): não inclui FIDC, FII, FIP nem carteira administrada, e cliente que não é gestora (ex.: empresa que só faz hedge) fica sem AuM. Nomes curtos que servem a várias empresas do mesmo grupo (Safra, Itaú, BTG) ficam "ambíguos": resolva no `de_para_manual.csv`. O segmento B3 da CVM é a visão **pública** da gestora, não o que ela opera conosco.

## Testes

```bat
.venv\Scripts\python tests\test_anomalias.py       rem limpeza de erros do informe
.venv\Scripts\python tests\test_prospeccao.py      rem cruzamento com as bases internas (dados fictícios)
.venv\Scripts\python tests\avaliar_vinculo.py <pasta-temporaria>          rem precisão do casamento por nome
.venv\Scripts\python tests\avaliar_vinculo.py <pasta-temporaria> listas   rem precisão das listas
```
