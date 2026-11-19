# Agent BL-RMO : réglages Windows posés et retirés par l'installeur (setup-agent-bl-rmo.exe).
#   -Action install    droits du dossier de réglages, règle du pare-feu pour netaudio,
#                      retrait de l'ancienne sonde Dante, tâche planifiée « BL-RMO Agent »
#   -Action uninstall  arrêt de l'agent, retrait de la tâche et de la règle du pare-feu
param(
    [Parameter(Mandatory)][ValidateSet('install', 'uninstall')][string]$Action,
    [Parameter(Mandatory)][string]$AppDir
)
$ErrorActionPreference = 'Continue'
$Task = 'BL-RMO Agent'
$Rule = 'BL-RMO Agent (Dante)'
$Data = Join-Path $env:ProgramData 'BL-RMO\agent'
$Exe = Join-Path $AppDir 'agent-bl-rmo.exe'

function Stop-Agent {
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
    Get-Process -Name 'agent-bl-rmo', 'netaudio-daemon' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

if ($Action -eq 'uninstall') {
    Stop-Agent
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    exit 0
}

# Ancienne sonde Dante (script Python) : remplacée par le module Dante de l'agent
$old = Get-ScheduledTask -TaskName 'BL-RMO Sonde Dante' -ErrorAction SilentlyContinue
if ($old) {
    Stop-ScheduledTask -TaskName 'BL-RMO Sonde Dante' -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName 'BL-RMO Sonde Dante' -Confirm:$false -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*sonde-dante.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Remove-Item (Join-Path $env:ProgramData 'BL-RMO\sonde-dante') -Recurse -Force -ErrorAction SilentlyContinue

# Réglages (jeton) : lisibles par le système et les administrateurs seulement
New-Item -ItemType Directory -Force -Path $Data | Out-Null
& icacls $Data /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null

# Pare-feu : réponses mDNS et Dante reçues par le moteur de netaudio
Get-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
$daemon = Join-Path $AppDir '_internal\netaudio\bin\netaudio-daemon.exe'
New-NetFirewallRule -DisplayName $Rule -Description 'Lecture du réseau Dante par l''agent BL-RMO' `
    -Direction Inbound -Program $daemon -Protocol UDP -Action Allow -Profile Any | Out-Null

# Tâche planifiée : au démarrage du PC, même sans session ouverte, compte SYSTEM
Stop-Agent
$action = New-ScheduledTaskAction -Execute $Exe -Argument 'run' -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Agent BL-RMO : relevés de ce PC envoyés à BL-RMO' -Force | Out-Null
if (-not (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue)) { exit 1 }
exit 0
