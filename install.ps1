# CAE Node one-line installer for Windows (PowerShell) — works on a fresh PC.
# Installs Python (via winget) if missing, installs dependencies, downloads the
# node, opens the local interface in the browser, and starts the node.
#
# Easiest (PowerShell):
#   iwr -useb https://raw.githubusercontent.com/omarperso/cae/main/install.ps1 | iex
#
# With a custom name:
#   & ([scriptblock]::Create((iwr -useb https://raw.githubusercontent.com/omarperso/cae/main/install.ps1))) "My Laptop"
param([string]$Name = $env:COMPUTERNAME)

$ErrorActionPreference = "Stop"
$Raw = "https://raw.githubusercontent.com/omarperso/cae/main"
$Dir = Join-Path $env:USERPROFILE ".cae"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null

Write-Host ">> CAE Node install for: $Name"

# --- 1. Make sure Python exists; install via winget if not -------------------
function Get-Py {
  $p = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $p) { $p = (Get-Command python3 -ErrorAction SilentlyContinue).Source }
  return $p
}
$py = Get-Py
if (-not $py) {
  Write-Host ">> Python not found — installing it with winget..."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
    $py = Get-Py
  }
  if (-not $py) {
    Write-Error "Could not install Python automatically. Install it from https://python.org and re-run."
    exit 1
  }
}

# --- 2. Dependencies ---------------------------------------------------------
Write-Host ">> Installing Python packages..."
& $py -m pip install --quiet --user --upgrade pip 2>$null
& $py -m pip install --quiet --user zeroconf mss pyautogui requests

# --- 3. Download the node ----------------------------------------------------
Write-Host ">> Downloading node..."
Invoke-WebRequest -UseBasicParsing "$Raw/node/cae_node.py" -OutFile (Join-Path $Dir "cae_node.py")

# --- 4. Open the local interface, then run -----------------------------------
$Url = "http://localhost:8765/"
Start-Job { Start-Sleep 2; Start-Process $using:Url } | Out-Null
Write-Host ">> Starting CAE Node. Interface: $Url"
Write-Host "   (Stop: Ctrl+C.  New pairing code later: press Enter in this window.)"
& $py (Join-Path $Dir "cae_node.py") --name $Name --pair
