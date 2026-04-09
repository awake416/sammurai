# Usage: .\scripts\inject-secrets.ps1 -Action up
# Prerequisite: Install-Module CredentialManager -Scope CurrentUser
# Supported Actions: up, down, build, logs, restart
# This script retrieves secrets from Windows Credential Manager or DPAPI and injects them into Docker Compose.

param (
    [Parameter(Mandatory=$false)]
    [ValidateSet('up', 'down', 'build', 'logs', 'restart')]
    [string]$Action = 'up'
)

$ErrorActionPreference = 'Stop'
$SecretsPath = Join-Path $env:APPDATA "sammurai\secrets.dpapi"

function Set-VaultSecret {
    param ([string]$Target, [string]$Value)
    
    # 1. Try CredentialManager Module
    if (Get-Module -ListAvailable CredentialManager) {
        Import-Module CredentialManager -ErrorAction SilentlyContinue
        Set-StoredCredential -Target $Target -UserName "sammurai" -Password $Value -Persist LocalMachine
    }
    
    # 2. Fallback to cmdkey
    cmdkey /add:$Target /user:sammurai /pass:$Value

    # 3. Sync to DPAPI file for redundancy
    if (-not (Test-Path (Split-Path $SecretsPath))) { New-Item -ItemType Directory -Path (Split-Path $SecretsPath) -Force }
    
    $existing = @{}
    if (Test-Path $SecretsPath) {
        try {
            $bytes = [System.IO.File]::ReadAllBytes($SecretsPath)
            $unprotected = [System.Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
            $existing = [System.Text.Encoding]::UTF8.GetString($unprotected) | ConvertFrom-Json -AsHashtable
        } catch {}
    }
    
    $existing[$Target] = $Value
    $json = $existing | ConvertTo-Json
    $data = [System.Text.Encoding]::UTF8.GetBytes($json)
    $protected = [System.Security.Cryptography.ProtectedData]::Protect($data, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    [System.IO.File]::WriteAllBytes($SecretsPath, $protected)
}

function Get-VaultSecret {
    param ([string]$Target)
    
    # 1. Try CredentialManager Module
    if (Get-Module -ListAvailable CredentialManager) {
        Import-Module CredentialManager -ErrorAction SilentlyContinue
        $creds = Get-StoredCredential -Target $Target -ErrorAction SilentlyContinue
        if ($creds) {
            return $creds.GetNetworkCredential().Password
        }
    }

    # 2. Try DPAPI Fallback
    if (Test-Path $SecretsPath) {
        try {
            $bytes = [System.IO.File]::ReadAllBytes($SecretsPath)
            $unprotected = [System.Security.Cryptography.ProtectedData]::Unprotect($bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
            $secrets = [System.Text.Encoding]::UTF8.GetString($unprotected) | ConvertFrom-Json
            if ($secrets.$Target) { return $secrets.$Target }
        } catch {}
    }

    # 3. Prompt User and Save
    Write-Host "Secret not found for $Target" -ForegroundColor Yellow
    $val = Read-Host -Prompt "Enter value for $Target" -AsSecureString
    $plainVal = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($val))
    
    if ($plainVal) {
        Set-VaultSecret -Target $Target -Value $plainVal
        return $plainVal
    }
    return $null
}

# Main Logic
$SecretMap = @{
    "WHATSAPP_TOKEN"           = "sammurai_whatsapp_token"
    "WHATSAPP_PHONE_NUMBER_ID" = "sammurai_whatsapp_phone_id"
    "WHATSAPP_VERIFY_TOKEN"    = "sammurai_whatsapp_verify_token"
    "WHATSAPP_API_KEY"         = "sammurai_whatsapp_api_key"
}

foreach ($envVar in $SecretMap.Keys) {
    $val = Get-VaultSecret -Target $SecretMap[$envVar]
    if (-not $val) {
        Write-Error "Required secret $envVar is missing. Aborting."
        exit 1
    }
    [System.Environment]::SetEnvironmentVariable($envVar, $val, "Process")
}

Write-Host "Executing: docker compose $Action" -ForegroundColor Cyan
$dockerArgs = if ($Action -eq 'up') { @('up', '-d') } else { @($Action) }

$process = Start-Process docker -ArgumentList ( @('compose') + $dockerArgs ) -Wait -PassThru -NoNewWindow
if ($process.ExitCode -ne 0) {
    Write-Error "Docker compose failed with exit code $($process.ExitCode)"
    exit $process.ExitCode
}

if ($Action -eq 'up') {
    Write-Host "Verifying health check..." -ForegroundColor Gray
    Start-Sleep -Seconds 5
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8080/health" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Write-Host "Stack is healthy (HTTP 200)" -ForegroundColor Green
        }
    } catch {
        Write-Warning "Health check failed or endpoint not ready yet."
    }
}