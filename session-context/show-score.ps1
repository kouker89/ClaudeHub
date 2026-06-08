$ErrorActionPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectDir = $PSScriptRoot
$projectRoot = Split-Path $projectDir -Parent
$scoreFile = "$projectDir\proposal-score.json"
$taskFile = "$projectDir\active-task.json"
$balanceFile = "$projectDir\daily-balance.json"
$bridgeConfig = "$projectDir\qq-bridge\config.json"

# ===== Score =====
$dailyScore = 0; $todayTasks = 0; $todayMistakes = 0
if (Test-Path $scoreFile) {
    $sc = Get-Content $scoreFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $today = (Get-Date).ToString("yyyy-MM-dd")
    if ($sc.date -eq $today) {
        $dailyScore = $sc.daily_score
        $todayTasks = $sc.today_tasks
        $todayMistakes = $sc.today_mistakes
    }
}
$scoreColor = "37"
if ($dailyScore -gt 0) { $scoreColor = "32" }
elseif ($dailyScore -lt 0) { $scoreColor = "31" }
$esc = [char]27

# ===== Task Progress =====
$taskStr = "$esc[90midle$esc[0m"
$taskPct = $null
# 1) Explicit task from active-task.json (within 5 min)
if (Test-Path $taskFile) {
    $t = Get-Content $taskFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $cur = $t.current
    if ($cur -and $cur.name) {
        $taskAge = 999
        try { $taskAge = [int](New-TimeSpan -Start $t.last_updated).TotalSeconds } catch {}
        if ($taskAge -lt 300) {
            $name = $cur.name
            $done = $cur.steps_done; $total = $cur.steps_total
            if ($total -gt 0) {
                $pct = [Math]::Round(($done / $total) * 100)
                $blocks = [Math]::Floor($pct / 20)
                $bar = ("#" * $blocks) + ("-" * (5 - $blocks))
                $taskStr = "$esc[36m|${bar}| $pct% ($done/$total) $name$esc[0m"
                $taskPct = $pct
            } else {
                $taskStr = "$esc[36m~ $name$esc[0m"
            }
        }
    }
}
# 2) Fallback: auto-detect activity from recent git commits (5 min)
if ($taskStr -eq "$esc[90midle$esc[0m") {
    try {
        $lastCommit = & git -C $projectRoot log -1 --format="%at" 2>$null
        if ($lastCommit) {
            $now = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            $commitAge = $now - [int]$lastCommit
            if ($commitAge -lt 300) {
                $commitMsg = & git -C $projectRoot log -1 --format="%s" 2>$null
                if ($commitMsg) {
                    # Strip prefix like "新增：" or "修复：", max 30 chars
                    $short = $commitMsg -replace '^[^：:]*[：:]\s*', ''
                    if ($short.Length -gt 30) { $short = $short.Substring(0, 30) }
                    $taskStr = "$esc[36m~ $short$esc[0m"
                } else {
                    $taskStr = "$esc[36m~ 工作中$esc[0m"
                }
            }
        }
    } catch {}
}

# ===== Token Balance (2min cache + refresh + fallback) =====
$today = (Get-Date).ToString("yyyy-MM-dd")
$todaySpent = $null; $lastBalance = $null
$fetchBalance = $true
if (Test-Path $balanceFile) {
    $bal = Get-Content $balanceFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($bal.date -eq $today) {
        $age = 999
        try { $age = [int](New-TimeSpan -Start $bal.last_updated).TotalSeconds } catch {}
        if ($age -lt 120) {
            $fetchBalance = $false
            $lastBalance = $bal.last_balance
            $startBalance = $bal.start_balance
            $todaySpent = $startBalance - $lastBalance
            if ($todaySpent -lt 0) { $todaySpent = 0 }
        }
    }
}
if ($fetchBalance -and (Test-Path $bridgeConfig)) {
    $cfg = Get-Content $bridgeConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    $bot = $cfg.bots[0]
    if ($bot.api_base_url -match "deepseek") {
        $encKey = $bot.api_key
        if ($encKey) {
            try {
                Add-Type -AssemblyName System.Security
                $encBytes = [Convert]::FromBase64String($encKey)
                $decBytes = [System.Security.Cryptography.ProtectedData]::Unprotect($encBytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
                $apiKey = [System.Text.Encoding]::UTF8.GetString($decBytes)
                if ($apiKey) {
                    $resp = Invoke-RestMethod -Uri "https://api.deepseek.com/user/balance" -Headers @{"Authorization"="Bearer $apiKey"} -TimeoutSec 5
                    if ($resp.is_available) {
                        $lastBalance = [double]($resp.balance_infos[0].total_balance)
                        $startBalance = $lastBalance
                        $todaySpent = 0
                        if (Test-Path $balanceFile) {
                            $old = Get-Content $balanceFile -Raw -Encoding UTF8 | ConvertFrom-Json
                            if ($old.date -eq $today) { $startBalance = $old.start_balance; $todaySpent = $startBalance - $lastBalance }
                        }
                        if ($todaySpent -lt 0) { $todaySpent = 0; $startBalance = $lastBalance }
                        @{ date=$today; start_balance=$startBalance; last_balance=$lastBalance; last_updated=(Get-Date -Format "yyyy-MM-ddTHH:mm:ss") } | ConvertTo-Json -Compress | Out-File -FilePath $balanceFile -Encoding utf8
                    }
                }
            } catch {}
        }
    }
}
# Fallback: if fetch failed, use stale cached data
if ($null -eq $todaySpent -and (Test-Path $balanceFile)) {
    $bal = Get-Content $balanceFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($bal.last_balance) {
        $lastBalance = $bal.last_balance
        $startBalance = $bal.start_balance
        $todaySpent = $startBalance - $lastBalance
        if ($todaySpent -lt 0) { $todaySpent = 0 }
    }
}
if ($null -ne $todaySpent) {
    $tokColor = "37"
    if ($todaySpent -ge 5.0) { $tokColor = "31" }
    elseif ($todaySpent -ge 1.0) { $tokColor = "33" }
    $tokPart = "$esc[${tokColor}m{0:F2}$esc[0m$esc[90m/{1:F2}$esc[0m" -f $todaySpent, $lastBalance
} else {
    $tokPart = "$esc[90m$ --$esc[0m"
}

# ===== Git commits today =====
$gitCount = 0
try {
    $gitCount = & git -C $projectRoot log --since="midnight" --oneline 2>$null | Measure-Object -Line | Select-Object -ExpandProperty Lines
} catch {}

# ===== Output =====
$scorePart = "$esc[${scoreColor}m[P] $dailyScore$esc[0m"
$mistakePart = if ($todayMistakes -gt 0) { "$esc[31m$todayMistakes err$esc[0m" } else { "0 err" }
$taskPart = if ($todayTasks -gt 0) { "$todayTasks done" } else { "" }
$gitPart = if ($gitCount -gt 0) { "git:$gitCount" } else { "git:0" }

Write-Host "$scorePart ($mistakePart) $taskPart | $taskStr | $tokPart | $gitPart"