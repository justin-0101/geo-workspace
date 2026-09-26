param(
    [Parameter(Mandatory = $true)]
    [string]$CompanyDir,
    [string]$TemplatePath = (Join-Path $PSScriptRoot '..\templates\run-config.template.json'),
    [switch]$OverwriteConfig
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$resolvedCompanyDir = [System.IO.Path]::GetFullPath($CompanyDir)
$configDir = Join-Path $resolvedCompanyDir 'config'
$inputDir = Join-Path $resolvedCompanyDir 'input'
$runsDir = Join-Path $resolvedCompanyDir 'runs'
$reportDir = Join-Path $resolvedCompanyDir 'report'

foreach ($dir in @($resolvedCompanyDir, $configDir, $inputDir, $runsDir, $reportDir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

$configPath = Join-Path $configDir 'run-config.json'
if ((Test-Path -LiteralPath $configPath -PathType Leaf) -and -not $OverwriteConfig) {
    throw "CONFIG_EXISTS: $configPath。若确认覆盖，重新运行并加 -OverwriteConfig。"
}
Copy-Item -LiteralPath ([System.IO.Path]::GetFullPath($TemplatePath)) -Destination $configPath -Force

[ordered]@{
    ok = $true
    company_dir = $resolvedCompanyDir
    config_path = $configPath
    runs_root = $runsDir
    next_step = '填写 config/run-config.json，并由人工确认 8 道问题后运行 initialize-run.ps1。'
} | ConvertTo-Json -Depth 8
