<#
  S21 photo reader - one-click runner.

  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 as ANSI
  unless the file starts with a UTF-8 BOM, so non-ASCII text breaks parsing.

  Usage:
    .\run.ps1 "D:\QMClaw\s21_image\photo.png" -OutDir "D:\out"
    .\run.ps1 "D:\QMClaw\s21_image\photos" -Summary "D:\out\summary.csv"
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

$toolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $toolDir)

# locate a usable python
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
if (-not $py) { throw "No usable Python interpreter found" }

$argList = @("-m", "s21pr", $Target, "--ocr-scale", $Scale)
if ($OutDir) { $argList += @("-o", $OutDir) }
if ($Summary) { $argList += @("--summary", $Summary) }
if ($NoAnnotate) { $argList += "--no-annotate" }

Push-Location $toolDir
try {
    & $py @argList
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
