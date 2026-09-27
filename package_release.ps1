[CmdletBinding()]
param(
    [string]$Output = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Output) { $Output = Join-Path $root 'dist\geo-workspace' }
$outputRoot = [System.IO.Path]::GetFullPath($Output)

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required to build a release package from the current worktree.'
}

$requiredExtras = @(
    'DEPLOY.md', 'requirements.txt', 'install.ps1', 'package_release.ps1',
    'browser_paths.py', 'runtime_config.py', 'process_identity.py',
    'check_install.py', 'stop.py', 'cover_gen.py',
    'test_deployment.py', 'test_frontend_server.py', 'test_start_stop.py',
    'test_cover_gen.py'
)
$tracked = @(git -C $root ls-files)
if ($LASTEXITCODE -ne 0) { throw 'Could not enumerate tracked files.' }
$files = @($tracked + $requiredExtras | Sort-Object -Unique)
$excludedPrefixes = @('data/', 'backups/', 'test-results/', '.venv/', 'venv/', '.pi/', '__pycache__/')

if (Test-Path $outputRoot) { Remove-Item $outputRoot -Recurse -Force }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$copied = [System.Collections.Generic.List[string]]::new()
foreach ($relative in $files) {
    $normalized = ($relative -replace '\\', '/')
    if ($excludedPrefixes | Where-Object { $normalized.StartsWith($_) }) { continue }
    $source = Join-Path $root $relative
    if (-not (Test-Path $source -PathType Leaf)) {
        throw "Required release file is missing: $relative"
    }
    $destination = Join-Path $outputRoot $relative
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item $source $destination -Force
    $copied.Add($normalized)
}

$manifest = Join-Path $outputRoot 'RELEASE-MANIFEST.txt'
$copied | Sort-Object | Set-Content -Path $manifest -Encoding UTF8
Write-Host "Release package created: $outputRoot"
Write-Host "Files copied: $($copied.Count)"
Write-Host "Run from the package root: .\install.ps1"
