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

# Each version lives in its own directory and the name on PATH is only a
# pointer to one. A symlink needs a privilege an ordinary account may not
# hold here, so the pointer degrades to a copy and a marker file records
# which version it came from -- the layout stays the same either way.
$ShareDir = if ($env:AI_CONFIG_SHARE_DIR) { $env:AI_CONFIG_SHARE_DIR } else { Join-Path $UserHome '.local\share\ai-config' }
$VersionsDir = Join-Path $ShareDir 'versions'
$ActiveMarker = Join-Path $ShareDir 'active'
$KeepVersions = if ($env:AI_CONFIG_KEEP_VERSIONS) { [int]$env:AI_CONFIG_KEEP_VERSIONS } else { 5 }

function Write-Step([string]$Message) { Write-Host "* $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "! $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { Write-Host "x $Message" -ForegroundColor Red; exit 1 }

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Install-GitBashLauncher([string]$Name, [string]$Executable) {
    $Launcher = Join-Path $BinDir $Name
    $ExecutableName = Split-Path -Leaf $Executable
    $Content = (
        '#!/usr/bin/env bash' + "`n" +
        'exec "$(dirname -- "$0")/' + $ExecutableName + '" "$@"' + "`n"
    )
    Write-Utf8NoBom $Launcher $Content
}

function Install-CommandAlias([string]$Name, [string]$Executable) {
    $AliasPath = Join-Path $BinDir "$Name.cmd"
    $ExecutableName = Split-Path -Leaf $Executable
    $Content = '@echo off' + "`r`n" + '"%~dp0' + $ExecutableName + '" %*' + "`r`n"
    Write-Utf8NoBom $AliasPath $Content
}

function Copy-WithRetry([string]$Source, [string]$Destination) {
    $Attempts = 50
    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        try {
            Copy-Item -LiteralPath $Source -Destination $Destination -Force
            return
        }
        catch {
            if ($Attempt -eq $Attempts) { throw }
            Start-Sleep -Milliseconds 200
        }
    }
}

function Install-Binary([string]$Source, [string]$Destination) {
    Adopt-ExistingBinary $Destination
    $Resolved = Get-BinaryVersion $Source
    if (-not $Resolved) { $Resolved = ($Version -replace '^v', '') }
    # An unreadable version must not fail the install: a name that is merely
    # definite still gives the user a working binary, and the next update
    # that can name itself replaces it
    if (-not $Resolved -or $Resolved -eq 'latest') { $Resolved = 'unversioned' }

    $VersionRoot = Join-Path $VersionsDir $Resolved
    New-Item -ItemType Directory -Force -Path $VersionRoot | Out-Null
    Copy-WithRetry $Source (Join-Path $VersionRoot 'ai-config.exe')
    Copy-WithRetry (Join-Path $VersionRoot 'ai-config.exe') $Destination
    New-Item -ItemType Directory -Force -Path $ShareDir | Out-Null
    Write-Utf8NoBom $ActiveMarker $Resolved
    Remove-StaleVersions $Resolved
}

function Get-BinaryVersion([string]$Executable) {
    # Ask the binary itself: $Version may be "latest", which names no directory.
    # This runs before Wait-ExecutableReady has vouched for the file, so a
    # download that cannot start yet must yield no version, never an error.
    $global:LASTEXITCODE = 0
    try {
        $Reported = & $Executable version 2>$null | Out-String
    }
    catch { return $null }
    if ($LASTEXITCODE -ne 0) { return $null }
    $Match = [regex]::Match($Reported, '\d+\.\d+\.\d+')
    if ($Match.Success) { return $Match.Value }
    return $null
}

function Adopt-ExistingBinary([string]$Destination) {
    # A machine installed before this layout has the real exe sitting on PATH.
    # Take it into a version directory on the first update, or every later one
    # still overwrites a file that may be running.
    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) { return }
    if (Test-Path -LiteralPath $ActiveMarker) { return }
    # The process that ran `update` has only just exited, so Windows may still
    # hold this file. Asking it for a version right away answers nothing, and
    # the version it would have named is the one worth keeping.
    if (-not (Wait-ExecutableReady $Destination)) {
        Write-Warn "The installed binary did not start; keeping no copy of it"
        return
    }
    $Existing = Get-BinaryVersion $Destination
    if (-not $Existing) {
        Write-Warn "Could not read the installed version; keeping no copy of it"
        return
    }
    $Adopted = Join-Path (Join-Path $VersionsDir $Existing) 'ai-config.exe'
    if (Test-Path -LiteralPath $Adopted) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path $Adopted) | Out-Null
    Copy-Item -LiteralPath $Destination -Destination $Adopted -Force
    Write-Step "Adopted the existing $Existing into $(Split-Path $Adopted)"
}

function Remove-StaleVersions([string]$Active) {
    if (-not (Test-Path -LiteralPath $VersionsDir)) { return }
    $Kept = 0
    # Newest first, and never the one in use
    $Ordered = Get-ChildItem -LiteralPath $VersionsDir -Directory |
        Sort-Object -Property @{ Expression = { try { [version]$_.Name } catch { [version]'0.0.0' } } } -Descending
    foreach ($Directory in $Ordered) {
        if ($Directory.Name -eq $Active) { continue }
        $Kept++
        if ($Kept -ge $KeepVersions) {
            Remove-Item -LiteralPath $Directory.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-ExecutableReady([string]$Executable) {
    # A freshly replaced onefile build unpacks its Python runtime into %TEMP% on
    # first launch, and an antivirus scan or a lingering file lock can make that
    # fail for a moment. Retry until it runs, so the checks below don't misread a
    # transient DLL failure as a real answer.
    for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
        try {
            & $Executable version *> $null
            if ($LASTEXITCODE -eq 0) { return $true }
        }
        catch { }
        Start-Sleep -Milliseconds 1000
    }
    return $false
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
    Write-Utf8NoBom (Join-Path $BashCompletionDir 'acg.bash') $BashText
    $LegacyExeCompletion = Join-Path $BashCompletionDir 'ai-config.exe.bash'
    Remove-Item -LiteralPath $LegacyExeCompletion -Force -ErrorAction SilentlyContinue

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
    Install-Binary $LocalBinary $Destination
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
        Install-Binary $Download $Destination
    }
    finally {
        Remove-Item -LiteralPath $TemporaryDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Install-GitBashLauncher 'ai-config' $Destination
Install-GitBashLauncher 'acg' $Destination
Install-CommandAlias 'acg' $Destination
Write-Step "${BinaryVerb}: $Destination"
if (-not (Wait-ExecutableReady $Destination)) {
    Write-Warn 'The installed executable did not start yet; skipping completion and setup.'
    Write-Step "$Operation complete; verify with: ai-config version"
    exit 0
}
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
