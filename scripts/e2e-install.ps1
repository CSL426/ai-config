# End-to-end check of the onedir + launcher layout on Windows, the
# counterpart of e2e-install.sh. Everything happens under a scratch directory
# with USERPROFILE pointed at it, so the machine's own install, PATH, profile
# and ~/.claude are never touched. CI runs it with -NoGui; on a real machine
# run it without, and it also opens and closes the desktop window a few times.
# Needs network: the migration step downloads the 1.0.97 release.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\e2e-install.ps1 `
#          -Archive <zip> -Launcher <launcher exe> -Portable <acg.exe> -Installer install.ps1 [-NoGui]
# Keep this file ASCII, like install.ps1.
param(
    [Parameter(Mandatory)][string]$Archive,
    [Parameter(Mandatory)][string]$Launcher,
    [Parameter(Mandatory)][string]$Portable,
    [Parameter(Mandatory)][string]$Installer,
    [switch]$NoGui
)
# Continue, not Stop: Windows PowerShell turns a native command's redirected
# stderr into an error record, and a command expected to fail would end the run
$ErrorActionPreference = 'Continue'
$Archive = (Resolve-Path $Archive).Path; $Launcher = (Resolve-Path $Launcher).Path
$Portable = (Resolve-Path $Portable).Path; $Installer = (Resolve-Path $Installer).Path
$Real = [Environment]::GetFolderPath('UserProfile')
$Root = Join-Path $env:TEMP ('acg-e2e-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $Root | Out-Null
$script:Fails = 0
function Check([string]$Name, [scriptblock]$Test) {
    $ok = $false
    try { $ok = [bool](& $Test) } catch { Write-Host "    ($($_.Exception.Message))" }
    if ($ok) { Write-Host "PASS $Name" } else { Write-Host "FAIL $Name"; $script:Fails++ }
}
function Time-Command([scriptblock]$Block, [int]$Times = 5) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    for ($i = 0; $i -lt $Times; $i++) { & $Block | Out-Null }
    return [int]($watch.ElapsedMilliseconds / $Times)
}
function Gui-Processes { Get-Process ai-config -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'acg*' } }

Write-Host "== scratch root $Root"
Copy-Item $Installer "$Root\install.ps1"
$Zip = $Archive; $LauncherFile = $Launcher
$portable = & $Portable --version
Check 'the portable acg.exe runs' { "$portable" -like 'ai-config (acg)*' }

# Scratch home: from here on this process and its children only see it
$FakeHome = Join-Path $Root 'home'
New-Item -ItemType Directory -Force -Path "$Root\data\claude", $FakeHome | Out-Null
$env:USERPROFILE = $FakeHome; $env:HOME = $FakeHome
$env:AI_CONFIG_BIN_DIR = "$FakeHome\.local\bin"; $env:AI_CONFIG_SHARE_DIR = "$FakeHome\.local\share\ai-config"
$env:AI_CONFIG_REPO = "$Root\data"
$env:AI_CONFIG_NO_PLUGIN = '1'; $env:AI_CONFIG_SKIP_PATH_UPDATE = '1'; $env:AI_CONFIG_SKIP_COMPLETION = '1'
$env:AI_CONFIG_NO_SHORTCUT = '1'; $env:AI_CONFIG_NO_UPDATE_CHECK = '1'
$Bin = $env:AI_CONFIG_BIN_DIR; $Versions = "$env:AI_CONFIG_SHARE_DIR\versions"; $Entry = "$Bin\ai-config.exe"

Write-Host '== fresh install from the zip'
$env:AI_CONFIG_BINARY_PATH = $Zip; $env:AI_CONFIG_LAUNCHER_PATH = $LauncherFile
$watch = [Diagnostics.Stopwatch]::StartNew()
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Root\install.ps1" *> "$Root\install.log"
Write-Host "    install took $($watch.Elapsed.TotalSeconds.ToString('0.0')) s, exit $LASTEXITCODE"
Check 'install exits 0' { $LASTEXITCODE -eq 0 }
$Active = (Get-Content "$Versions\active" -Raw).Trim()
Check 'versions\active names the build' { $Active -match '^\d+\.\d+\.\d+$' }
Check 'onedir sits in versions\<v>\app' { (Test-Path "$Versions\$Active\app\ai-config.exe") -and (Test-Path "$Versions\$Active\app\_internal") }
Check 'nothing old-style beside app\' { -not (Test-Path "$Versions\$Active\ai-config.exe") }
Check 'PATH entry is the small launcher' { (Get-Item $Entry).Length -lt 4MB }
Check 'launcher hash recorded' { Test-Path "$env:AI_CONFIG_SHARE_DIR\launcher.sha256" }

