# Start the whole Mizan stack in Docker and open it in the browser.
# PowerShell equivalent of run_mizan.py:
#   powershell -ExecutionPolicy Bypass -File infra/start.ps1
# Needs Docker Desktop running. The first start builds the images and downloads the embedding
# model (about 2 GB) into a Docker volume, so it takes several minutes; later starts are quick.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example: add your Groq key (MIZAN_LLM__GROQ_API_KEY) for answers."
}

Write-Host "Building and starting the stack (docker compose up -d --build api)..."
# docker writes progress to stderr; under a redirected host PowerShell 5.1 would treat it as an error.
$ErrorActionPreference = "Continue"
docker compose up -d --build api
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) { throw "docker compose failed. Is Docker Desktop running?" }

$port = if ($env:MIZAN_HOST_PORT_API) { $env:MIZAN_HOST_PORT_API } else { "18000" }
$base = "http://127.0.0.1:$port"
Write-Host "Waiting for the API to load its models ($base/health/ready)..."
$deadline = (Get-Date).AddMinutes(30)
while ($true) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$base/health/ready"
        if ($r.StatusCode -eq 200) { break }
    } catch { }
    if ((Get-Date) -gt $deadline) {
        throw "The API did not become ready in 30 minutes. See: docker compose logs api"
    }
    Start-Sleep -Seconds 5
}

Write-Host "Mizan is ready."
Write-Host "  Ask a question: $base/"
Write-Host "  Contract check: $base/contracts"
Write-Host "  API reference:  $base/docs"
Start-Process "$base/"
Write-Host "Stop with: docker compose stop api"
