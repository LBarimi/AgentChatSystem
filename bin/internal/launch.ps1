param([switch]$NoOpen, [switch]$Stop)
$ErrorActionPreference = 'Stop'
$chatRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$chatArchive = Join-Path $PSScriptRoot 'AgentChatSystem.pyz'
$chatVar = Join-Path $chatRoot 'var'
$chatPidFile = Join-Path $chatVar 'server.json'
$chatLauncherLog = Join-Path $chatVar 'launcher-error.log'
$chatProcess = $null

trap {
    $chatDetails = ($_ | Out-String).TrimEnd()
    [Console]::Error.WriteLine($chatDetails)
    try {
        New-Item -ItemType Directory -Force -Path $chatVar | Out-Null
        Add-Content -LiteralPath $chatLauncherLog -Encoding UTF8 -Value ('[{0:o}] {1}' -f [DateTimeOffset]::Now, $chatDetails)
        [Console]::Error.WriteLine("Launcher error log: $chatLauncherLog")
    } catch {
        # Keep the original error visible if the log directory cannot be written.
    }
    exit 1
}

if (Test-Path -LiteralPath $chatPidFile) {
    $chatRecord = Get-Content -LiteralPath $chatPidFile -Raw | ConvertFrom-Json
    $chatOwner = Get-Process -Id $chatRecord.id -ErrorAction SilentlyContinue
    if ($chatOwner -and $chatOwner.StartTime.ToUniversalTime().Ticks.ToString() -eq $chatRecord.started) {
        $chatProcess = $chatOwner
    }
}

if ($Stop) {
    if ($chatProcess) {
        $chatProcess.Kill()
        if (!$chatProcess.WaitForExit(5000)) { throw 'AgentChatSystem did not stop.' }
    }
    if (Test-Path -LiteralPath $chatPidFile) { Remove-Item -LiteralPath $chatPidFile }
    exit
}

# Match normal PATH lookup even when multiple Python executables or aliases exist.
$chatPythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$chatPythonCommand) { throw 'Python 3.10 or later is required on PATH.' }
$chatPython = & $chatPythonCommand.Source -c 'import sys; print(sys.executable)'
if ($LASTEXITCODE -ne 0 -or !$chatPython -or !(Test-Path -LiteralPath $chatPython)) {
    throw 'Python executable not found. Add Python 3.10 or later to PATH.'
}
if (!(Test-Path -LiteralPath $chatArchive)) {
    & (Join-Path $PSScriptRoot 'build.cmd')
    if ($LASTEXITCODE -ne 0) { throw 'Build failed. Add Python 3.10 or later to PATH.' }
}
$chatConfig = & $chatPython -B -X utf8 $chatArchive settings
if ($LASTEXITCODE -ne 0) {
    throw 'Could not load runtime settings. See the Python error above; check access to bin/internal/AgentChatSystem.pyz and bin/settings.ini.'
}
$chatConfig = $chatConfig | ConvertFrom-Json
$chatUrl = 'http://{0}:{1}' -f $chatConfig.host, $chatConfig.port

function Test-ChatOnline($chatProcessId) {
    try {
        $chatHealth = Invoke-RestMethod -Uri "$chatUrl/api/health" -TimeoutSec 1
        return ($chatHealth.service -eq 'AgentChatSystem' -and $chatHealth.pid -eq $chatProcessId -and $chatHealth.listen_host -eq $chatConfig.listen_host)
    } catch {
        return $false
    }
}

if ($chatProcess) {
    if ($chatRecord.port -ne $chatConfig.port -or !(Test-ChatOnline $chatProcess.Id)) {
        throw 'A prior instance is still running. Run bin\start.cmd -Stop, then start it again.'
    }
} else {
    New-Item -ItemType Directory -Force -Path $chatVar | Out-Null
    $chatArgs = '-X utf8 "{0}" --port {1} serve' -f $chatArchive, $chatConfig.port
    $chatProcess = Start-Process -FilePath $chatPython -ArgumentList $chatArgs -WorkingDirectory $chatRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $chatVar 'server.log') -RedirectStandardError (Join-Path $chatVar 'server-error.log')
    $chatReady = $false
    for ($chatAttempt = 0; $chatAttempt -lt 15; $chatAttempt++) {
        if ($chatProcess.HasExited) { break }
        if (Test-ChatOnline $chatProcess.Id) { $chatReady = $true; break }
        Start-Sleep -Milliseconds 200
    }
    if (!$chatReady) {
        if (!$chatProcess.HasExited) { $chatProcess.Kill() }
        throw "AgentChatSystem could not open $chatUrl. See $chatVar\server-error.log"
    }
    @{ id = $chatProcess.Id; started = $chatProcess.StartTime.ToUniversalTime().Ticks.ToString(); port = $chatConfig.port } |
        ConvertTo-Json | Set-Content -LiteralPath $chatPidFile -Encoding UTF8
}
Write-Output "$chatUrl/"
if (!$NoOpen) { Start-Process "$chatUrl/" }
