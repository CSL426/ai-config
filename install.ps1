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
$SkipCompletion = $env:AI_CONFIG_SKIP_COMPLETION -eq '1'

function Write-Step([string]$Message) { Write-Host "* $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "! $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { Write-Host "x $Message" -ForegroundColor Red; exit 1 }

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Install-GitBashLauncher([string]$Executable) {
    $Launcher = Join-Path $BinDir 'ai-config'
    $ExecutableName = Split-Path -Leaf $Executable
    $Content = (
        '#!/usr/bin/env bash' + "`n" +
        'exec "$(dirname -- "$0")/' + $ExecutableName + '" "$@"' + "`n"
    )
    Write-Utf8NoBom $Launcher $Content
}

function Test-ExistingConfiguration([string]$Executable) {
    try {
        & $Executable list *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Read-ProfileText([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $Bytes = [IO.File]::ReadAllBytes($Path)
    if ($Bytes.Length -eq 0) { return '' }
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xef -and $Bytes[1] -eq 0xbb -and $Bytes[2] -eq 0xbf) {
        return (New-Object System.Text.UTF8Encoding($true)).GetString($Bytes, 3, $Bytes.Length - 3)
    }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xff -and $Bytes[1] -eq 0xfe) {
        return [Text.Encoding]::Unicode.GetString($Bytes, 2, $Bytes.Length - 2)
    }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xfe -and $Bytes[1] -eq 0xff) {
        return [Text.Encoding]::BigEndianUnicode.GetString($Bytes, 2, $Bytes.Length - 2)
    }
    try {
        return (New-Object System.Text.UTF8Encoding($false, $true)).GetString($Bytes)
    }
    catch {
        return [Text.Encoding]::Default.GetString($Bytes)
    }
}

function Update-CompletionProfile([string]$ProfilePath, [string]$CompletionPath) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ProfilePath) | Out-Null
    $MarkerStart = '# >>> ai-config completion >>>'
    $MarkerEnd = '# <<< ai-config completion <<<'
    $QuotedCompletionPath = $CompletionPath.Replace("'", "''")
    $Block = "$MarkerStart`r`n. '$QuotedCompletionPath'`r`n$MarkerEnd"
    $ProfileText = Read-ProfileText $ProfilePath
    $StartIndex = $ProfileText.IndexOf($MarkerStart, [StringComparison]::Ordinal)
    $EndIndex = if ($StartIndex -ge 0) {
        $ProfileText.IndexOf($MarkerEnd, $StartIndex, [StringComparison]::Ordinal)
    }
    else {
        -1
    }
    if ($StartIndex -ge 0 -and $EndIndex -ge 0) {
        $SuffixIndex = $EndIndex + $MarkerEnd.Length
        $UpdatedProfile = $ProfileText.Substring(0, $StartIndex) + $Block + $ProfileText.Substring($SuffixIndex)
    }
    else {
        $Separator = if ($ProfileText -and -not $ProfileText.EndsWith("`n")) { "`r`n" } else { '' }
        $UpdatedProfile = $ProfileText + $Separator + $Block + "`r`n"
    }
    $Encoding = New-Object System.Text.UTF8Encoding($true)
    [IO.File]::WriteAllText($ProfilePath, $UpdatedProfile, $Encoding)
}

function Install-Completions([string]$Executable) {
    if ($SkipCompletion) { return }
    try {
        $BashCompletion = @(& $Executable completion bash)
        if ($LASTEXITCODE -ne 0) { throw 'Bash completion generation failed.' }
        $PowerShellCompletion = @(& $Executable completion powershell)
        if ($LASTEXITCODE -ne 0) { throw 'PowerShell completion generation failed.' }
    }
    catch {
        Write-Warn "Shell completion could not be installed: $($_.Exception.Message)"
        return
    }

    $BashCompletionDir = Join-Path $UserHome '.local\share\bash-completion\completions'
    New-Item -ItemType Directory -Force -Path $BashCompletionDir | Out-Null
    $BashText = ($BashCompletion -join "`n") + "`n"
    Write-Utf8NoBom (Join-Path $BashCompletionDir 'ai-config.bash') $BashText
    Write-Utf8NoBom (Join-Path $BashCompletionDir 'ai-config.exe.bash') $BashText

    $CompletionDir = Join-Path $UserHome '.local\share\ai-config'
    New-Item -ItemType Directory -Force -Path $CompletionDir | Out-Null
    $PowerShellCompletionPath = Join-Path $CompletionDir 'completion.ps1'
    Write-Utf8NoBom $PowerShellCompletionPath (($PowerShellCompletion -join "`r`n") + "`r`n")

    Update-CompletionProfile $PROFILE.CurrentUserAllHosts $PowerShellCompletionPath
    Write-Step 'Installed Bash and PowerShell completions; restart the terminal to load them.'
}

if (-not [Environment]::Is64BitOperatingSystem) {
    Fail 'Only 64-bit Windows is supported.'
}
$Asset = 'ai-config-windows-x86_64.exe'
$Destination = Join-Path $BinDir 'ai-config.exe'
$Operation = if (Test-Path -LiteralPath $Destination -PathType Leaf) { 'Update' } else { 'Installation' }
$BinaryVerb = if ($Operation -eq 'Update') { 'Updated' } else { 'Installed' }
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

Install-GitBashLauncher $Destination
Write-Step "${BinaryVerb}: $Destination"
Install-Completions $Destination
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
    Write-Step "$Operation complete"
}
elseif (Test-ExistingConfiguration $Destination) {
    Write-Step "$Operation complete; existing data repository configuration preserved."
}
else {
    if (-not [Console]::IsInputRedirected) {
        Write-Step 'Starting first-run setup'
        & $Destination
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Step "$Operation complete"
    }
    else {
        Write-Step "$Operation complete; next: ai-config setup"
    }
}
