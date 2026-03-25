param(
    [Parameter(Position = 0)]
    [string]$TargetPath = "."
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-ZoneIdentifier {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    try {
        $null = Get-Item -LiteralPath $LiteralPath -Stream Zone.Identifier -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

if ($env:OS -ne "Windows_NT") {
    Write-Error "该脚本只能在 Windows 上运行。"
    exit 1
}

try {
    $resolvedTarget = (Resolve-Path -LiteralPath $TargetPath).Path
} catch {
    Write-Error "目标路径不存在: $TargetPath"
    exit 1
}

$targetItem = Get-Item -LiteralPath $resolvedTarget

if ($targetItem.PSIsContainer) {
    $files = @(Get-ChildItem -LiteralPath $resolvedTarget -Recurse -Force -File -ErrorAction Stop)
} else {
    $files = @($targetItem)
}

if ($files.Count -eq 0) {
    Write-Host "未找到可处理的文件: $resolvedTarget"
    exit 0
}

$scanned = 0
$unblocked = 0
$failed = 0

foreach ($file in $files) {
    $scanned += 1

    if (-not (Test-ZoneIdentifier -LiteralPath $file.FullName)) {
        continue
    }

    try {
        $file | Unblock-File -ErrorAction Stop
        $unblocked += 1
        Write-Host ("[OK] 已解除锁定: {0}" -f $file.FullName)
    } catch {
        $failed += 1
        Write-Warning ("[FAIL] 解除锁定失败: {0}`n{1}" -f $file.FullName, $_.Exception.Message)
    }
}

Write-Host ""
Write-Host ("扫描文件数: {0}" -f $scanned)
Write-Host ("已解除锁定: {0}" -f $unblocked)
Write-Host ("失败数量: {0}" -f $failed)

if ($failed -gt 0) {
    exit 2
}

exit 0
