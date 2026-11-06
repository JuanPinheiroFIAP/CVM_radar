@echo off
rem Atualiza os dados da CVM: baixa, normaliza, recalcula os marts e gera os CSVs do Salesforce.
rem Agende no Agendador de Tarefas do Windows para rodar todo dia ~07:30 (a CVM publica de madrugada).
rem Para uma carga historica maior na primeira vez: atualizar_dados.bat --meses-informe 26
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" run_pipeline.py %*
exit /b %ERRORLEVEL%
