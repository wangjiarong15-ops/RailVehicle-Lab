param([string]$Tools = "tmp/pdfs/portable-build-tools")
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$railCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine Git commit.' }
$railDirty = @(git status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git status.' }
$railClean = ($railDirty.Count -eq 0)
$railPython = (Resolve-Path '.venv/Scripts/python.exe').Path
$railTools = (Resolve-Path $Tools).Path
$railParent = Join-Path (Get-Location).Path 'tmp/pdfs'
New-Item -ItemType Directory -Force -Path $railParent | Out-Null
$railBuild = Join-Path (Resolve-Path $railParent).Path ('portable-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $railBuild | Out-Null
$railDriver = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("PyInstaller",run_name="__main__")'
& $railPython -c $railDriver $railTools --distpath (Join-Path $railBuild 'dist') --workpath (Join-Path $railBuild 'work') 'RailVehicleLab.spec'
if ($LASTEXITCODE -ne 0) { throw "Portable build failed; inspect output above." }
$railAfter = (git rev-parse HEAD).Trim()
$railDirtyAfter = @(git status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git status after build.' }
$railClean = $railClean -and $railAfter -eq $railCommit -and $railDirtyAfter.Count -eq 0
if (-not $railClean) { Write-Warning 'Source was modified during or before the build; this build cannot be packaged as a versioned test ZIP.' }
Copy-Item -LiteralPath 'packaging/Start-Demo.bat' -Destination (Join-Path $railBuild 'dist/RailVehicleLab/Start-Demo.bat')
Copy-Item -LiteralPath 'packaging/PORTABLE_README.txt' -Destination (Join-Path $railBuild 'dist/RailVehicleLab/README.txt')
$railBundle = Join-Path $railBuild 'dist/RailVehicleLab'
$railExeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $railBundle 'RailVehicleLab.exe')).Hash.ToLowerInvariant()
@{ source_commit = $railCommit; exe_sha256 = $railExeHash; source_clean = $railClean } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $railBundle 'BUILD-METADATA.json')
Write-Host "Portable directory: $(Join-Path $railBuild 'dist/RailVehicleLab')"
