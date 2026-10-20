<#
.SYNOPSIS
    Section 4.1 — Copies a pinned snapshot of `promptcore` into an app's `vendor/promptcore`
    folder when `TextPrompt` or `VoicePrompt` needs to be delivered as a standalone repository.
.PARAMETER TargetAppDir
    Path to the target application directory (defaults to `../../Text-improvement`).
#>
param(
    [string]$TargetAppDir = (Join-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)) "Text-improvement")
)

$ErrorActionPreference = "Stop"

$PromptCoreRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$VendorDest = Join-Path $TargetAppDir "vendor\promptcore"

Write-Host "[vendor.ps1] Copying promptcore from $PromptCoreRoot to $VendorDest ..." -ForegroundColor Cyan

if (Test-Path $VendorDest) {
    Remove-Item -Path $VendorDest -Recurse -Force
}
New-Item -ItemType Directory -Path $VendorDest -Force | Out-Null

Copy-Item -Path (Join-Path $PromptCoreRoot "pyproject.toml") -Destination $VendorDest
Copy-Item -Path (Join-Path $PromptCoreRoot "README.md") -Destination $VendorDest -ErrorAction SilentlyContinue
Copy-Item -Path (Join-Path $PromptCoreRoot "promptcore") -Destination (Join-Path $VendorDest "promptcore") -Recurse

Write-Host "[✓] Vendored promptcore into $VendorDest" -ForegroundColor Green
Write-Host "    To switch pyproject.toml to the vendored copy, update [tool.uv.sources]:" -ForegroundColor Gray
Write-Host '    promptcore = { path = "vendor/promptcore", editable = true }' -ForegroundColor Gray