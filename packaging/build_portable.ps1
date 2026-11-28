param(
    [string]$Tools = "",
    [string]$Python = ".venv/Scripts/python.exe",
    [string]$OutputRoot = "dist",
    [switch]$Rc
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$railCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine Git commit.' }
$railDirty = @(git status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git status.' }
$railClean = ($railDirty.Count -eq 0)
$railPython = (Resolve-Path -LiteralPath $Python).Path
& $railPython -c "import sys,sysconfig; sys.exit(sysconfig.get_platform() != 'win-amd64')"
if ($LASTEXITCODE -ne 0) { throw 'Build requires Windows x64 Python.' }
$railRepo = (Get-Location).Path
$railOutput = if ([IO.Path]::IsPathRooted($OutputRoot)) { $OutputRoot } else { Join-Path $railRepo $OutputRoot }
$railOutput = [IO.Path]::GetFullPath($railOutput)
if (-not $railOutput.StartsWith($railRepo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'The output directory must be within the workspace.' }
$railDestination = Join-Path $railOutput 'RailVehicle-Lab-Beta'
if (Test-Path -LiteralPath $railDestination) { throw 'The output directory already exists. Choose another -OutputRoot.' }
$railParent = Join-Path $railRepo 'tmp'
New-Item -ItemType Directory -Force -Path $railParent | Out-Null
$railBuild = Join-Path (Resolve-Path $railParent).Path ('windows-v05-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $railBuild | Out-Null
$railBuildArgs = @('--distpath', (Join-Path $railBuild 'dist'), '--workpath', (Join-Path $railBuild 'work'), 'RailVehicleLab.spec')
if ($Tools) {
    $railTools = (Resolve-Path -LiteralPath $Tools).Path
    $railDriver = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('PyInstaller',run_name='__main__')"
    & $railPython -c $railDriver $railTools @railBuildArgs
} else {
    & $railPython -m PyInstaller @railBuildArgs
}
if ($LASTEXITCODE -ne 0) { throw "Portable build failed; inspect output above." }
$railAfter = (git rev-parse HEAD).Trim()
$railDirtyAfter = @(git status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git status after build.' }
$railClean = $railClean -and $railAfter -eq $railCommit -and $railDirtyAfter.Count -eq 0
if (-not $railClean) { Write-Warning 'Source was modified during or before the build; this build cannot be packaged as a versioned test ZIP.' }
$railBundle = Join-Path $railBuild 'dist/RailVehicle-Lab-Beta'
Copy-Item -LiteralPath 'packaging/Start-Demo.bat' -Destination $railBundle
$railMetadataArgs = @($railBundle, '--record-build', $railCommit, '--source-clean', $railClean.ToString().ToLowerInvariant())
if ($Rc) { $railMetadataArgs += '--rc' }
& $railPython 'packaging/package_portable.py' @railMetadataArgs
if ($LASTEXITCODE -ne 0) { throw 'Failed to record build metadata.' }
New-Item -ItemType Directory -Force -Path $railOutput | Out-Null
[IO.Directory]::Move($railBundle, $railDestination)
Write-Host "Portable directory: $railDestination"
