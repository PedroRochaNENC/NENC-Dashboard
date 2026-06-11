@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
set "APP_PATH=%PROJECT_ROOT%nenc-dashboard\app.py"

if not exist "%PYTHON_EXE%" (
  echo Nao encontrei o Python da venv em: %PYTHON_EXE%
  exit /b 1
)

if not exist "%APP_PATH%" (
  echo Nao encontrei o app em: %APP_PATH%
  exit /b 1
)

"%PYTHON_EXE%" -m streamlit run "%APP_PATH%"
