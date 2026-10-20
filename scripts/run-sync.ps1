param(
    [ValidateSet('Manual', 'Scheduled', 'Logon')]
    [string]$Mode = 'Manual'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$config = Join-Path $projectRoot 'config.json'

function Show-Notification([string]$message, [int]$seconds = 10) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($message, $seconds, 'Calendar Sync', 0x40)
    } catch {
        # Notification failure must not change the synchronization result.
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $message = 'Calendar Sync: Python environment is missing.'
    if ($Mode -eq 'Manual') { Write-Host $message -ForegroundColor Red } else { Show-Notification $message }
    exit 2
}
if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
    $message = 'Calendar Sync: config.json is missing.'
    if ($Mode -eq 'Manual') { Write-Host $message -ForegroundColor Red } else { Show-Notification $message }
    exit 2
}

if ($Mode -eq 'Manual') {
    Write-Host 'Синхронизация календарей запущена...'
}

Push-Location $projectRoot
try {
    & $python -m calendar_sync apply --config $config --confirm-initial-fill
    $exitCode = $LASTEXITCODE
} catch {
    $exitCode = 2
} finally {
    Pop-Location
}

if ($Mode -eq 'Manual') {
    if ($exitCode -eq 0) {
        Write-Host 'Синхронизация успешно завершена.' -ForegroundColor Green
    } elseif ($exitCode -eq 2) {
        Write-Host 'Синхронизация не запущена. Возможно, другой экземпляр уже работает.' -ForegroundColor Yellow
    } else {
        Write-Host "Синхронизация завершилась с ошибкой. Код: $exitCode" -ForegroundColor Red
    }
    Write-Host 'Нажмите Enter для выхода.'
    $null = Read-Host
} elseif ($exitCode -ne 0) {
    Show-Notification "Calendar Sync завершился с ошибкой. Код: $exitCode"
} elseif ($Mode -eq 'Logon') {
    Show-Notification 'Calendar Sync успешно запущен после входа в Windows.'
}

exit $exitCode
