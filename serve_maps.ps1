param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$outputPath = Join-Path $PSScriptRoot 'output'
Write-Host "Maps: http://127.0.0.1:$Port/jev_2026-09-01/index.html"
Write-Host 'Press Ctrl+C to stop the local server.'
& $pythonPath -m http.server $Port --bind 127.0.0.1 --directory $outputPath
