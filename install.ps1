[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipChrome,
    [string]$Venv = '.venv'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = Join-Path $Venv 'Scripts\python.exe'
if (-not $CheckOnly) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Python Launcher (py) not found. Install CPython 3.11 for all users, then retry.'
    }
    if (-not (Test-Path $python)) {
        & py -3.11 -m venv $Venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.11 virtual environment.' }
    }
    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (-not (Test-Path $python)) { throw "Virtual environment not found: $python" }
$arguments = @('check_install.py', '--json')
if ($SkipChrome) { $arguments += '--skip-chrome' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Install preflight failed; fix the reported checks before starting GEO.' }
Write-Host "Install preflight passed. Start with: $python start.py"
