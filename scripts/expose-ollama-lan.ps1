# Run ONCE on the desktop that hosts Ollama/Gemma.
# Default Ollama binds to 127.0.0.1, so the laptop cannot reach it.
# Requires an elevated PowerShell.

$ErrorActionPreference = "Stop"

[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
Write-Host "Set machine env OLLAMA_HOST=0.0.0.0:11434"

$rule = Get-NetFirewallRule -DisplayName "Ollama LAN" -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -DisplayName "Ollama LAN" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow | Out-Null
    Write-Host "Opened Windows Firewall TCP 11434"
} else {
    Write-Host "Firewall rule already exists"
}

$svc = Get-Service -Name "Ollama" -ErrorAction SilentlyContinue
if ($svc) {
    Restart-Service -Name "Ollama" -Force
    Write-Host "Restarted Ollama service"
} else {
    Get-Process -Name "ollama*" -ErrorAction SilentlyContinue | Stop-Process -Force
    $exe = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:ProgramFiles\Ollama\ollama.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($exe) {
        Start-Process $exe -ArgumentList "serve"
        Write-Host "Started $exe serve"
    } else {
        Write-Host "Ollama process/service not found. Quit and reopen the Ollama app."
    }
}

Write-Host ""
Write-Host "From the laptop, test:"
Write-Host "  curl http://<IP-deste-desktop>:11434/api/tags"
Write-Host "  local-worker status"
