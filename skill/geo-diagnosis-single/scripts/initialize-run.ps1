param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\run-config.json'),
    [string]$RunsRoot = (Join-Path $PSScriptRoot '..\runs')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-WithError {
    param([string]$Message)
    throw "CONFIG_INVALID: $Message"
}

$resolvedConfig = [System.IO.Path]::GetFullPath($ConfigPath)
if (-not (Test-Path -LiteralPath $resolvedConfig -PathType Leaf)) {
    Stop-WithError "找不到配置文件：$resolvedConfig。请先从 run-config.example.json 复制并填写。"
}

$rawConfig = Get-Content -LiteralPath $resolvedConfig -Raw -Encoding UTF8
if ([string]::IsNullOrWhiteSpace($rawConfig)) {
    Stop-WithError '配置文件为空。'
}
if ($rawConfig -match '待填写|TODO|CHANGE_ME') {
    Stop-WithError '配置中仍有待填写项。正式问题提交前必须全部补齐。'
}

try {
    $config = $rawConfig | ConvertFrom-Json
} catch {
    Stop-WithError "JSON 解析失败：$($_.Exception.Message)"
}

$questions = @($config.scope.questions)
$platforms = @($config.platforms)
$repetitions = [int]$config.scope.repetitions
$expectedQuestions = [int]$config.scope.expected_questions
$expectedPlatforms = [int]$config.scope.expected_platforms
$expectedTasks = [int]$config.scope.expected_tasks

if ($questions.Count -ne $expectedQuestions) {
    Stop-WithError "问题数量应为 $expectedQuestions，当前为 $($questions.Count)。"
}
if ($platforms.Count -ne $expectedPlatforms) {
    Stop-WithError "平台数量应为 $expectedPlatforms，当前为 $($platforms.Count)。"
}
if ($repetitions -ne 1) {
    Stop-WithError "重复次数必须为 1，当前为 $repetitions。"
}
$computedTasks = $questions.Count * $platforms.Count * $repetitions
if ($computedTasks -ne $expectedTasks) {
    Stop-WithError "任务矩阵必须等于 scope.expected_tasks（计算值 $computedTasks，配置值 $expectedTasks）。"
}

$questionIds = @($questions | ForEach-Object { [string]$_.id })
$platformIds = @($platforms | ForEach-Object { [string]$_.id })
if (($questionIds | Select-Object -Unique).Count -ne $questions.Count) {
    Stop-WithError '问题 ID 存在重复。'
}
if (($platformIds | Select-Object -Unique).Count -ne $platforms.Count) {
    Stop-WithError '平台 ID 存在重复。'
}

$allowedQuestionTypes = @('non_brand', 'brand_cognition', 'comparison', 'concept')
$allowedBusinessValues = @('high', 'medium', 'low')
$allowedLayers = @('', 'edge-real-world', 'query-intent', 'industry', 'prompt-style', 'time-sensitivity', 'trigger-intensity', 'uncategorized')
$allowedTimeSensitivity = @('', 'low', 'medium', 'high')
$allowedTriggerIntensity = @('', 'low', 'medium', 'high')
foreach ($question in $questions) {
    if ([string]::IsNullOrWhiteSpace([string]$question.prompt)) {
        Stop-WithError "问题 $($question.id) 的原文为空。"
    }
    if ($allowedQuestionTypes -notcontains [string]$question.type) {
        Stop-WithError "问题 $($question.id) 的类型无效：$($question.type)。"
    }
    if ($allowedBusinessValues -notcontains [string]$question.business_value) {
        Stop-WithError "问题 $($question.id) 的业务价值无效：$($question.business_value)。"
    }
    $layerValue = if ($question.PSObject.Properties.Name -contains 'layer') { [string]$question.layer } else { '' }
    $timeValue = if ($question.PSObject.Properties.Name -contains 'time_sensitivity') { [string]$question.time_sensitivity } else { '' }
    $triggerValue = if ($question.PSObject.Properties.Name -contains 'trigger_intensity') { [string]$question.trigger_intensity } else { '' }
    if ($allowedLayers -notcontains $layerValue) {
        Stop-WithError "问题 $($question.id) 的 layer 无效：$layerValue。"
    }
    if ($allowedTimeSensitivity -notcontains $timeValue) {
        Stop-WithError "问题 $($question.id) 的 time_sensitivity 无效：$timeValue。"
    }
    if ($allowedTriggerIntensity -notcontains $triggerValue) {
        Stop-WithError "问题 $($question.id) 的 trigger_intensity 无效：$triggerValue。"
    }
}

