$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Stamp = "2026-05-11"
$Stage = Join-Path $Root "diagnostic_bundle_$Stamp"
$Zip = Join-Path $Root "resumen_escolar_diagnostico_llm_$Stamp.zip"

if (Test-Path -LiteralPath $Stage) {
    Remove-Item -LiteralPath $Stage -Recurse -Force
}
if (Test-Path -LiteralPath $Zip) {
    Remove-Item -LiteralPath $Zip -Force
}

New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "resumen_escolar") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "outbox\2026-05-08") | Out-Null

Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $Root "requirements.txt") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $Root "install_deps.cmd") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $Root "run_resumen_escolar.cmd") -Destination $Stage -Force

Copy-Item -LiteralPath (Join-Path $Root "resumen_escolar\app.py") -Destination (Join-Path $Stage "resumen_escolar") -Force
Copy-Item -LiteralPath (Join-Path $Root "resumen_escolar\__init__.py") -Destination (Join-Path $Stage "resumen_escolar") -Force
Copy-Item -LiteralPath (Join-Path $Root "resumen_escolar\__main__.py") -Destination (Join-Path $Stage "resumen_escolar") -Force

$PromptPath = Join-Path $Root "outbox\2026-05-08\prompt_chatgpt.txt"
if (Test-Path -LiteralPath $PromptPath) {
    Copy-Item -LiteralPath $PromptPath -Destination (Join-Path $Stage "outbox\2026-05-08") -Force
}

Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip -Force

Write-Host "ZIP creado:"
Write-Host $Zip
Get-Item -LiteralPath $Zip | Format-List FullName,Length,LastWriteTime
