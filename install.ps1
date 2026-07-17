# ai-config bootstrap installer (Windows)
#
#   全新機器:  git clone <tool-repo-url> $HOME\ai-config-tool; & $HOME\ai-config-tool\install.ps1
#   已有 repo: & $HOME\ai-config-tool\install.ps1
#
# 全自動處理:定位 Python → 建獨立 venv → editable 安裝 → PATH shim。
# 可用環境變數覆寫:AI_CONFIG_REPO_URL / AI_CONFIG_HOME / AI_CONFIG_VENV
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoUrl = if ($env:AI_CONFIG_REPO_URL) { $env:AI_CONFIG_REPO_URL } else { $null }
$Target = if ($env:AI_CONFIG_HOME) { $env:AI_CONFIG_HOME } else { Join-Path $HOME 'ai-config' }
$Venv = if ($env:AI_CONFIG_VENV) { $env:AI_CONFIG_VENV } else { Join-Path $HOME '.venvs\ai-config' }
$BinDir = Join-Path $HOME '.local\bin'

function Write-Step([string]$Message) { Write-Host "* $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "! $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { Write-Host "x $Message" -ForegroundColor Red; exit 1 }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ((Test-Path (Join-Path $scriptDir 'pyproject.toml')) -and (Test-Path (Join-Path $scriptDir 'ai_config'))) {
    $ToolSource = $scriptDir
    Write-Step "Using this checkout: $ToolSource"
    
    if ($RepoUrl -and -not (Test-Path (Join-Path $Target '.git'))) {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'git is required to clone data repository' }
        Write-Step "Cloning data repository: $RepoUrl -> $Target"
        git clone $RepoUrl $Target
        if ($LASTEXITCODE -ne 0) { Fail 'git clone failed' }
    }
}
else {
    Fail 'install.ps1 must be run from inside the ai-config tool repository checkout.'
}

# Python >= 3.11:依序嘗試 py -3 / python / python3
$pythonCommand = $null
$candidates = @(
    @{ Exe = 'py'; Args = @('-3') },
    @{ Exe = 'python'; Args = @() },
    @{ Exe = 'python3'; Args = @() }
)
foreach ($candidate in $candidates) {
    if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
    & $candidate.Exe @($candidate.Args) -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>$null
    if ($LASTEXITCODE -eq 0) { $pythonCommand = $candidate; break }
}
if (-not $pythonCommand) {
    Fail 'Python 3.11+ not found. Install it first: winget install Python.Python.3.12'
}
Write-Step "Python: $($pythonCommand.Exe) $($pythonCommand.Args -join ' ')"

Write-Step "Creating venv: $Venv"
& $pythonCommand.Exe @($pythonCommand.Args) -m venv $Venv
if ($LASTEXITCODE -ne 0) { Fail 'venv creation failed' }

$pip = Join-Path $Venv 'Scripts\pip.exe'
& $pip install --quiet --editable $ToolSource
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed' }

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$exe = Join-Path $Venv 'Scripts\ai-config.exe'
$shim = Join-Path $BinDir 'ai-config.cmd'
Set-Content -Path $shim -Encoding ascii -Value "@echo off`r`n`"$exe`" %*"
Write-Step "Installed shim: $shim"

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$BinDir", 'User')
    Write-Warn "Added $BinDir to user PATH - restart your terminal for it to take effect."
}

Write-Step 'Done. Try: ai-config status'
