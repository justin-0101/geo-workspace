param(
    [Parameter(Mandatory = $true)]
    [string]$RunDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$resolvedRunDir = [System.IO.Path]::GetFullPath($RunDir)
$errors = New-Object System.Collections.Generic.List[string]

function Add-ValidationError {
    param([string]$Message)
    $errors.Add($Message)
}

if (-not (Test-Path -LiteralPath $resolvedRunDir -PathType Container)) {
    throw "RUN_NOT_FOUND: $resolvedRunDir"
}

$requiredFiles = @('manifest.json', 'frozen-config.json', 'tasks.jsonl', 'state.json', 'observations.jsonl', 'citations.jsonl')
foreach ($fileName in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedRunDir $fileName) -PathType Leaf)) {
        Add-ValidationError "缺少文件：$fileName"
    }
}

$tasks = @()
$observations = @()
if (Test-Path -LiteralPath (Join-Path $resolvedRunDir 'tasks.jsonl')) {
    $tasks = @(Get-Content -LiteralPath (Join-Path $resolvedRunDir 'tasks.jsonl') -Encoding UTF8 | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_ | ConvertFrom-Json })
}
if (Test-Path -LiteralPath (Join-Path $resolvedRunDir 'observations.jsonl')) {
    $observations = @(Get-Content -LiteralPath (Join-Path $resolvedRunDir 'observations.jsonl') -Encoding UTF8 | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_ | ConvertFrom-Json })
}

# 期望任务数由 frozen-config.json 推导（平台/问题数量由配置决定）
$expectedTasks = -1
$frozenPath = Join-Path $resolvedRunDir 'frozen-config.json'
if (Test-Path -LiteralPath $frozenPath -PathType Leaf) {
    try {
        $frozen = Get-Content -LiteralPath $frozenPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $expectedTasks = @($frozen.scope.questions).Count * @($frozen.platforms).Count * [int]$frozen.scope.repetitions
    } catch {
        Add-ValidationError "frozen-config.json 解析失败：$($_.Exception.Message)"
    }
}

if ($tasks.Count -ne $expectedTasks) {
    Add-ValidationError "任务数量应为 $expectedTasks，当前为 $($tasks.Count)。"
}

$taskIds = @($tasks | ForEach-Object { [string]$_.task_id })
$observationIds = @($observations | ForEach-Object { [string]$_.task_id })
if (@($taskIds | Select-Object -Unique).Count -ne $taskIds.Count) {
    Add-ValidationError 'tasks.jsonl 中存在重复 task_id。'
}
if (@($observationIds | Select-Object -Unique).Count -ne $observationIds.Count) {
    Add-ValidationError 'observations.jsonl 中存在重复 task_id。'
}

$allowedStatuses = @('pending', 'running', 'success', 'failed', 'manual_required')
foreach ($task in $tasks) {
    if ($allowedStatuses -notcontains [string]$task.status) {
        Add-ValidationError "任务 $($task.task_id) 状态无效：$($task.status)。"
    }
}

$nonTerminal = @($tasks | Where-Object { $_.status -notin @('success', 'failed') })
if ($nonTerminal.Count -gt 0) {
    Add-ValidationError "仍有 $($nonTerminal.Count) 个非终态任务。"
}
if ($observations.Count -ne $tasks.Count) {
    Add-ValidationError "observation 数量应与任务一致；当前 observation=$($observations.Count)，task=$($tasks.Count)。"
}

foreach ($observation in $observations) {
    $taskId = [string]$observation.task_id
    if ($taskIds -notcontains $taskId) {
        Add-ValidationError "存在未知 observation：$taskId。"
        continue
    }

    $task = @($tasks | Where-Object { $_.task_id -eq $taskId })[0]
    if ([string]$observation.status -ne [string]$task.status) {
        Add-ValidationError "任务 $taskId 的 task 与 observation 状态不一致。"
    }

    $evidenceFiles = @($observation.evidence_files)
    if ($evidenceFiles.Count -eq 0) {
        Add-ValidationError "任务 $taskId 没有证据文件。"
    }
    foreach ($evidenceFile in $evidenceFiles) {
        $candidate = if ([System.IO.Path]::IsPathRooted([string]$evidenceFile)) {
            [string]$evidenceFile
        } else {
            Join-Path $resolvedRunDir ([string]$evidenceFile)
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            Add-ValidationError "任务 $taskId 的证据文件不存在：$evidenceFile。"
        }
    }

    if ($observation.status -eq 'success' -and [string]::IsNullOrWhiteSpace([string]$observation.response_text)) {
        Add-ValidationError "成功任务 $taskId 缺少完整回答。"
    }
    if ($observation.status -eq 'failed' -and [string]::IsNullOrWhiteSpace([string]$observation.failure_reason)) {
        Add-ValidationError "失败任务 $taskId 缺少失败原因。"
    }
}

$reportFiles = @('report\diagnosis.md', 'report\metrics.json', 'report\manual-review.md', 'report\optimization-plan.md', 'report\QUALITY_REPORT.md')
foreach ($relativePath in $reportFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedRunDir $relativePath) -PathType Leaf)) {
        Add-ValidationError "缺少报告文件：$relativePath"
    }
}

$successCount = @($tasks | Where-Object { $_.status -eq 'success' }).Count
$failedCount = @($tasks | Where-Object { $_.status -eq 'failed' }).Count
$result = [ordered]@{
    ok = ($errors.Count -eq 0)
    run_dir = $resolvedRunDir
    task_count = $tasks.Count
    observation_count = $observations.Count
    success = $successCount
    failed = $failedCount
    errors = @($errors)
}

$result | ConvertTo-Json -Depth 8
if ($errors.Count -gt 0) {
    exit 1
}
