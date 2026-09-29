param(
    [string]$InstanceId = "ocid1.instance.oc1.ca-toronto-1.an2g6ljrooq74kqckwx6ktylj4cvnhkeszdkc6kcofp4x47zav2nw6t7zvsa",
    [string]$Region = "ca-toronto-1",
    [string]$OciProfile = "",
    [string]$ConfigPath = "$env:USERPROFILE\.oci\config",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-vm-soft-reboot.log"
)

$ErrorActionPreference = "Stop"
$tempConfig = ""

function Redact([string]$Value) {
    $Value = $Value -replace 'ocid1\.[A-Za-z0-9._-]+', '[OCID_REDACTED]'
    $Value = $Value -replace '[A-Fa-f0-9]{2}(:[A-Fa-f0-9]{2}){15,}', '[FINGERPRINT_REDACTED]'
    $Value = $Value -replace 'C:\\Users\\Martin\\[^\s\r\n]+', '[PATH_REDACTED]'
    return $Value
}

function Write-Log([string]$Value) {
    $line = Redact $Value
    Add-Content -LiteralPath $LogPath -Value $line
    Write-Output $line
}

function Read-Passphrase {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $cred = Get-Credential -UserName "oci-key" -Message "Ingresa la passphrase de la llave OCI en el campo Password"
        if (-not $cred) {
            Write-Log "PASSPHRASE_DIALOG_CANCELLED=$attempt"
            continue
        }
        $plain = $cred.GetNetworkCredential().Password
        if (-not [string]::IsNullOrEmpty($plain)) {
            return $plain
        }
        Write-Log "PASSPHRASE_EMPTY_RETRY=$attempt"
    }
    throw "La passphrase OCI esta vacia."
}

function New-TemporaryConfig([string]$Passphrase) {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw "No existe OCI config local."
    }

    $profileName = if ([string]::IsNullOrWhiteSpace($OciProfile)) { "DEFAULT" } else { $OciProfile }
    $target = Join-Path $env:TEMP ("oci-config-soft-reboot-" + [guid]::NewGuid().ToString("N"))
    $lines = Get-Content -LiteralPath $ConfigPath
    $out = New-Object System.Collections.Generic.List[string]
    $inProfile = $false
    $profileSeen = $false
    $passphraseWritten = $false

    foreach ($line in $lines) {
        if ($line.Trim() -match '^\[(.+)\]$') {
            if ($inProfile -and -not $passphraseWritten) {
                $out.Add("pass_phrase=$Passphrase")
                $passphraseWritten = $true
            }
            $inProfile = ($matches[1] -eq $profileName)
            if ($inProfile) {
                $profileSeen = $true
            }
        }

        if ($inProfile -and $line.Trim() -match '^pass_phrase\s*=') {
            if (-not $passphraseWritten) {
                $out.Add("pass_phrase=$Passphrase")
                $passphraseWritten = $true
            }
            continue
        }

        $out.Add($line)
    }

    if (-not $profileSeen) {
        throw "No se encontro el perfil OCI '$profileName'."
    }
    if ($inProfile -and -not $passphraseWritten) {
        $out.Add("pass_phrase=$Passphrase")
    }

    Set-Content -LiteralPath $target -Value $out -Encoding utf8
    return $target
}

try {
    Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
    Write-Log "START $(Get-Date -Format o)"
    Write-Log "ACTION=SOFTRESET"
    Write-Log "REGION=$Region"
    Write-Log ("OCI_PROFILE=" + $(if ([string]::IsNullOrWhiteSpace($OciProfile)) { "(default)" } else { $OciProfile }))

    $passphrase = Read-Passphrase
    $tempConfig = New-TemporaryConfig $passphrase
    Write-Log "TEMP_CONFIG_CREATED=true"

    $globalArgs = @("--config-file", $tempConfig, "--region", $Region)
    if (-not [string]::IsNullOrWhiteSpace($OciProfile)) {
        $globalArgs += @("--profile", $OciProfile)
    }

    Write-Log "OCI_STEP=softreset_instance"
    $raw = & oci @globalArgs compute instance action --action SOFTRESET --instance-id $InstanceId 2>&1
    $exitCode = $LASTEXITCODE
    $raw | ForEach-Object { Write-Log ([string]$_) }
    Write-Log "OCI_STEP_EXIT=$exitCode"
    if ($exitCode -ne 0) {
        throw "OCI soft reset failed"
    }

    Write-Log "END $(Get-Date -Format o)"
    Write-Log "exit=0"
    exit 0
} finally {
    if ($tempConfig) {
        Remove-Item -LiteralPath $tempConfig -Force -ErrorAction SilentlyContinue
    }
}
