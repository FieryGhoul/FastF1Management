param(
    [Parameter(Mandatory = $true)]
    [int]$BackfillProcessId
)

$repoDir = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoDir "backend"
$pythonExe = & python -c "import sys; print(sys.executable)"
$logDir = [System.IO.Path]::GetTempPath()

try {
    Wait-Process -Id $BackfillProcessId -ErrorAction SilentlyContinue
}
finally {
    $workerRunning = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "pythoncore.*app\.worker" }
    if (-not $workerRunning) {
        Start-Process -FilePath $pythonExe -ArgumentList "-m", "app.worker" `
            -WorkingDirectory $backendDir -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logDir "race-worker.out.log") `
            -RedirectStandardError (Join-Path $logDir "race-worker.err.log")
    }

    $schedulerRunning = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "pythoncore.*app\.scheduler" }
    if (-not $schedulerRunning) {
        Start-Process -FilePath $pythonExe -ArgumentList "-m", "app.scheduler" `
            -WorkingDirectory $backendDir -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logDir "race-scheduler.out.log") `
            -RedirectStandardError (Join-Path $logDir "race-scheduler.err.log")
    }
}
