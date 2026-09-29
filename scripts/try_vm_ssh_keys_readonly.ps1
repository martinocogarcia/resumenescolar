param(
    [string]$HostName = "129.153.51.108",
    [string]$User = "ubuntu",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-vm-key-try.log"
)

$ErrorActionPreference = "Continue"

$candidates = @(
    "C:\Users\Martin\.ssh\id_ed25519",
    "C:\Users\Martin\.ssh\id_rsa",
    "C:\Users\Martin\Documents\New project 3\.oci-deploy\worldcup2026_ssh",
    "C:\Users\Martin\Desktop\proyectos MG\oci_api_key_nopass_20260420_210527.pem"
) | Where-Object { Test-Path -LiteralPath $_ }

$sshBaseArgs = @(
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=8",
    "-o", "UserKnownHostsFile=$env:TEMP\codex_known_hosts_resumen_escolar",
    "-o", "StrictHostKeyChecking=accept-new"
)

$remote = @'
printf "host="; hostname
printf "user="; id -un
printf "pwd="; pwd
printf "os="; uname -srm
printf "python="; python3 --version 2>&1 || true
printf "chrome="; command -v google-chrome || command -v chromium-browser || command -v chromium || true
printf "oci="; oci --version 2>&1 || true
printf "docker="; docker --version 2>&1 || true
printf "cron="; systemctl is-active cron 2>&1 || systemctl is-active crond 2>&1 || true
'@

& {
    "START $(Get-Date -Format o)"
    "TARGET=$User@$HostName"
    "CANDIDATE_COUNT=$($candidates.Count)"

    foreach ($keyPath in $candidates) {
        "TRY_KEY=$keyPath"
        $output = & ssh @sshBaseArgs -i $keyPath "$User@$HostName" $remote 2>&1
        "TRY_EXIT=$LASTEXITCODE"
        if ($LASTEXITCODE -eq 0) {
            "SSH_OK_KEY=$keyPath"
            $output
            "END $(Get-Date -Format o)"
            exit 0
        }
        ($output | Select-Object -First 5)
    }

    "SSH_OK_KEY="
    "END $(Get-Date -Format o)"
    exit 1
} *> $LogPath

exit $LASTEXITCODE