# 平台集合完全由配置决定；如配置声明 required_platform_ids 则额外强制
$requiredIdsFromConfig = @()
if ($config.PSObject.Properties.Name -contains 'required_platform_ids') {
    $requiredIdsFromConfig = @($config.required_platform_ids | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}
if ($requiredIdsFromConfig.Count -gt 0) {
    foreach ($requiredId in $requiredIdsFromConfig) {
        if ($platformIds -notcontains $requiredId) {
            Stop-WithError "缺少必需平台：$requiredId。"
        }
    }
}
foreach ($platform in $platforms) {
    $urlValue = [string]$platform.entry_url
    $parsedUri = $null
    if (-not [System.Uri]::TryCreate($urlValue, [System.UriKind]::Absolute, [ref]$parsedUri)) {
        Stop-WithError "平台 $($platform.label) 的入口不是有效绝对 URL。"
    }
    if ($parsedUri.Scheme -ne 'https') {
        Stop-WithError "平台 $($platform.label) 的入口必须使用 HTTPS。"
    }
    if ([string]::IsNullOrWhiteSpace([string]$platform.required_mode)) {
        Stop-WithError "平台 $($platform.label) 缺少目标模式。"
    }
}

$brandName = [string]$config.brand.canonical_name
$aliases = @($config.brand.aliases | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
$officialDomains = @($config.brand.official_domains | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
$officialPages = @($config.brand.official_pages | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
$noOfficialWebPresence = $false
if ($config.brand.PSObject.Properties.Name -contains 'no_official_web_presence') {
    $noOfficialWebPresence = [System.Convert]::ToBoolean($config.brand.no_official_web_presence)
}
if ([string]::IsNullOrWhiteSpace($brandName)) {
    Stop-WithError '品牌标准名称为空。'
}
if ($officialDomains.Count -eq 0 -and $officialPages.Count -eq 0 -and -not $noOfficialWebPresence) {
    Stop-WithError '未填写官方域名/官方页面。若客户确无官网、公众号或官方页面，请在 brand.no_official_web_presence 显式设为 true。'
}
if ($officialDomains.Count -eq 0 -and $noOfficialWebPresence) {
    Write-Warning '配置声明无官方网页/公众号。source_citation 将无法命中官方域名，报告必须单独说明此限制。'
}

$forbiddenBrandTerms = @($brandName) + $aliases
foreach ($question in @($questions | Where-Object { $_.type -eq 'non_brand' })) {
    foreach ($term in $forbiddenBrandTerms) {
        if (-not [string]::IsNullOrWhiteSpace($term) -and ([string]$question.prompt).Contains($term)) {
            Stop-WithError "非品牌题 $($question.id) 含品牌词：$term。"
        }
    }
}

$resolvedRunsRoot = [System.IO.Path]::GetFullPath($RunsRoot)
if (Test-Path -LiteralPath $resolvedRunsRoot -PathType Container) {
    $activeRuns = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $resolvedRunsRoot -Directory | ForEach-Object {
        $statePath = Join-Path $_.FullName 'state.json'
        if (Test-Path -LiteralPath $statePath -PathType Leaf) {
            try {
                $existingState = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
                $pendingCount = [int]$existingState.pending
                $runningCount = [int]$existingState.running
                $manualCount = [int]$existingState.manual_required
                if (($pendingCount + $runningCount + $manualCount) -gt 0) {
                    $activeRuns.Add($_.FullName)
                }
            } catch {
                $activeRuns.Add($_.FullName + '（state.json 无法解析）')
            }
        }
    }
    if ($activeRuns.Count -gt 0) {
        Stop-WithError "当前工作区仍有未完成或需人工处理的 run。稳定单批版禁止在同一工作区并发：$($activeRuns -join '; ')"
    }
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $resolvedRunsRoot $runId
if (Test-Path -LiteralPath $runDir) {
    Stop-WithError "运行目录已存在：$runDir。"
}

$directories = @(
    $runDir,
    (Join-Path $runDir 'raw'),
    (Join-Path $runDir 'evidence'),
    (Join-Path $runDir 'logs'),
    (Join-Path $runDir 'report')
)
foreach ($directory in $directories) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$frozenConfigPath = Join-Path $runDir 'frozen-config.json'
Copy-Item -LiteralPath $resolvedConfig -Destination $frozenConfigPath
$configHash = (Get-FileHash -LiteralPath $frozenConfigPath -Algorithm SHA256).Hash.ToLowerInvariant()

$createdAt = (Get-Date).ToUniversalTime().ToString('o')
$manifest = [ordered]@{
    schema_version = '1.0'
    run_id = $runId
    created_at = $createdAt
    config_sha256 = $configHash
    expected_questions = $expectedQuestions
    expected_platforms = $expectedPlatforms
    expected_tasks = $expectedTasks
    repetitions = $repetitions
    execution_order = 'question_major'
    status = 'initialized'
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runDir 'manifest.json') -Encoding UTF8

$taskRows = New-Object System.Collections.Generic.List[object]
foreach ($question in $questions) {
    foreach ($platform in $platforms) {
        $taskRows.Add([ordered]@{
            task_id = ('{0}_{1}_01' -f $question.id, $platform.id)
            question_id = [string]$question.id
            question_type = [string]$question.type
            business_value = [string]$question.business_value
            layer = if ($question.PSObject.Properties.Name -contains 'layer') { [string]$question.layer } else { '' }
            subcat = if ($question.PSObject.Properties.Name -contains 'subcat') { [string]$question.subcat } else { '' }
            intent = if ($question.PSObject.Properties.Name -contains 'intent') { [string]$question.intent } else { '' }
            time_sensitivity = if ($question.PSObject.Properties.Name -contains 'time_sensitivity') { [string]$question.time_sensitivity } else { '' }
            trigger_intensity = if ($question.PSObject.Properties.Name -contains 'trigger_intensity') { [string]$question.trigger_intensity } else { '' }
            platform_id = [string]$platform.id
            platform_label = [string]$platform.label
            repetition = 1
            status = 'pending'
            started_at = $null
            completed_at = $null
            observation_ref = $null
            failure_reason = $null
        })
    }
}

$taskPath = Join-Path $runDir 'tasks.jsonl'
$taskLines = @($taskRows | ForEach-Object { $_ | ConvertTo-Json -Depth 8 -Compress })
$taskLines | Set-Content -LiteralPath $taskPath -Encoding UTF8
# 引用来源明细：即使本批次没有抽取到来源，也保留空 JSONL 文件，便于校验和归档。
Set-Content -LiteralPath (Join-Path $runDir 'citations.jsonl') -Value $null -Encoding UTF8

$state = [ordered]@{
    run_id = $runId
    updated_at = $createdAt
    total = $taskRows.Count
    pending = $taskRows.Count
    running = 0
    success = 0
    failed = 0
    manual_required = 0
    next_task_id = $taskRows[0].task_id
}
$state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runDir 'state.json') -Encoding UTF8

[ordered]@{
    ok = $true
    run_id = $runId
    run_dir = $runDir
    task_count = $taskRows.Count
    config_sha256 = $configHash
    next_step = '执行浏览器能力与各平台登录状态预检；通过后才可提交正式问题。'
} | ConvertTo-Json -Depth 8
