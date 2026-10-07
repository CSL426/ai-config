# Install the standalone ai-config release. Python is not required.
# Keep this file ASCII: Windows PowerShell 5.1 reads a .ps1 without a BOM in the
# system code page, and on a cp950 machine a Chinese comment swallowed the next
# line and the whole script failed to parse (tests/test_release_contract.py).
#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repository = if ($env:AI_CONFIG_TOOL_REPOSITORY) { $env:AI_CONFIG_TOOL_REPOSITORY } else { 'CSL426/ai-config' }
$Version = if ($env:AI_CONFIG_VERSION) { $env:AI_CONFIG_VERSION } else { 'latest' }
$UserHome = [Environment]::GetFolderPath('UserProfile')
$BinDir = if ($env:AI_CONFIG_BIN_DIR) { $env:AI_CONFIG_BIN_DIR } else { Join-Path $UserHome '.local\bin' }
$LocalBinary = if ($env:AI_CONFIG_BINARY_PATH) { $env:AI_CONFIG_BINARY_PATH } else { $null }
$LocalLauncher = if ($env:AI_CONFIG_LAUNCHER_PATH) { $env:AI_CONFIG_LAUNCHER_PATH } else { $null }
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
# The same record ai_config.versions and the launcher read. Releases before
# 1.0.99 wrote it one level up, where nothing read it back.
$ActiveMarker = Join-Path $VersionsDir 'active'
$LauncherMarker = Join-Path $ShareDir 'launcher.sha256'
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
    # WSL runs this script too, since it appends the Windows PATH. WSL hands a
    # Windows program only the variables WSLENV names: without it the
    # entrypoint name is lost, and the console WSL gives the program looks
    # like a double-click, which waits for Enter. WSL's bash also never loads
    # the completion installed for Git Bash, so its ~/.bashrc sources it once.
    $Lines = @(
        '#!/usr/bin/env bash',
        'dir=$(dirname -- "$0")',
        'if [ -n "$WSL_DISTRO_NAME" ]; then',
        '    export AI_CONFIG_WSL=1 WSLENV="${WSLENV:+$WSLENV:}AI_CONFIG_ENTRYPOINT:AI_CONFIG_WSL"',
        '    completion=$(cd -- "$dir/../share/bash-completion/completions" 2>/dev/null && pwd)/acg.bash',
        '    if [ -f "$completion" ] && ! grep -qsF ''# >>> ai-config completion >>>'' ~/.bashrc; then',
        '        printf ''\n# >>> ai-config completion >>>\n[ -f %q ] && . %q\n# <<< ai-config completion <<<\n'' "$completion" "$completion" >> ~/.bashrc',
        '        echo "Added acg tab completion to ~/.bashrc in WSL; it works in new terminals." >&2',
        '    fi',
        'fi',
        'AI_CONFIG_ENTRYPOINT=__NAME__ exec "$dir/__EXE__" "$@"'
    )
    $Content = (($Lines -join "`n") + "`n").Replace('__NAME__', $Name).Replace('__EXE__', $ExecutableName)
    Write-Utf8NoBom $Launcher $Content
}

function Install-CommandAlias([string]$Name, [string]$Executable) {
    $AliasPath = Join-Path $BinDir "$Name.cmd"
    $ExecutableName = Split-Path -Leaf $Executable
    # Without it the hints tell people to type ai-config, while they type acg
    $Content = '@echo off' + "`r`n" + 'setlocal' + "`r`n" + 'set "AI_CONFIG_ENTRYPOINT=' + $Name + '"' + "`r`n" + '"%~dp0' + $ExecutableName + '" %*' + "`r`n"
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

function Move-WithRetry([string]$Source, [string]$Destination) {
    # Windows lets a running exe be renamed, but not a file some process has
    # open without delete sharing. A onefile acg opens its own exe that way
    # while it unpacks, and Claude Code starts one for the status line all
    # the time: the 1.0.99 update on Windows failed on exactly that. Those
    # processes last well under a second, so wait them out.
    $Attempts = 50
    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        try {
            Move-Item -LiteralPath $Source -Destination $Destination
            return
        }
        catch {
            if ($Attempt -eq $Attempts) { throw }
            Start-Sleep -Milliseconds 200
        }
    }
}

