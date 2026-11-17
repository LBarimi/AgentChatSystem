param([switch]$NoOpen, [switch]$Stop)
$ErrorActionPreference = 'Stop'
$chatRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$chatArchive = Join-Path $PSScriptRoot 'AgentChatSystem.pyz'
$chatVar = Join-Path $chatRoot 'var'
$chatPidFile = Join-Path $chatVar 'server.json'
$chatProcess = $null

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

if (!(Test-Path -LiteralPath $chatArchive)) {
    & (Join-Path $PSScriptRoot 'build.cmd')
    if ($LASTEXITCODE -ne 0) { throw 'Build failed. Add Python 3.10 or later to PATH.' }
}
$chatPython = (& python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $chatPython)) { throw 'Python executable not found.' }
$chatConfig = & $chatPython -B -X utf8 $chatArchive settings
if ($LASTEXITCODE -ne 0) { throw 'Check bin/settings.ini and try again.' }
$chatConfig = $chatConfig | ConvertFrom-Json
$chatUrl = 'http://{0}:{1}' -f $chatConfig.host, $chatConfig.port

function Test-ChatOnline($chatProcessId) {
    try {
        $chatHealth = Invoke-RestMethod -Uri "$chatUrl/api/health" -TimeoutSec 1
        return ($chatHealth.service -eq 'AgentChatSystem' -and $chatHealth.pid -eq $chatProcessId)
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
