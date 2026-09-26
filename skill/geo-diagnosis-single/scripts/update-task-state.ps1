param(
    [Parameter(Mandatory = $true)]
    [string]$RunDir,
    [Parameter(Mandatory = $true)]
    [string]$TaskId,
    [Parameter(Mandatory = $true)]
    [ValidateSet('running', 'success', 'failed', 'manual_required')]
    [string]$Status,
    [string]$ObservationRef = '',
    [string]$FailureReason = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$resolvedRunDir = [System.IO.Path]::GetFullPath($RunDir)
$taskPath = Join-Path $resolvedRunDir 'tasks.jsonl'
if (-not (Test-Path -LiteralPath $taskPath -PathType Leaf)) {
    throw "TASK_FILE_NOT_FOUND: $taskPath"
}

$tasks = @(Get-Content -LiteralPath $taskPath -Encoding UTF8 | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_ | ConvertFrom-Json })
$matches = @($tasks | Where-Object { $_.task_id -eq $TaskId })
if ($matches.Count -ne 1) {
    throw "TASK_ID_INVALID: $TaskId"
}

$task = $matches[0]
$currentStatus = [string]$task.status
if ($currentStatus -in @('success', 'failed')) {
    throw "TASK_ALREADY_TERMINAL: $TaskId is $currentStatus"
}
if ($Status -eq 'running' -and $currentStatus -ne 'pending') {
    throw "INVALID_TRANSITION: $currentStatus -> running"
}
if ($Status -in @('success', 'failed') -and $currentStatus -ne 'running') {
    throw "INVALID_TRANSITION: $currentStatus -> $Status"
}
if ($Status -eq 'success' -and [string]::IsNullOrWhiteSpace($ObservationRef)) {
    throw 'SUCCESS_REQUIRES_OBSERVATION_REF'
}
if ($Status -eq 'failed' -and ([string]::IsNullOrWhiteSpace($ObservationRef) -or [string]::IsNullOrWhiteSpace($FailureReason))) {
    throw 'FAILED_REQUIRES_OBSERVATION_REF_AND_REASON'
}

$now = (Get-Date).ToUniversalTime().ToString('o')
if ($Status -eq 'running') {
    $task.started_at = $now
}
if ($Status -in @('success', 'failed')) {
    $task.completed_at = $now
    $task.observation_ref = $ObservationRef
}
if ($Status -in @('failed', 'manual_required')) {
    $task.failure_reason = $FailureReason
}
$task.status = $Status

$tempTaskPath = Join-Path $resolvedRunDir 'tasks.jsonl.tmp'
@($tasks | ForEach-Object { $_ | ConvertTo-Json -Depth 8 -Compress }) | Set-Content -LiteralPath $tempTaskPath -Encoding UTF8
Move-Item -LiteralPath $tempTaskPath -Destination $taskPath -Force

$nextTaskId = $null
$pendingFirst = @($tasks | Where-Object { $_.status -eq 'pending' } | Select-Object -First 1)
if ($pendingFirst.Count -gt 0) { $nextTaskId = [string]$pendingFirst[0].task_id }
$counts = [ordered]@{
    run_id = (Split-Path -Leaf $resolvedRunDir)
    updated_at = $now
    total = $tasks.Count
    pending = @($tasks | Where-Object { $_.status -eq 'pending' }).Count
    running = @($tasks | Where-Object { $_.status -eq 'running' }).Count
    success = @($tasks | Where-Object { $_.status -eq 'success' }).Count
    failed = @($tasks | Where-Object { $_.status -eq 'failed' }).Count
    manual_required = @($tasks | Where-Object { $_.status -eq 'manual_required' }).Count
    next_task_id = $nextTaskId
}
$counts | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $resolvedRunDir 'state.json') -Encoding UTF8
$counts | ConvertTo-Json -Depth 8