function Replace-Binary([string]$Source, [string]$Destination) {
    # `ai-config update` runs this while its own exe is still executing.
    # Windows refuses to overwrite a running exe but lets it be renamed, so
    # move it aside and put the new one in its place. The slow copy goes to a
    # side name first: between the two renames the path is empty only for an
    # instant, not for the whole copy, and a hook that starts the exe then
    # would otherwise find nothing there.
    $Staged = "$Destination.new"
    Copy-WithRetry $Source $Staged
    $Aside = $null
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        $Aside = "$Destination.old-" + [guid]::NewGuid().ToString('N')
        Move-WithRetry $Destination $Aside
    }
    try {
        Move-WithRetry $Staged $Destination
    }
    catch {
        if ($Aside) { Move-WithRetry $Aside $Destination }
        Remove-Item -LiteralPath $Staged -Force -ErrorAction SilentlyContinue
        throw
    }
    # An old file not running is deleted now; a running one refuses and is left for the next start
    if ($Aside) { Remove-Item -LiteralPath $Aside -Force -ErrorAction SilentlyContinue }
}

function Remove-ReplacedBinaries([string]$Destination) {
    Get-ChildItem -Path "$Destination.old-*" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Get-Sha256([string]$Path) {
    # .NET directly rather than Get-FileHash: Windows PowerShell started from
    # PowerShell 7 inherits its PSModulePath and cannot load the cmdlet.
    $Stream = [IO.File]::OpenRead($Path)
    try {
        $Hasher = [Security.Cryptography.SHA256]::Create()
        try { $Bytes = $Hasher.ComputeHash($Stream) }
        finally { $Hasher.Dispose() }
    }
    finally { $Stream.Dispose() }
    return ([BitConverter]::ToString($Bytes) -replace '-', '').ToLowerInvariant()
}

function Test-Launcher([string]$Path) {
    # Install-Launcher records the hash of what it put on PATH; a copy of a
    # real build left there by an older layout will not match it.
    # Mirrors ai_config.versions.is_launcher.
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $LauncherMarker -PathType Leaf)) { return $false }
    $Recorded = (Get-Content -LiteralPath $LauncherMarker -Raw).Trim().ToLowerInvariant()
    return (Get-Sha256 $Path) -eq $Recorded
}

function Set-ActiveVersion([string]$Resolved) {
    New-Item -ItemType Directory -Force -Path $VersionsDir | Out-Null
    Write-Utf8NoBom $ActiveMarker $Resolved
    # Releases before 1.0.99 wrote the record one level up; nothing reads it
    # now, and a stale value there would only mislead whoever looks
    Remove-Item -LiteralPath (Join-Path $ShareDir 'active') -Force -ErrorAction SilentlyContinue
    Remove-StaleVersions $Resolved
}

function Resolve-InstalledVersion([string]$Executable) {
    $Resolved = Get-BinaryVersion $Executable
    if (-not $Resolved) { $Resolved = ($Version -replace '^v', '') }
    # An unreadable version must not fail the install: a name that is merely
    # definite still gives the user a working binary, and the next update
    # that can name itself replaces it
    if (-not $Resolved -or $Resolved -eq 'latest') { $Resolved = 'unversioned' }
    return $Resolved
}

function Install-Binary([string]$Source, [string]$Destination) {
    # A single onefile exe: a local test build, or a release from before the
    # onedir archives.
    Adopt-ExistingBinary $Destination
    $Resolved = Resolve-InstalledVersion $Source
    $VersionRoot = Join-Path $VersionsDir $Resolved
    New-Item -ItemType Directory -Force -Path $VersionRoot | Out-Null
    # The same version installed as onedir before would shadow this file
    Remove-Item -LiteralPath (Join-Path $VersionRoot 'app') -Recurse -Force -ErrorAction SilentlyContinue
    Copy-WithRetry $Source (Join-Path $VersionRoot 'ai-config.exe')
    # With the launcher on PATH, recording the version is the whole switch
    if (-not (Test-Launcher $Destination)) {
        Replace-Binary (Join-Path $VersionRoot 'ai-config.exe') $Destination
    }
    Set-ActiveVersion $Resolved
}

