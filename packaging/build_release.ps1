$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Fail([string]$Message) { Write-Host "ERREUR: $Message" -ForegroundColor Red; exit 1 }

if (-not [Environment]::Is64BitOperatingSystem) { Fail 'Windows 64 bits est requis.' }
if ($env:OS -ne 'Windows_NT') { Fail 'Ce build doit être exécuté sous Windows.' }

Step 'Recherche de Python x64 compatible (3.11 à 3.13)'
$Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $Launcher) { Fail 'Python Launcher introuvable. Installez Python 3.13 x64 pour construire la release.' }

$BuildTag = $null
foreach ($Candidate in @('3.13','3.12','3.11')) {
  & py "-$Candidate" -c "import struct; assert struct.calcsize('P')==8" *> $null
  if ($LASTEXITCODE -eq 0) { $BuildTag = $Candidate; break }
}
if (-not $BuildTag) { Fail 'Python x64 3.11, 3.12 ou 3.13 est requis uniquement pour construire la release.' }
Write-Host "Python $BuildTag x64 sélectionné." -ForegroundColor Green

Step 'Création de l’environnement de build'
$RecreateVenv = $false
if (Test-Path '.venv\Scripts\python.exe') {
  $Existing = & '.venv\Scripts\python.exe' -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
  if ($Existing -ne $BuildTag) { $RecreateVenv = $true }
}
if ($RecreateVenv) { Remove-Item -Recurse -Force '.venv' }
if (-not (Test-Path '.venv\Scripts\python.exe')) { & py "-$BuildTag" -m venv .venv }
$Python = Join-Path $Root '.venv\Scripts\python.exe'
& $Python -m pip install --upgrade pip wheel
& $Python -m pip install -r requirements-dev.txt

Step 'Vérification des dépendances natives'
& $Python -c "import PyQt6, pytsk3, reportlab, PIL, pypdfium2; print('Dépendances OK')"

Step 'Vérification de la syntaxe Python'
& $Python -m compileall -q main.py src tests
if ($LASTEXITCODE -ne 0) { Fail 'La vérification de syntaxe Python a échoué. Build annulé.' }

Step 'Tests automatisés'
& $Python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { Fail 'La suite de tests a échoué. Build annulé.' }

Step 'Nettoyage des anciens artefacts'
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Step 'Construction de l’application autonome'
& $Python -m PyInstaller FResucitary.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { Fail 'PyInstaller a échoué.' }
$Exe = Join-Path $Root 'dist\FResucitary\FResucitary.exe'
if (-not (Test-Path $Exe)) { Fail 'FResucitary.exe n’a pas été généré.' }

$Version = (Get-Item $Exe).VersionInfo.ProductVersion
Write-Host "EXE autonome créé: $Exe (version $Version)" -ForegroundColor Green

Step 'Auto-test du binaire autonome'
$Smoke = Start-Process -FilePath $Exe -ArgumentList '--self-test' -PassThru -Wait
if ($Smoke.ExitCode -ne 0) { Fail "L’EXE autonome échoue son auto-test (code $($Smoke.ExitCode))." }
Write-Host 'Auto-test EXE: OK' -ForegroundColor Green

Step 'Recherche de NSIS pour créer l’installateur'
$NsisCandidates = @( @(
  "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
  "${env:ProgramFiles}\NSIS\makensis.exe"
) | Where-Object { $_ -and (Test-Path $_) } )

if ($NsisCandidates.Count -eq 0) {
  $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
  if ($Winget) {
    Write-Host 'NSIS absent. Installation automatique via winget…' -ForegroundColor Yellow
    & winget install --id NSIS.NSIS -e --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    $NsisCandidates = @( @(
      "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
      "${env:ProgramFiles}\NSIS\makensis.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } )
  }
}
if ($NsisCandidates.Count -eq 0) {
  Write-Host 'NSIS n’a pas pu être installé. Le dossier autonome reste disponible dans dist\FResucitary.' -ForegroundColor Yellow
  exit 0
}

$MakeNsis = $NsisCandidates[0]
Step 'Construction de l’installateur Windows'
Push-Location (Join-Path $Root 'packaging')
try {
  & $MakeNsis 'installer.nsi'
  if ($LASTEXITCODE -ne 0) { Fail 'NSIS a échoué.' }
} finally {
  Pop-Location
}

$Installer = Get-ChildItem (Join-Path $Root 'dist') -Filter 'FResucitary-Pro-Setup-*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $Installer) { Fail 'Installateur non généré.' }
Write-Host "INSTALLATEUR PRÊT: $($Installer.FullName)" -ForegroundColor Green
Write-Host 'Le PC du client n’a besoin ni de Python ni de pip.' -ForegroundColor Green
