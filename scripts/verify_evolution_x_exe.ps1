param([Parameter(Mandatory=$true)][string]$Bundle,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$exe=(Resolve-Path -LiteralPath (Join-Path $Bundle 'RailVehicle-Lab.exe')).Path
$node=(Get-Command node -CommandType Application).Source
$qa=(New-Item -ItemType Directory -Path $Output).FullName
$env:LOCALAPPDATA=Join-Path $qa 'isolated'
$env:AI_ENABLED='false'
$env:NODE_PATH=(Resolve-Path -LiteralPath (Join-Path $root 'tmp/browser-tools/node_modules')).Path
$env:RAIL_BROWSER_PATH='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
$env:PYTHONHOME=$null
$env:PYTHONPATH=$null
$env:Path=Join-Path $env:SystemRoot 'System32'
& $exe --check
if($LASTEXITCODE){throw 'Portable diagnostic failed.'}

function Test-PackagedSession([string]$Stage,[string[]]$Extra) {
    $port=8519
    while(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue){ $port++ }
    $address="http://127.0.0.1:$port"
    $process=Start-Process -FilePath $exe -ArgumentList @('--port',$port,'--no-browser') -WorkingDirectory (Split-Path $exe) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $qa "$Stage.out.log") -RedirectStandardError (Join-Path $qa "$Stage.err.log")
    try {
        $until=(Get-Date).AddSeconds(55)
        $healthy=$false
        while(!( $process.HasExited ) -and (Get-Date) -lt $until) {
            try { $healthy=(Invoke-WebRequest -UseBasicParsing "$address/_stcore/health" -TimeoutSec 2).Content.Trim() -eq 'ok' } catch { $healthy=$false }
            if($healthy){ break }
            Start-Sleep -Milliseconds 500
        }
        if(!$healthy){ throw "Portable service did not pass health verification for $Stage." }
        & $node (Join-Path $PSScriptRoot 'check_evolution_x_browser.cjs') $address (Join-Path $qa "$Stage-edge") @Extra
        if($LASTEXITCODE){ throw "Edge acceptance failed for $Stage ($LASTEXITCODE)." }
    } finally {
        if(!$process.HasExited){ Stop-Process -Id $process.Id; $process.WaitForExit() }
    }
}

Test-PackagedSession 'fresh' @()
Test-PackagedSession 'restart' @('--verify-restart')
