param([switch]$WhatIf)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'run-sync.ps1'
$powerShell = Join-Path ([Environment]::GetFolderPath('Windows')) 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $powerShell -PathType Leaf)) {
    throw "System Windows PowerShell is missing: $powerShell"
}

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Launcher is missing: $launcher"
}

$principalName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $principalName -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -DisallowHardTerminate `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$periodicAction = New-ScheduledTaskAction -Execute $powerShell -Argument (
    '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Mode Scheduled' -f $launcher
)
$periodicTriggers = for ($slot = 0; $slot -lt 48; $slot++) {
    New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddMinutes(30 * $slot))
}
$periodicTask = New-ScheduledTask -Action $periodicAction -Trigger $periodicTriggers -Principal $principal -Settings $settings

$logonAction = New-ScheduledTaskAction -Execute $powerShell -Argument (
    '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Mode Logon' -f $launcher
)
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $principalName
$logonTask = New-ScheduledTask -Action $logonAction -Trigger $logonTrigger -Principal $principal -Settings $settings

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Синхронизировать календари.lnk'
$shortcutArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Mode Manual' -f $launcher

if ($WhatIf) {
    [pscustomobject]@{
        PeriodicTask = 'CalendarSync'
        PeriodicTriggerCount = $periodicTriggers.Count
        FirstPeriodicStart = $periodicTriggers[0].StartBoundary
        LastPeriodicStart = $periodicTriggers[-1].StartBoundary
        LogonTask = 'CalendarSyncAtLogon'
        MultipleInstances = $settings.MultipleInstances
        RestartCount = $settings.RestartCount
        StartWhenAvailable = $settings.StartWhenAvailable
        Shortcut = $shortcutPath
        Launcher = $launcher
    }
    exit 0
}

Register-ScheduledTask -TaskName 'CalendarSync' -InputObject $periodicTask -Force | Out-Null
Register-ScheduledTask -TaskName 'CalendarSyncAtLogon' -InputObject $logonTask -Force | Out-Null

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powerShell
$shortcut.Arguments = $shortcutArguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Запустить синхронизацию Outlook и Google Calendar'
$shortcut.Save()

Write-Host 'Installed CalendarSync, CalendarSyncAtLogon and the desktop shortcut.'
