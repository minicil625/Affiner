<#
.SYNOPSIS
    Task 6.3 — Builds the standalone PyInstaller one-folder distribution (`dist\TextPromptAI\`).
#>
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "[1/2] Ensuring PyInstaller is installed in the uv environment..." -ForegroundColor Cyan
uv pip install pyinstaller

Write-Host "[2/2] Building one-folder package via textprompt.spec..." -ForegroundColor Cyan
uv run pyinstaller textprompt.spec --noconfirm --clean

Write-Host "`n[✓] Build complete: $(Join-Path $ProjectRoot 'dist\TextPromptAI\TextPromptAI.exe')" -ForegroundColor Green