@echo off
REM Sobe o dashboard a partir de nenc-dashboard/ — mesmo cwd do Dockerfile,
REM para que .streamlit/config.toml seja carregado.
cd /d "%~dp0..\nenc-dashboard" || exit /b 1
"%~dp0..\.venv\Scripts\python.exe" -m streamlit run app.py --server.port=8501 --server.headless=true --browser.gatherUsageStats=false
