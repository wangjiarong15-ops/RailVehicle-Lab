param([string]$Tools = "tmp/pdfs/portable-build-tools")
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$railPython = (Resolve-Path '.venv/Scripts/python.exe').Path
$railTools = (Resolve-Path $Tools).Path
$railParent = Join-Path (Get-Location).Path 'tmp/pdfs'
New-Item -ItemType Directory -Force -Path $railParent | Out-Null
$railBuild = Join-Path (Resolve-Path $railParent).Path ('portable-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $railBuild | Out-Null
$railDriver = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("PyInstaller",run_name="__main__")'
& $railPython -c $railDriver $railTools --distpath (Join-Path $railBuild 'dist') --workpath (Join-Path $railBuild 'work') 'RailVehicleLab.spec'
if ($LASTEXITCODE -ne 0) { throw "Portable build failed; inspect output above." }
Copy-Item -LiteralPath 'packaging/Start-Demo.bat' -Destination (Join-Path $railBuild 'dist/RailVehicleLab/Start-Demo.bat')
Copy-Item -LiteralPath 'packaging/PORTABLE_README.txt' -Destination (Join-Path $railBuild 'dist/RailVehicleLab/README.txt')
Write-Host "Portable directory: $(Join-Path $railBuild 'dist/RailVehicleLab')"
