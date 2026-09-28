# Register an hourly Windows scheduled task that runs calendar-hourly-chime in WSL.
# (Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less files as ANSI.)
#   From WSL:  powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w scripts/install_task.ps1)"
#   Remove:    ... -File install_task.ps1 -Uninstall
param(
    [string]$Distro = "Ubuntu-20.04",
    [string]$RepoDir = "/home/yue/trunk/calendar-hourly-chime",
    [string]$TaskName = "calendar-hourly-chime",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Unregistered: $TaskName"
    return
}

# conhost --headless: run wsl.exe without flashing a console window
$args_ = "--headless wsl.exe -d $Distro --cd $RepoDir -e $RepoDir/.venv/bin/calendar-hourly-chime run"
$action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument $args_

# Every 5 minutes at xx:x4:00 / xx:x9:00; each run plays the cues in the following 5 minutes
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(4) -RepetitionInterval (New-TimeSpan -Minutes 5)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 8) `
    -MultipleInstances Parallel
# Parallel: a run may still be playing its last cue when the next one starts
# Do not catch up on chimes missed while asleep
$settings.StartWhenAvailable = $false

# Run in the logged-on user's session so audio works
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered: $TaskName"
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Select-Object LastRunTime, NextRunTime
