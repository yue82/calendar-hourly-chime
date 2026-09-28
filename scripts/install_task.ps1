# Register an hourly Windows scheduled task that runs hourly-chime in WSL.
# (Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less files as ANSI.)
#   From WSL:  powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w scripts/install_task.ps1)"
#   Remove:    ... -File install_task.ps1 -Uninstall
param(
    [string]$Distro = "Ubuntu-20.04",
    [string]$RepoDir = "/home/yue/trunk/hourly-chime",
    [string]$TaskName = "hourly-chime",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Unregistered: $TaskName"
    return
}

# conhost --headless: run wsl.exe without flashing a console window
$args_ = "--headless wsl.exe -d $Distro --cd $RepoDir -e $RepoDir/.venv/bin/hourly-chime chime"
$action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument $args_

# Every hour on the hour, starting today 00:00, repeating indefinitely
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew
# Do not catch up on chimes missed while asleep
$settings.StartWhenAvailable = $false

# Run in the logged-on user's session so audio works
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered: $TaskName"
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo | Select-Object LastRunTime, NextRunTime
