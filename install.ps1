# Install the standalone ai-config release. Python is not required.
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repository = if ($env:AI_CONFIG_TOOL_REPOSITORY) { $env:AI_CONFIG_TOOL_REPOSITORY } else { 'CSL426/ai-config' }
$Version = if ($env:AI_CONFIG_VERSION) { $env:AI_CONFIG_VERSION } else { 'latest' }
$UserHome = [Environment]::GetFolderPath('UserProfile')
$BinDir = if ($env:AI_CONFIG_BIN_DIR) { $env:AI_CONFIG_BIN_DIR } else { Join-Path $UserHome '.local\bin' }
$LocalBinary = if ($env:AI_CONFIG_BINARY_PATH) { $env:AI_CONFIG_BINARY_PATH } else { $null }
$DataRepoUrl = if ($env:AI_CONFIG_REPO_URL) { $env:AI_CONFIG_REPO_URL } else { $null }
$DataDir = if ($env:AI_CONFIG_DATA_DIR) { $env:AI_CONFIG_DATA_DIR } elseif ($env:AI_CONFIG_HOME) { $env:AI_CONFIG_HOME } else { $null }
$SkipPathUpdate = $env:AI_CONFIG_SKIP_PATH_UPDATE -eq '1'

function Write-Step([string]$Message) { Write-Host "* $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "! $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { Write-Host "x $Message" -ForegroundColor Red; exit 1 }

if (-not [Environment]::Is64BitOperatingSystem) {
    Fail 'Only 64-bit Windows is supported.'
}
$Asset = 'ai-config-windows-x86_64.exe'
$Destination = Join-Path $BinDir 'ai-config.exe'
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

if ($LocalBinary) {
    if (-not (Test-Path -LiteralPath $LocalBinary -PathType Leaf)) {
        Fail "Local binary not found: $LocalBinary"
    }
    Write-Step 'Installing local standalone binary'
    Copy-Item -LiteralPath $LocalBinary -Destination $Destination -Force
}
else {
    $BaseUrl = if ($Version -eq 'latest') {
        "https://github.com/$Repository/releases/latest/download"
    }
    else {
        "https://github.com/$Repository/releases/download/$Version"
    }
    $TemporaryDir = Join-Path ([IO.Path]::GetTempPath()) ("ai-config-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $TemporaryDir | Out-Null
    try {
        $Download = Join-Path $TemporaryDir $Asset
        $Checksum = "$Download.sha256"
        Write-Step "Downloading $Asset"
        Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/$Asset" -OutFile $Download
        Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/$Asset.sha256" -OutFile $Checksum
        $Expected = ((Get-Content -LiteralPath $Checksum -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
        $Actual = (Get-FileHash -LiteralPath $Download -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -ne $Expected) { Fail 'Downloaded binary checksum mismatch' }
        Copy-Item -LiteralPath $Download -Destination $Destination -Force
    }
    finally {
        Remove-Item -LiteralPath $TemporaryDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Step "Installed: $Destination"
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $SkipPathUpdate -and ($UserPath -split ';') -notcontains $BinDir) {
    $UpdatedPath = if ($UserPath) { "$UserPath;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable('Path', $UpdatedPath, 'User')
    Write-Warn "Added $BinDir to user PATH; restart the terminal to use ai-config."
}

if ($DataRepoUrl -or $DataDir) {
    if (-not $DataDir) { $DataDir = Join-Path $UserHome 'ai-config\data' }
    $SetupArgs = @('setup', '--data-dir', $DataDir)
    if ($DataRepoUrl) { $SetupArgs += @('--repo-url', $DataRepoUrl) }
    & $Destination @SetupArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    if (-not [Console]::IsInputRedirected) {
        Write-Step 'Starting first-run setup'
        & $Destination
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    else {
        Write-Step 'Next: ai-config setup'
    }
}
