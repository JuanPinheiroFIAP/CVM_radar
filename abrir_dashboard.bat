@echo off
rem Sobe o dashboard em http://localhost:8501 (e na rede interna, em http://<ip-desta-maquina>:8501).
rem Sem login: so publique em rede interna.
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m streamlit run app\streamlit_app.py --server.address 0.0.0.0 --server.port 8501
