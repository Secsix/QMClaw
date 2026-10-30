<#
  S21 照片读数工具一键运行脚本。

  用法:
    .\run.ps1 "D:\QMClaw\s21_image\读取线6上的待测比特.png" [-OutDir D:\out] [-Scale 2]
    .\run.ps1 D:\QMClaw\s21_image\photos -Summary D:\out\summary.csv
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Target,

    [string]$OutDir = "",
    [double]$Scale = 2.0,
    [string]$Summary = "",
    [switch]$NoAnnotate
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
try { chcp 65001 | Out-Null } catch { }

$toolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $toolDir)

# 找一个可用的 Python
$pyCandidates = @(
    "$repoRoot\.venv\Scripts\python.exe",
    "C:\Users\lqcs\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
    "python"
)
$py = $null
foreach ($c in $pyCandidates) {
    if ($c -eq "python") { $py = "python"; break }
    if (Test-Path $c) { $py = $c; break }
}
if (-not $py) { throw "找不到可用的 Python 解释器" }

$argsList = @("-m", "s21pr", $Target, "--ocr-scale", $Scale)
if ($OutDir) { $argsList += @("-o", $OutDir) }
if ($Summary) { $argsList += @("--summary", $Summary) }
if ($NoAnnotate) { $argsList += "--no-annotate" }

Push-Location $toolDir
try {
    & $py @argsList
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