function Install-Directory([string]$Source, [string]$Destination, [string]$Launcher) {
    # A onedir build: the exe and its _internal directory move together into
    # versions\<version>\app, staged beside it first so a failure never leaves
    # half a version for the launcher to start. app\ is for releases before
    # this one: switching versions they copy versions\<version>\ai-config.exe
    # onto PATH, and finding nothing there they run the installer instead.
    $Executable = Join-Path $Source 'ai-config.exe'
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { Fail "No ai-config.exe in $Source" }
    Adopt-ExistingBinary $Destination
    [void](Wait-ExecutableReady $Executable)
    $Resolved = Resolve-InstalledVersion $Executable
    $VersionRoot = Join-Path $VersionsDir $Resolved
    $Staging = Join-Path $VersionsDir (".$Resolved.staging-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    Copy-Item -LiteralPath $Source -Destination (Join-Path $Staging 'app') -Recurse -Force
    if (Test-Path -LiteralPath $VersionRoot) {
        $Aside = Join-Path $VersionsDir (".$Resolved.old-" + [guid]::NewGuid().ToString('N'))
        try {
            Rename-Item -LiteralPath $VersionRoot -NewName (Split-Path -Leaf $Aside)
        }
        catch {
            # Windows will not rename a directory whose exe is running: this
            # version is already installed and in use, so keep that copy.
            Write-Warn "$Resolved is already installed and running; keeping that copy"
            Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
            $Aside = $null
        }
        if ($Aside) {
            Rename-Item -LiteralPath $Staging -NewName $Resolved
            Remove-Item -LiteralPath $Aside -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    else {
        Rename-Item -LiteralPath $Staging -NewName $Resolved
    }
    Install-Launcher $Launcher $Destination
    Set-ActiveVersion $Resolved
}

function Install-Launcher([string]$Launcher, [string]$Destination) {
    # The launcher barely changes between releases; replace it only when it
    # did. The old file may be running (it may be the very acg that started
    # this update), and Replace-Binary moves a running file aside, never over.
    $Wanted = (Get-Sha256 $Launcher)
    $Current = if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        (Get-Sha256 $Destination)
    }
    if ($Current -ne $Wanted) { Replace-Binary $Launcher $Destination }
    New-Item -ItemType Directory -Force -Path $ShareDir | Out-Null
    Write-Utf8NoBom $LauncherMarker $Wanted
    # A onefile acg still running from the old path (the one that started this
    # update, before 1.0.99) reads its modules back from that path, which now
    # holds the launcher: it will print a decompression error as it exits.
    $StillRunning = @(Get-ChildItem -Path "$Destination.old-*" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -ge 4MB })
    if ($StillRunning.Count -gt 0) {
        Write-Warn 'The acg that started this update may print a zlib "Error -3" or "Error -5 while decompressing data" as it exits; the update itself is complete.'
    }
}

function Expand-Build([string]$Archive) {
    $Unpacked = Join-Path ([IO.Path]::GetTempPath()) ("ai-config-unpacked-" + [guid]::NewGuid().ToString('N'))
    # .NET directly for the same reason as Get-Sha256: Expand-Archive lives in
    # a script module that PowerShell 7's PSModulePath hides from 5.1
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Unpacked)
    $Root = Join-Path $Unpacked 'ai-config'
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { Fail "Unexpected archive layout: $Archive" }
    return $Root
}

function Save-Url([string]$Url, [string]$OutFile, [string]$Label = '') {
    # curl.exe ships with Windows 10 1803 and later. On one machine it fetched
    # the 13.5 MB release in 79 s where Invoke-WebRequest under Windows
    # PowerShell 5.1 took 249 s, and it can show progress, so a manual update
    # no longer sits silent for minutes. Progress is off when the caller
    # turned PowerShell's off (the desktop app and the scheduler read output).
    # Only a labelled download gets a bar, and its label goes right above it:
    # four unnamed bars (archive, launcher and their checksums) read as one
    # download stuck repeating itself.
    if ($Label) { Write-Step "Downloading $Label" }
    $Curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $Curl -PathType Leaf) {
        $Meter = if (-not $Label -or $ProgressPreference -eq 'SilentlyContinue') { '--silent' } else { '--progress-bar' }
        & $Curl --fail --location --show-error --retry 2 $Meter --output $OutFile $Url
        if ($LASTEXITCODE -ne 0) { throw "curl.exe exited $LASTEXITCODE for $Url" }
        return
    }
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $OutFile
}

