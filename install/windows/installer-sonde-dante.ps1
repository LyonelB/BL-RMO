# Installe la sonde Dante de BL-RMO sur un PC Windows qui voit le réseau Dante (ex. COOR).
# A lancer dans PowerShell ouvert en administrateur, depuis le dossier install\windows du dépôt :
#   powershell -ExecutionPolicy Bypass -File .\installer-sonde-dante.ps1
# Pour la retirer :
#   powershell -ExecutionPolicy Bypass -File .\installer-sonde-dante.ps1 -Desinstaller
#
# - installe netaudio (version figée) pour Python 3.10 ou plus récent, installé pour tous les utilisateurs ;
# - copie la sonde dans C:\ProgramData\BL-RMO\sonde-dante, avec ses réglages lisibles par
#   l'administrateur et le système seulement (le jeton y est enregistré) ;
# - crée la tâche planifiée « BL-RMO Sonde Dante » : au démarrage du PC, même sans session
#   ouverte, sous le compte système, relancée en cas d'arrêt.
# La sonde ne fait que lire le réseau Dante et n'ouvre aucun port sur le PC.
# Le jeton est affiché dans BL-RMO, page Dante, onglet Configuration : il est demandé au lancement.

param([switch]$Desinstaller, [string]$Url = 'https://192.168.1.30')

$ErrorActionPreference = 'Stop'
$Netaudio = 'netaudio==0.6.1'
$Dest = Join-Path $env:ProgramData 'BL-RMO\sonde-dante'
$Task = 'BL-RMO Sonde Dante'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Lancer PowerShell en administrateur (clic droit, Exécuter en tant qu'administrateur)." -ForegroundColor Red
    exit 1
}

if ($Desinstaller) {
    if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $Task -Confirm:$false
    }
    if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
    Write-Host "Sonde Dante retirée (netaudio reste installé : python -m pip uninstall netaudio pour l'enlever)."
    exit 0
}

# 1. Python 3.10 ou plus récent, installé pour tous les utilisateurs (pas celui du Microsoft Store)
$py = $null
foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
    if ($c.Source -notmatch 'WindowsApps') { $py = $c.Source; break }
}
if (-not $py) {
    Write-Host "Python introuvable : installer Python 3.14 depuis python.org, pour tous les utilisateurs." -ForegroundColor Red
    exit 1
}
$ver = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.10') {
    Write-Host "Python $ver trop ancien ($py) : netaudio demande 3.10 ou plus récent." -ForegroundColor Red
    exit 1
}
if ($py -notmatch [regex]::Escape($env:ProgramFiles)) {
    Write-Host "Attention : $py n'est pas dans Program Files. La tâche tourne sous le compte système :" -ForegroundColor Yellow
    Write-Host "Python doit être installé pour tous les utilisateurs." -ForegroundColor Yellow
}
Write-Host "Python $ver : $py"

# 2. netaudio
Write-Host "Installation de $Netaudio..."
& $py -m pip install --disable-pip-version-check -q $Netaudio
if ($LASTEXITCODE -ne 0) { Write-Host "Echec de l'installation de netaudio." -ForegroundColor Red; exit 1 }

# 3. Sonde et réglages
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot 'sonde-dante.py') $Dest
$cfgFile = Join-Path $Dest 'sonde-dante.json'
$token = $null
if (Test-Path $cfgFile) {
    $keep = Read-Host "Réglages déjà présents. Garder le jeton actuel ? (O/n)"
    if ($keep -notmatch '^[nN]') { $token = (Get-Content $cfgFile -Raw | ConvertFrom-Json).token }
}
while (-not $token) {
    $sec = Read-Host "Jeton de la sonde (BL-RMO, page Dante, onglet Configuration)" -AsSecureString
    $token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    $token = $token.Trim()
}
$json = @{ url = $Url; token = $token } | ConvertTo-Json
[IO.File]::WriteAllText($cfgFile, $json, (New-Object Text.UTF8Encoding($false)))
$token = $null
# Réglages lisibles par le système et les administrateurs seulement
icacls $cfgFile /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
Write-Host "Sonde copiée dans $Dest."

# 4. Essai immédiat : un relevé envoyé à BL-RMO
Write-Host "Premier relevé (une vingtaine de secondes)..."
& $py (Join-Path $Dest 'sonde-dante.py') --once

# 5. Tâche planifiée au démarrage, sous le compte système, relancée en cas d'arrêt
$action = New-ScheduledTaskAction -Execute $py -Argument ('"' + (Join-Path $Dest 'sonde-dante.py') + '"') -WorkingDirectory $Dest
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Lit le réseau Dante (netaudio, lecture seule) et envoie l''état à BL-RMO.' -Force | Out-Null
Start-ScheduledTask -TaskName $Task
Write-Host "Tâche « $Task » créée et lancée : elle démarre avec le PC, même sans session ouverte." -ForegroundColor Green
Write-Host "Journal de la sonde : $Dest\sonde-dante.log"
