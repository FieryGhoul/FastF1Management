$projectRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $projectRoot "backend"
$frontendPath = Join-Path $projectRoot "frontend"
$pythonPath = "C:\Users\tp732\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$npmPath = "C:\Program Files\nodejs\npm.cmd"

$apiListening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $apiListening) {
    Start-Process -FilePath $pythonPath -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory $backendPath -WindowStyle Hidden
}

$processes = Get-CimInstance Win32_Process
if (-not ($processes | Where-Object { $_.CommandLine -match "-m\s+app\.worker" })) {
    Start-Process -FilePath $pythonPath -ArgumentList "-m", "app.worker" -WorkingDirectory $backendPath -WindowStyle Hidden
}
if (-not ($processes | Where-Object { $_.CommandLine -match "-m\s+app\.scheduler" })) {
    Start-Process -FilePath $pythonPath -ArgumentList "-m", "app.scheduler" -WorkingDirectory $backendPath -WindowStyle Hidden
}

$frontendListening = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue
if (-not $frontendListening) {
    Start-Process -FilePath $npmPath -ArgumentList "run", "dev", "--", "--host", "127.0.0.1" -WorkingDirectory $frontendPath -WindowStyle Hidden
}
