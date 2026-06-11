$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$appPath = Join-Path $projectRoot "nenc-dashboard\app.py"

if (-not (Test-Path $pythonExe)) {
    Write-Error "Nao encontrei o Python da venv em: $pythonExe"
    exit 1
}

if (-not (Test-Path $appPath)) {
    Write-Error "Nao encontrei o app em: $appPath"
    exit 1
}

& $pythonExe -m streamlit run $appPath