function Get-VerifiedDownload([string]$BaseUrl, [string]$Name, [string]$Directory) {
    # $null when the release has no such asset, so the caller can fall back
    $Download = Join-Path $Directory $Name
    try {
        Save-Url "$BaseUrl/$Name" $Download $Name
    }
    catch {
        return $null
    }
    Save-Url "$BaseUrl/$Name.sha256" "$Download.sha256"
    $Expected = ((Get-Content -LiteralPath "$Download.sha256" -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
    $Actual = (Get-Sha256 $Download)
    if ($Actual -ne $Expected) { Fail "Downloaded $Name checksum mismatch" }
    return $Download
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
    if (Test-Launcher $Destination) { return }
    # A freshly unpacked onefile build can fail its first start for a moment;
    # the version it would have named is the one worth keeping, so wait for it.
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
        Where-Object { -not $_.Name.StartsWith('.') } |
        Sort-Object -Property @{ Expression = { try { [version]$_.Name } catch { [version]'0.0.0' } } } -Descending
    foreach ($Directory in $Ordered) {
        if ($Directory.Name -eq $Active) { continue }
        $Kept++
        if ($Kept -ge $KeepVersions) {
            # Rename first: a version still running cannot be renamed, and is
            # left whole instead of half-deleted for a later rollback to find.
            $Trash = ".trash-" + [guid]::NewGuid().ToString('N')
            try { Rename-Item -LiteralPath $Directory.FullName -NewName $Trash }
            catch { continue }
            Remove-Item -LiteralPath (Join-Path $VersionsDir $Trash) -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-ExecutableReady([string]$Executable) {
    # A freshly replaced onefile build unpacks its Python runtime into %TEMP% on
    # first launch, and an antivirus scan or a lingering file lock can make that
    # fail for a moment. Retry until it runs, so the checks below don't misread a
    # transient DLL failure as a real answer.
    $Attempts = if ($env:AI_CONFIG_READY_ATTEMPTS) { [int]$env:AI_CONFIG_READY_ATTEMPTS } else { 30 }
    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
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

function Find-GitBash {
    # git.exe sits in <Git>\cmd or <Git>\mingw64\bin; bash.exe in <Git>\bin
    $Git = Get-Command git.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $Git) { return $null }
    $Dir = Split-Path -Parent $Git.Source
    for ($Level = 0; $Level -lt 3 -and $Dir; $Level++) {
        $Bash = Join-Path $Dir 'bin\bash.exe'
        if (Test-Path -LiteralPath $Bash -PathType Leaf) { return $Bash }
        $Dir = Split-Path -Parent $Dir
    }
    return $null
}

function Update-GitBashRc([string]$HomeDir) {
    # Git Bash never looks in ~/.local/share/bash-completion by itself, so the
    # completion written there did nothing until ~/.bashrc sourced it. bash
    # wants LF and no BOM here.
    $BashRc = Join-Path $HomeDir '.bashrc'
    $MarkerStart = '# >>> ai-config completion >>>'
    $Source = '"$HOME/.local/share/bash-completion/completions/acg.bash"'
    $Block = $MarkerStart + "`n" + "[ -f $Source ] && . $Source" + "`n" + '# <<< ai-config completion <<<'
    $Text = Read-ProfileText $BashRc
    if (-not $Text.Contains($MarkerStart)) {
        $Separator = if ($Text -and -not $Text.EndsWith("`n")) { "`n" } else { '' }
        Write-Utf8NoBom $BashRc ($Text + $Separator + $Block + "`n")
    }
    # A ~/.bashrc with no login file makes Git Bash warn once and write this
    # same line itself; writing it here spares the warning.
    $Logins = @('.bash_profile', '.bash_login', '.profile') | ForEach-Object { Join-Path $HomeDir $_ }
    if (-not ($Logins | Where-Object { Test-Path -LiteralPath $_ })) {
        Write-Utf8NoBom $Logins[0] "test -f ~/.bashrc && . ~/.bashrc`n"
    }
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
    if ((Test-Path -LiteralPath (Join-Path $UserHome '.bashrc') -PathType Leaf) -or (Find-GitBash)) {
        Update-GitBashRc $UserHome
    }
    Write-Step 'Installed Bash and PowerShell completions; restart the terminal to load them.'
}

if (-not [Environment]::Is64BitOperatingSystem) {
    Fail 'Only 64-bit Windows is supported.'
}
$Asset = 'ai-config-windows-x86_64.exe'
$Archive = 'ai-config-windows-x86_64.zip'
$LauncherAsset = 'ai-config-launcher-windows-x86_64.exe'
$Destination = Join-Path $BinDir 'ai-config.exe'
$Operation = if (Test-Path -LiteralPath $Destination -PathType Leaf) { 'Update' } else { 'Installation' }
$BinaryVerb = if ($Operation -eq 'Update') { 'Updated' } else { 'Installed' }
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Remove-ReplacedBinaries $Destination

if ($LocalBinary) {
    if (Test-Path -LiteralPath $LocalBinary -PathType Container) {
        if (-not $LocalLauncher) { Fail 'AI_CONFIG_LAUNCHER_PATH is required with a local build directory' }
        Write-Step 'Installing local build directory'
        Install-Directory $LocalBinary $Destination $LocalLauncher
    }
    elseif (-not (Test-Path -LiteralPath $LocalBinary -PathType Leaf)) {
        Fail "Local binary not found: $LocalBinary"
    }
    elseif ($LocalBinary.EndsWith('.zip')) {
        if (-not $LocalLauncher) { Fail 'AI_CONFIG_LAUNCHER_PATH is required with a local build archive' }
        Write-Step 'Installing local build archive'
        $Root = Expand-Build $LocalBinary
        try { Install-Directory $Root $Destination $LocalLauncher }
        finally { Remove-Item -LiteralPath (Split-Path $Root) -Recurse -Force -ErrorAction SilentlyContinue }
    }
    else {
        Write-Step 'Installing local standalone binary'
        Install-Binary $LocalBinary $Destination
    }
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
        $Downloaded = Get-VerifiedDownload $BaseUrl $Archive $TemporaryDir
        if ($Downloaded) {
            $Launcher = Get-VerifiedDownload $BaseUrl $LauncherAsset $TemporaryDir
            if (-not $Launcher) { Fail "The release has $Archive but no $LauncherAsset" }
            $Root = Expand-Build $Downloaded
            try { Install-Directory $Root $Destination $Launcher }
            finally { Remove-Item -LiteralPath (Split-Path $Root) -Recurse -Force -ErrorAction SilentlyContinue }
        }
        else {
            # Releases before 1.0.99 ship a single onefile exe
            Write-Step "No archive in this release"
            $Downloaded = Get-VerifiedDownload $BaseUrl $Asset $TemporaryDir
            if (-not $Downloaded) { Fail "Could not download $Asset" }
            Install-Binary $Downloaded $Destination
        }
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
# The desktop app is for people who do not type commands. Only a first install
# creates the shortcut, so one the user deleted does not keep coming back.
if ($Operation -eq 'Installation' -and -not $env:AI_CONFIG_NO_SHORTCUT) {
    & $Destination gui --shortcut
    if ($LASTEXITCODE -ne 0) { Write-Warn "Desktop shortcut was not created; run: ai-config gui --shortcut" }
}
# Bring Claude Code's /acg up to this version, and on update repair hook paths.
# The version just installed does it: the older acg that started the update may
# be reading its modules from a file that was just replaced, and dies importing.
if (-not $env:AI_CONFIG_NO_PLUGIN) {
    & $Destination __claude-plugin
    if ($LASTEXITCODE -ne 0) { Write-Warn "Claude Code /acg was not updated; run: ai-config update" }
}
if ($Operation -eq 'Update') {
    & $Destination __refresh-hooks
    if ($LASTEXITCODE -ne 0) { Write-Warn "Hook paths were not refreshed; run: ai-config apply" }
}
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
