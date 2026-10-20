<#
.SYNOPSIS
    TextPrompt AI — Phase 0 Setup Script (uv sync + SymSpell dictionary + shared PromptCore API key wizard)
.DESCRIPTION
    1. Verifies `uv` is installed.
    2. Runs `uv sync --all-extras` to create the virtual environment and link `../promptcore`.
    3. Downloads the MIT-licensed SymSpell frequency dictionary (`frequency_dictionary_en_82_765.txt`) into `data/`.
    4. Runs an interactive wizard to save free-tier API keys into Windows Credential Manager under service `PromptCore`.
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  TextPrompt AI (Cloud Free-Tier Edition) — Setup Wizard  " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check for uv
if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Host "[!] 'uv' is not installed. Installing uv via official installer..." -ForegroundColor Yellow
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "`n[1/3] Syncing Python workspace & editable '../promptcore' package..." -ForegroundColor Green
uv sync --all-extras

# 2. Download SymSpell frequency dictionary if missing
$DataDir = Join-Path $ProjectRoot "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir | Out-Null
}

$DictPath = Join-Path $DataDir "frequency_dictionary_en_82_765.txt"
$DictUrl = "https://raw.githubusercontent.com/mammothb/symspellpy/master/symspellpy/frequency_dictionary_en_82_765.txt"

if (-not (Test-Path $DictPath)) {
    Write-Host "`n[2/3] Downloading SymSpell English frequency dictionary (MIT)..." -ForegroundColor Green
    Invoke-WebRequest -Uri $DictUrl -OutFile $DictPath -UseBasicParsing
    Write-Host "      Saved to $DictPath" -ForegroundColor Gray
} else {
    Write-Host "`n[2/3] SymSpell frequency dictionary already present." -ForegroundColor Green
}

# 3. Interactive Keyring Wizard (Service: PromptCore)
Write-Host "`n[3/3] Configuring Shared Free-Tier API Keys (Service: PromptCore)" -ForegroundColor Green
Write-Host "      Keys are stored in Windows Credential Manager and shared with VoicePrompt AI." -ForegroundColor Gray
Write-Host "      Press ENTER to keep an existing key or skip a provider.`n" -ForegroundColor Gray

$Providers = @(
    @{ Id = "groq";          Name = "Groq (Primary Fast/Quality)"; Url = "https://console.groq.com/keys" },
    @{ Id = "cerebras";      Name = "Cerebras (Fast Fallback)";    Url = "https://cloud.cerebras.ai/" },
    @{ Id = "gemini";        Name = "Google AI Studio (No-Billing Project)"; Url = "https://aistudio.google.com/apikey" },
    @{ Id = "openrouter";    Name = "OpenRouter (:free models)";   Url = "https://openrouter.ai/keys" },
    @{ Id = "github_models"; Name = "GitHub Models (Optional)";    Url = "https://github.com/settings/tokens" },
    @{ Id = "mistral";       Name = "Mistral Experiment (Optional)"; Url = "https://console.mistral.ai/api-keys/" }
)

foreach ($p in $Providers) {
    $id = $p.Id
    $existing = uv run python -c "from promptcore.secrets import get; k = get('$id'); print('FOUND' if k else 'NONE')" 2>$null
    if ($existing -eq "FOUND") {
        $promptMsg = "  - $($p.Name) [Key already stored - press Enter to keep, or paste new]"
    } else {
        $promptMsg = "  - $($p.Name) ($($p.Url)) [Paste key or Enter to skip]"
    }

    $inputKey = Read-Host $promptMsg
    if (-not [string]::IsNullOrWhiteSpace($inputKey)) {
        $escapedKey = $inputKey.Trim().Replace("'", "''")
        uv run python -c "from promptcore.secrets import set_key; ok = set_key('$id', '$escapedKey'); print('    Saved to keyring.' if ok else '    Failed to save.')"
    }
}

Write-Host "`n[✓] Setup complete! Configured providers in PromptCore keyring:" -ForegroundColor Cyan
uv run python -c "from promptcore.secrets import list_configured_providers; print('   ', ', '.join(list_configured_providers()) or 'None yet')"
Write-Host "`nNext: Run 'uv run python ../promptcore/scripts/check_providers.py' once we generate Step 3, or proceed to Step 2 (Rule Engine)." -ForegroundColor Gray