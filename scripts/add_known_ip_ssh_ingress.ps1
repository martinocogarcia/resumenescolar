param(
    [string]$InstanceId = "ocid1.instance.oc1.ca-toronto-1.an2g6ljrooq74kqckwx6ktylj4cvnhkeszdkc6kcofp4x47zav2nw6t7zvsa",
    [string]$SourceCidr = "181.43.240.194/32",
    [string]$OciProfile = "codex",
    [string]$OciRegion = "ca-toronto-1",
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-add-ssh-ingress.log"
)

$ErrorActionPreference = "Stop"
$tempRulesPath = $null

function Write-Redacted([string]$Value) {
    $Value = $Value -replace 'ocid1\.[A-Za-z0-9._-]+', '[OCID_REDACTED]'
    $Value = $Value -replace 'C:\\Users\\Martin\\[^\s\r\n]+', '[PATH_REDACTED]'
    Write-Output $Value
}

function Invoke-OciJson([string[]]$ArgsList) {
    $globalArgs = @('--region', $OciRegion, '--connection-timeout', '8', '--read-timeout', '20', '--no-retry')
    if ($OciProfile) { $globalArgs += @('--profile', $OciProfile) }
    $raw = & oci @globalArgs @ArgsList 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "OCI command failed: $($ArgsList -join ' ')`n$($raw | Out-String)"
    }
    return ($raw | Out-String | ConvertFrom-Json)
}

$scriptExitCode = 0
try {
& {
    Write-Redacted ("START {0}" -f (Get-Date -Format o))
    Write-Redacted "OCI_PROFILE=$OciProfile"
    Write-Redacted "OCI_REGION=$OciRegion"
    Write-Redacted "SOURCE_CIDR=$SourceCidr"

    if ($SourceCidr -notmatch '^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)/32$') {
        throw 'SourceCidr must be a single IPv4 address with /32.'
    }
    if (-not $env:OCI_CLI_PASSPHRASE -and -not $env:OCI_CLI_KEY_PASSPHRASE) {
        $secure = Read-Host 'OCI key passphrase (Enter si no aplica; no se guarda ni se imprime)' -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $passphrase = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if ($passphrase) {
                $env:OCI_CLI_PASSPHRASE = $passphrase
                $env:OCI_CLI_KEY_PASSPHRASE = $passphrase
                $env:OCI_CLI_PASS_PHRASE = $passphrase
            }
        } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    }

    Write-Redacted 'STAGE=resolve_vnic'
    $instance = Invoke-OciJson @('compute', 'instance', 'get', '--instance-id', $InstanceId)
    $attachments = Invoke-OciJson @('compute', 'vnic-attachment', 'list', '--compartment-id', $instance.data.'compartment-id', '--instance-id', $InstanceId, '--all')
    $attachment = @($attachments.data) | Select-Object -First 1
    if (-not $attachment) { throw 'No VNIC attachment found for the instance.' }
    $vnic = Invoke-OciJson @('network', 'vnic', 'get', '--vnic-id', $attachment.'vnic-id')
    $subnet = Invoke-OciJson @('network', 'subnet', 'get', '--subnet-id', $vnic.data.'subnet-id')
    $securityListIds = @($subnet.data.'security-list-ids') | Where-Object { $_ }
    if ($securityListIds.Count -eq 0) { throw 'The subnet has no security lists.' }

    foreach ($securityListId in $securityListIds) {
        Write-Redacted 'STAGE=inspect_security_list'
        $securityList = Invoke-OciJson @('network', 'security-list', 'get', '--security-list-id', $securityListId)
        $rules = @($securityList.data.'ingress-security-rules')
        $alreadyAllowed = $rules | Where-Object {
            $_.source -eq $SourceCidr -and $_.protocol -eq '6' -and
            $_.'tcp-options'.'destination-port-range'.min -le 22 -and $_.'tcp-options'.'destination-port-range'.max -ge 22
        }
        if ($alreadyAllowed) {
            Write-Redacted 'SSH_RULE_ALREADY_PRESENT=true'
            continue
        }
        $newRule = [ordered]@{
            protocol = '6'
            source = $SourceCidr
            'source-type' = 'CIDR_BLOCK'
            'is-stateless' = $false
            'tcp-options' = [ordered]@{ 'destination-port-range' = [ordered]@{ min = 22; max = 22 } }
            description = 'Temporary SSH access for Classroom login'
        }
        $updatedRules = @($rules) + [pscustomobject]$newRule
        $tempRulesPath = Join-Path $env:TEMP ("oci-ingress-rules-{0}.json" -f ([guid]::NewGuid().ToString('N')))
        [System.IO.File]::WriteAllText($tempRulesPath, ($updatedRules | ConvertTo-Json -Depth 12), [System.Text.UTF8Encoding]::new($false))
        Write-Redacted 'STAGE=update_security_list'
        $null = Invoke-OciJson @('network', 'security-list', 'update', '--security-list-id', $securityListId, '--ingress-security-rules', ("file://{0}" -f $tempRulesPath), '--force')
        Write-Redacted 'SSH_RULE_ADDED=true'
    }
    Write-Redacted ("END {0}" -f (Get-Date -Format o))
} *> $LogPath
} catch {
    $scriptExitCode = 1
} finally {
    if ($tempRulesPath -and (Test-Path -LiteralPath $tempRulesPath)) { Remove-Item -LiteralPath $tempRulesPath -Force }
    Remove-Item Env:OCI_CLI_PASSPHRASE -ErrorAction SilentlyContinue
    Remove-Item Env:OCI_CLI_KEY_PASSPHRASE -ErrorAction SilentlyContinue
    Remove-Item Env:OCI_CLI_PASS_PHRASE -ErrorAction SilentlyContinue
}

exit $scriptExitCode