Write-Host '== running through the launcher'
$v = & $Entry --version; Write-Host "    $v"
Check 'acg --version via launcher' { $LASTEXITCODE -eq 0 -and "$v" -like 'ai-config (acg)*' }
& $Entry version | Out-Null; $ok = $LASTEXITCODE
& $Entry definitely-not-a-command *> $null; $bad = $LASTEXITCODE
Check 'exit codes pass through (0 and non-zero)' { $ok -eq 0 -and $bad -ne 0 }
# The active line carries a check mark before the version; its bytes depend on the console code page
Check 'acg versions marks the active one' { @(& $Entry versions 2>&1 | Where-Object { "$_" -match ('^\s*\S+\s+' + [regex]::Escape($Active) + '\s*$') }).Count -eq 1 }
$new = Time-Command { & $Entry --version }
$newHook = Time-Command { & $Entry __handoff-statusline eA== 70 }
$oldExe = Join-Path $Real '.local\bin\ai-config.exe'
$old = if (Test-Path $oldExe) { Time-Command { & $oldExe --version } } else { -1 }
Write-Host "    --version: launcher+onedir $new ms, installed onefile $old ms; statusline $newHook ms"
Check 'launcher+onedir starts faster than the installed onefile' { $old -lt 0 -or $new -lt $old }

if (-not $NoGui) {
Write-Host '== cmd.exe starts'
foreach ($case in @(@{ Name = 'acg (no arguments)'; Args = '' }, @{ Name = 'acg gui'; Args = 'gui' })) {
    Gui-Processes | Stop-Process -Force
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process cmd.exe -ArgumentList "/c `"$Bin\acg.cmd`" $($case.Args)" -PassThru -WindowStyle Hidden
    $returned = $p.WaitForExit(15000)
    $secs = $watch.Elapsed.TotalSeconds.ToString('0.0')
    Start-Sleep -Seconds 6
    $windows = @(Gui-Processes)
    Check "cmd /c $($case.Name) returns (${secs}s) and a window opens" { $returned -and $windows.Count -ge 1 }
    $windows | Stop-Process -Force
}

Write-Host '== double-click (new console owned by the launcher)'
Gui-Processes | Stop-Process -Force
$p = Start-Process $Entry -PassThru
Start-Sleep -Seconds 8
$windows = @(Gui-Processes)
Check 'double-click opens the window' { $windows.Count -ge 1 }
Add-Type -Namespace E2E -Name Win -MemberDefinition '[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);'
$consoleVisible = $false
foreach ($proc in Get-Process -Id $p.Id -ErrorAction SilentlyContinue) {
    if ($proc.MainWindowHandle -ne [IntPtr]::Zero) { $consoleVisible = [E2E.Win]::IsWindowVisible($proc.MainWindowHandle) }
}
Check 'the launcher console is hidden while the window is up' { -not $consoleVisible }
$windows | Stop-Process -Force
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}

Write-Host '== replacing a running onefile copy on PATH (the migration)'
Remove-Item -Recurse -Force $Bin, $env:AI_CONFIG_SHARE_DIR
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
# A real older onefile (a different version, so it does not share a version directory with this build)
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/CSL426/ai-config/releases/download/v1.0.97/ai-config-windows-x86_64.exe' -OutFile $Entry
# Something must be running from the old path while the installer replaces it:
# the desktop window on a real machine, a long hidden command in CI
$running = if ($NoGui) {
    # __channel reads its own console's input until closed
    Start-Process $Entry -ArgumentList '__channel' -PassThru -WindowStyle Hidden
} else {
    Start-Process $Entry -ArgumentList 'gui', '--wait' -PassThru -WindowStyle Hidden
}
Start-Sleep -Seconds 8
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Root\install.ps1" *> "$Root\migrate.log"
Check 'installer succeeds while the old exe runs' { $LASTEXITCODE -eq 0 }
Check 'launcher replaced the running copy' { (Get-Item $Entry).Length -lt 4MB }
Check 'the running old process was not interrupted' { -not $running.HasExited }
Check 'the old copy was adopted into versions\1.0.97' { Test-Path "$Versions\1.0.97\ai-config.exe" }
Check 'installer warned about the one-time exit error' { Select-String -Path "$Root\migrate.log" -Pattern 'decompressing' -Quiet }
$running | Stop-Process -Force -ErrorAction SilentlyContinue; Gui-Processes | Stop-Process -Force
Start-Sleep -Seconds 2
& $Entry version | Out-Null
Check 'the moved-aside copy is cleaned on a later start' { @(Get-ChildItem "$Bin\ai-config.exe.old-*" -ErrorAction SilentlyContinue).Count -eq 0 }

Write-Host '== switching versions'
$adopted = '1.0.97'
& $Entry update $adopted *> "$Root\switch.log"
Check "switch to the adopted $adopted on disk, no download" { $LASTEXITCODE -eq 0 -and -not (Select-String -Path "$Root\switch.log" -Pattern 'Fetching|Downloading' -Quiet) -and ((Get-Content "$Versions\active" -Raw).Trim() -eq $adopted) }
Check 'launcher still on PATH after the switch' { (Get-Item $Entry).Length -lt 4MB }
# Switch back with the new code: this build reports 1.0.98 like the release, so 1.0.97 would download the release instead
& "$Versions\$Active\app\ai-config.exe" update $Active *> "$Root\switch-back.log"
Check "switch back to $Active with the new code" { (Get-Content "$Versions\active" -Raw).Trim() -eq $Active -and (& $Entry --version) -like "*$Active*" -and (Get-Item $Entry).Length -lt 4MB }

Write-Host "RESULT: $script:Fails failure(s); logs in $Root"
Gui-Processes | Stop-Process -Force
if ($script:Fails -eq 0) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue; exit 0 }
Get-ChildItem "$Root\*.log" | ForEach-Object { Write-Host "--- $($_.Name)"; Get-Content $_.FullName }
exit 1
