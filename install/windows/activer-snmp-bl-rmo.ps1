# Active SNMP en lecture seule sur un PC Windows 10 ou 11, pour la page Parc de BL-RMO.
# A lancer dans PowerShell ouvert en administrateur :
#   powershell -ExecutionPolicy Bypass -File .\activer-snmp-bl-rmo.ps1
#
# - installe le service SNMP (fonctionnalité facultative de Windows) ;
# - n'accepte les requêtes que de BL-RMO (192.168.1.30), en lecture seule ;
# - ouvre le port UDP 161 du pare-feu pour BL-RMO seulement.
# La communauté est demandée au lancement : elle n'est ni affichée ni enregistrée ailleurs que dans Windows.

$ErrorActionPreference = 'Stop'
$BlRmo = '192.168.1.30'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Lancer PowerShell en administrateur (clic droit, Exécuter en tant qu'administrateur)." -ForegroundColor Red
    exit 1
}

# 1. Service SNMP
$cap = Get-WindowsCapability -Online -Name 'SNMP.Client*' | Select-Object -First 1
if ($cap.State -ne 'Installed') {
    Write-Host "Installation du service SNMP (quelques minutes, connexion internet nécessaire)..."
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
}
Write-Host "Service SNMP installé."

# 2. Communauté en lecture seule et hôte autorisé
function Read-Secret($prompt) {
    $sec = Read-Host $prompt -AsSecureString
    [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
# Saisie masquée : tapée au clavier (le collage par clic droit peut ne pas passer), demandée deux fois
while ($true) {
    $community = Read-Secret "Communauté SNMP (tapez-la au clavier)"
    $again = Read-Secret "Retapez la communauté"
    if (-not $community) { Write-Host "Communauté vide, recommencez." -ForegroundColor Yellow; continue }
    if ($community -cne $again) { Write-Host "Les deux saisies diffèrent, recommencez." -ForegroundColor Yellow; continue }
    if ($community.Length -lt 4) { Write-Host "Seulement $($community.Length) caractère(s) saisi(s) : recommencez." -ForegroundColor Yellow; continue }
    break
}
$again = $null
Write-Host "Communauté enregistrée ($($community.Length) caractères)."

$base = 'HKLM:\SYSTEM\CurrentControlSet\Services\SNMP\Parameters'
New-Item -Path "$base\ValidCommunities" -Force | Out-Null
New-Item -Path "$base\PermittedManagers" -Force | Out-Null
# Une seule communauté, en lecture seule (4 = READ ONLY)
Get-Item "$base\ValidCommunities" | Select-Object -ExpandProperty Property | ForEach-Object {
    Remove-ItemProperty -Path "$base\ValidCommunities" -Name $_ }
New-ItemProperty -Path "$base\ValidCommunities" -Name $community -Value 4 -PropertyType DWord -Force | Out-Null
# Seul BL-RMO peut interroger le PC
Get-Item "$base\PermittedManagers" | Select-Object -ExpandProperty Property | ForEach-Object {
    Remove-ItemProperty -Path "$base\PermittedManagers" -Name $_ }
New-ItemProperty -Path "$base\PermittedManagers" -Name '1' -Value $BlRmo -PropertyType String -Force | Out-Null
$community = $null

# 3. Service démarré automatiquement
Set-Service -Name SNMP -StartupType Automatic
Restart-Service -Name SNMP
Write-Host "Service SNMP démarré, réservé à $BlRmo, en lecture seule."

# 4. Pare-feu : UDP 161 pour BL-RMO seulement, quel que soit le profil réseau
Get-NetFirewallRule -DisplayName 'SNMP BL-RMO' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'SNMP BL-RMO' -Direction Inbound -Protocol UDP -LocalPort 161 `
    -RemoteAddress $BlRmo -Action Allow -Profile Any | Out-Null
Write-Host "Pare-feu : port UDP 161 ouvert pour $BlRmo."

Write-Host ""
Write-Host "Terminé. Nom du PC : $env:COMPUTERNAME" -ForegroundColor Green
