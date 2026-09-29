param(
    [string]$Email = "martin.g.garcia@oracle.com",
    [string]$TopicName = "resumen-escolar-gabitin-updates",
    [string]$PolicyName = "resumen_escolar_gabitin_notifications_publish",
    [string]$DynamicGroupName = "resumen_escolar_gabitin_publishers",
    [string]$OciProfile = "",
    [string]$OciRegion = "ca-toronto-1",
    [string]$VmHost = "129.153.51.108",
    [string]$VmUser = "ubuntu",
    [string]$KeyPath = "C:\Users\Martin\Desktop\oci_keys\oracle_form_oci_20260511",
    [string]$RemoteAppDir = "/opt/resumen-escolar",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
$Script:TempOciConfigFile = ""
$Script:TempFiles = @()

if (-not $LogPath) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
    $LogPath = Join-Path $ProjectRoot "codex-setup-oci-notifications.log"
}

function Redact-Text([string]$Text) {
    if ($null -eq $Text) { return "" }
    return ($Text -replace "ocid1\.[A-Za-z0-9._-]+", "[OCID_REDACTED]")
}

function Add-Log([string]$Text) {
    Add-Content -LiteralPath $LogPath -Value (Redact-Text $Text)
}

function Step([string]$Text) {
    Write-Host $Text
    Add-Log $Text
}

function Read-OciConfigValue([string]$Name) {
    $configPath = Join-Path $env:USERPROFILE ".oci\config"
    if (-not (Test-Path -LiteralPath $configPath)) {
        return ""
    }
    $targetProfile = if ($OciProfile) { $OciProfile } else { "DEFAULT" }
    $currentProfile = ""
    foreach ($line in Get-Content -LiteralPath $configPath) {
        $trimmed = $line.Trim()
        if ($trimmed -match "^\[(.+)\]$") {
            $currentProfile = $Matches[1]
            continue
        }
        if ($currentProfile -ne $targetProfile) {
            continue
        }
        if ($trimmed -match "^\s*$([regex]::Escape($Name))\s*=\s*(.+?)\s*$") {
            return $Matches[1].Trim()
        }
    }
    return ""
}

function Get-SelectedOciKeyInfo {
    $configPath = Join-Path $env:USERPROFILE ".oci\config"
    $targetProfile = if ($OciProfile) { $OciProfile } else { "DEFAULT" }
    $currentProfile = ""
    $keyFile = ""
    $passPhrasePresent = $false
    $passPhraseValue = ""
    foreach ($line in Get-Content -LiteralPath $configPath) {
        $trimmed = $line.Trim()
        if ($trimmed -match "^\[(.+)\]$") {
            $currentProfile = $Matches[1]
            continue
        }
        if ($currentProfile -ne $targetProfile) {
            continue
        }
        if ($trimmed -match "^\s*key_file\s*=\s*(.+?)\s*$") {
            $keyFile = $Matches[1].Trim()
        }
        elseif ($trimmed -match "^\s*pass_phrase\s*=\s*(.*?)\s*$") {
            $passPhrasePresent = $true
            $passPhraseValue = $Matches[1].Trim()
        }
    }

    $expandedKeyFile = [Environment]::ExpandEnvironmentVariables($keyFile)
    if ($expandedKeyFile.StartsWith("~")) {
        $expandedKeyFile = Join-Path $env:USERPROFILE $expandedKeyFile.Substring(1).TrimStart("\", "/")
    }
    $looksEncrypted = $false
    if ($expandedKeyFile -and (Test-Path -LiteralPath $expandedKeyFile)) {
        $keyHead = Get-Content -LiteralPath $expandedKeyFile -TotalCount 5
        $looksEncrypted = (($keyHead -join "`n") -match "ENCRYPTED" -or ($keyHead -join "`n") -match "Proc-Type:\s*4,ENCRYPTED")
    }

    return @{
        keyFile = $expandedKeyFile
        keyFileBasename = if ($expandedKeyFile) { Split-Path -Leaf $expandedKeyFile } else { "" }
        looksEncrypted = [bool]$looksEncrypted
        passPhrasePresent = [bool]$passPhrasePresent
        passPhraseValue = $passPhraseValue
    }
}

function Ensure-OciPassphrase {
    $keyInfo = Get-SelectedOciKeyInfo
    Add-Log "OCI_KEY_FILE_BASENAME=$($keyInfo.keyFileBasename)"
    Add-Log "OCI_KEY_LOOKS_ENCRYPTED=$([int]$keyInfo.looksEncrypted)"
    if (-not $keyInfo.looksEncrypted) {
        Add-Log "OCI_PASSPHRASE_REQUIRED=0"
        return
    }
    Add-Log "OCI_PASSPHRASE_REQUIRED=1"

    $plain = $env:OCI_CLI_PASSPHRASE
    if ([string]::IsNullOrWhiteSpace($plain) -and $keyInfo.passPhrasePresent -and -not [string]::IsNullOrWhiteSpace($keyInfo.passPhraseValue)) {
        $plain = $keyInfo.passPhraseValue
        $env:OCI_CLI_PASSPHRASE = $plain
        $env:OCI_CLI_KEY_PASSPHRASE = $plain
        $env:OCI_CLI_PASS_PHRASE = $plain
        Add-Log "OCI_PASSPHRASE_FROM_PROFILE=1"
    }
    if ([string]::IsNullOrWhiteSpace($plain)) {
        $secure = Read-Host "OCI key passphrase (no se guarda ni se imprime; obligatorio para esta llave)" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            if ([string]::IsNullOrWhiteSpace($plain)) {
                throw "La passphrase OCI esta vacia. Ingresa la passphrase real en el primer prompt del script."
            }
            if (-not [string]::IsNullOrEmpty($plain)) {
                $env:OCI_CLI_PASSPHRASE = $plain
                $env:OCI_CLI_KEY_PASSPHRASE = $plain
                $env:OCI_CLI_PASS_PHRASE = $plain
                Add-Log "OCI_PASSPHRASE_PROVIDED=1"
            }
        }
        finally {
            if ($bstr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($plain)) {
        Ensure-TempOciConfigWithPassphrase $plain
    }
}

function Ensure-TempOciConfigWithPassphrase([string]$Passphrase) {
    $configPath = Join-Path $env:USERPROFILE ".oci\config"
    if (-not (Test-Path -LiteralPath $configPath)) {
        return
    }

    $targetProfile = if ($OciProfile) { $OciProfile } else { "DEFAULT" }
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in Get-Content -LiteralPath $configPath) {
        $lines.Add($line)
    }

    $start = -1
    $end = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim() -match "^\[(.+)\]$") {
            if ($Matches[1] -eq $targetProfile) {
                $start = $i
                continue
            }
            if ($start -ge 0) {
                $end = $i
                break
            }
        }
    }
    if ($start -lt 0) {
        return
    }

    $inserted = $false
    for ($i = $start + 1; $i -lt $end; $i++) {
        if ($lines[$i].Trim() -match "^pass_phrase\s*=") {
            $lines[$i] = "pass_phrase=$Passphrase"
            $inserted = $true
            break
        }
    }
    if (-not $inserted) {
        $lines.Insert($end, "pass_phrase=$Passphrase")
    }

    $tempPath = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "oci-codex-notifications-$([Guid]::NewGuid().ToString('N')).config")
    Set-Content -LiteralPath $tempPath -Value $lines -Encoding UTF8
    $Script:TempOciConfigFile = $tempPath
    Add-Log "TEMP_OCI_CONFIG_CREATED=1"
}

function Oci-Args([string[]]$CommandArgs) {
    $common = @()
    if ($Script:TempOciConfigFile) { $common += @("--config-file", $Script:TempOciConfigFile) }
    if ($OciProfile) { $common += @("--profile", $OciProfile) }
    if ($OciRegion) { $common += @("--region", $OciRegion) }
    $common += $CommandArgs
    return $common
}

function Invoke-OciJson([string[]]$CommandArgs, [string]$Name) {
    $cmdArgs = @("--output", "json") + (Oci-Args $CommandArgs)
    Add-Log ("OCI $Name CMD=oci " + (($cmdArgs -join " ") -replace "ocid1\.[A-Za-z0-9._-]+", "[OCID_REDACTED]"))
    Add-Log "OCI $Name START"
    $output = & oci @cmdArgs 2>&1
    $exit = $LASTEXITCODE
    Add-Log "OCI $Name EXIT=$exit"
    if ($exit -ne 0) {
        Add-Log (($output | Out-String).Trim())
        throw "OCI command failed: $Name"
    }
    $jsonText = ($output | Out-String).Trim()
    Add-Log "OCI $Name OK"
    if ([string]::IsNullOrWhiteSpace($jsonText) -and $Name.StartsWith("list ")) {
        Add-Log "OCI $Name EMPTY_LIST_OUTPUT=1"
        return [pscustomobject]@{ data = @() }
    }
    $firstJson = $jsonText.IndexOf("{")
    $firstArray = $jsonText.IndexOf("[")
    $first = -1
    if ($firstJson -ge 0 -and $firstArray -ge 0) {
        $first = [Math]::Min($firstJson, $firstArray)
    }
    elseif ($firstJson -ge 0) {
        $first = $firstJson
    }
    elseif ($firstArray -ge 0) {
        $first = $firstArray
    }
    if ($first -lt 0) {
        Add-Log "OCI $Name returned non-JSON output:"
        Add-Log (($jsonText -split "`r?`n" | Select-Object -First 60) -join "`n")
        throw "OCI command did not return JSON: $Name"
    }
    try {
        return $jsonText.Substring($first) | ConvertFrom-Json
    }
    catch {
        Add-Log "OCI $Name JSON parse failed. Output head:"
        Add-Log (($jsonText -split "`r?`n" | Select-Object -First 60) -join "`n")
        throw
    }
}

function Invoke-OciNoJson([string[]]$CommandArgs, [string]$Name) {
    $cmdArgs = Oci-Args $CommandArgs
    Add-Log "OCI $Name START"
    $output = & oci @cmdArgs 2>&1
    $exit = $LASTEXITCODE
    Add-Log "OCI $Name EXIT=$exit"
    if ($exit -ne 0) {
        Add-Log (($output | Out-String).Trim())
        throw "OCI command failed: $Name"
    }
    Add-Log "OCI $Name OK"
    return ($output | Out-String)
}

function New-TempCommandJsonFile($Value) {
    $tempPath = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "oci-codex-json-$([Guid]::NewGuid().ToString('N')).json")
    $json = ConvertTo-Json $Value -Compress -Depth 20
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($tempPath, $json, $utf8NoBom)
    $Script:TempFiles += $tempPath
    Add-Log "TEMP_COMMAND_JSON_CREATED=1"
    return "file://$tempPath"
}

function Get-TopicId($Topic) {
    if ($Topic.PSObject.Properties.Name -contains "topic-id") { return $Topic."topic-id" }
    if ($Topic.PSObject.Properties.Name -contains "id") { return $Topic.id }
    return ""
}

function Ensure-Policy([string]$TenancyId) {
    $statement = "Allow dynamic-group $DynamicGroupName to use ons-topics in tenancy"
    $policies = Invoke-OciJson @("iam", "policy", "list", "--compartment-id", $TenancyId, "--all") "list policies"
    $existing = @($policies.data | Where-Object { $_.name -eq $PolicyName -and $_."lifecycle-state" -ne "DELETED" }) | Select-Object -First 1
    if (-not $existing) {
        $createJson = New-TempCommandJsonFile @{
            compartmentId = $TenancyId
            name = $PolicyName
            description = "Permite a la VM de Resumen Escolar publicar notificaciones de actualizacion."
            statements = [string[]]@($statement)
        }
        Invoke-OciJson @("iam", "policy", "create", "--from-json", $createJson) "create notification policy" | Out-Null
        return "created"
    }

    $currentStatements = @($existing.statements)
    if ($currentStatements -contains $statement) {
        return "already-present"
    }
    $updatedStatements = @($currentStatements + $statement)
    $updateJson = New-TempCommandJsonFile @{
        policyId = $existing.id
        statements = [string[]]$updatedStatements
        force = $true
    }
    Invoke-OciJson @("iam", "policy", "update", "--from-json", $updateJson) "update notification policy" | Out-Null
    return "updated"
}

function Ensure-TopicAndSubscription([string]$CompartmentId) {
    $topics = Invoke-OciJson @("ons", "topic", "list", "--compartment-id", $CompartmentId, "--all") "list topics"
    $topic = @($topics.data | Where-Object { $_.name -eq $TopicName -and $_."lifecycle-state" -ne "DELETED" }) | Select-Object -First 1
    if (-not $topic) {
        $created = Invoke-OciJson @(
            "ons", "topic", "create",
            "--compartment-id", $CompartmentId,
            "--name", $TopicName,
            "--description", "Avisos de publicacion del prompt escolar Gabitin"
        ) "create topic"
        $topic = $created.data
        $topicState = "created"
    }
    else {
        $topicState = "already-present"
    }

    $topicId = Get-TopicId $topic
    if (-not $topicId) {
        throw "No pude determinar el OCID del topic creado/encontrado."
    }

    $subscriptions = Invoke-OciJson @("ons", "subscription", "list", "--compartment-id", $CompartmentId, "--topic-id", $topicId, "--all") "list subscriptions"
    $subscription = @(
        $subscriptions.data | Where-Object {
            $_.protocol -eq "EMAIL" -and $_.endpoint -eq $Email -and $_."lifecycle-state" -ne "DELETED"
        }
    ) | Select-Object -First 1
    if (-not $subscription) {
        Invoke-OciJson @(
            "ons", "subscription", "create",
            "--compartment-id", $CompartmentId,
            "--topic-id", $topicId,
            "--protocol", "EMAIL",
            "--subscription-endpoint", $Email
        ) "create email subscription" | Out-Null
        $subscriptionState = "created-pending-confirmation"
    }
    else {
        $subscriptionState = "already-present"
    }

    return @{
        topicId = $topicId
        topicState = $topicState
        subscriptionState = $subscriptionState
    }
}

function Configure-Vm([string]$TopicId) {
    $remote = "$VmUser@$VmHost"
    $remoteScript = @"
set -euo pipefail
APP="$RemoteAppDir"
CONFIG="`$APP/config/automation.env"
mkdir -p "`$(dirname "`$CONFIG")"
touch "`$CONFIG"
cp "`$CONFIG" "`$CONFIG.bak-notifications-`$(date -u +%Y%m%dT%H%M%SZ)"
if grep -q '^RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID=' "`$CONFIG"; then
  sed -i 's#^RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID=.*#RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID=$TopicId#' "`$CONFIG"
else
  printf '\nRESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID=$TopicId\n' >> "`$CONFIG"
fi
echo VM_NOTIFICATION_TOPIC_CONFIGURED=1
"@
    Add-Log "SSH configure VM START"
    ($remoteScript -replace "`r", "") | & ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new $remote "bash -s" 2>&1 | ForEach-Object {
        Add-Log $_
    }
    if ($LASTEXITCODE -ne 0) {
        throw "SSH configure VM failed"
    }
    Add-Log "SSH configure VM OK"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
Set-Content -LiteralPath $LogPath -Value "START $(Get-Date -Format o)"

$env:OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING = "True"
$env:PYTHONWARNINGS = "ignore"

try {
    Step "[1/6] Validando OCI CLI y perfil"
    if (-not (Get-Command oci -ErrorAction SilentlyContinue)) {
        throw "No se encontro OCI CLI en PATH."
    }
    Ensure-OciPassphrase
    $tenancyId = Read-OciConfigValue "tenancy"
    if (-not $tenancyId) {
        throw "No se encontro tenancy en el perfil OCI local."
    }
    if (-not $OciRegion) {
        $OciRegion = Read-OciConfigValue "region"
    }
    if (-not $OciRegion) {
        $OciRegion = "ca-toronto-1"
    }

    Step "[2/6] Asegurando policy IAM para publicar notificaciones"
    $policyState = Ensure-Policy $tenancyId
    Add-Log "POLICY_STATE=$policyState"

    Step "[3/6] Creando o reutilizando topic de OCI Notifications"
    $topicResult = Ensure-TopicAndSubscription $tenancyId
    Add-Log "TOPIC_STATE=$($topicResult.topicState)"
    Add-Log "SUBSCRIPTION_STATE=$($topicResult.subscriptionState)"

    Step "[4/6] Configurando topic en la VM"
    Configure-Vm $topicResult.topicId

    Step "[5/6] Verificando configuracion remota"
    $remote = "$VmUser@$VmHost"
    & ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new $remote "grep -q '^RESUMEN_ESCOLAR_NOTIFICATION_TOPIC_OCID=' '$RemoteAppDir/config/automation.env' && echo VM_CONFIG_OK=1 || echo VM_CONFIG_OK=0" 2>&1 | ForEach-Object {
        Add-Log $_
        Write-Host $_
    }
    if ($LASTEXITCODE -ne 0) {
        throw "No pude verificar la configuracion en la VM."
    }

    Step "[6/6] Listo. Revisa tu correo y confirma la suscripcion si llego pendiente."
    Add-Log "END $(Get-Date -Format o)"
}
finally {
    Remove-Item Env:\OCI_CLI_PASSPHRASE -ErrorAction SilentlyContinue
    Remove-Item Env:\OCI_CLI_KEY_PASSPHRASE -ErrorAction SilentlyContinue
    Remove-Item Env:\OCI_CLI_PASS_PHRASE -ErrorAction SilentlyContinue
    if ($Script:TempOciConfigFile) {
        Remove-Item -LiteralPath $Script:TempOciConfigFile -Force -ErrorAction SilentlyContinue
    }
    foreach ($tempFile in $Script:TempFiles) {
        Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
    }
}
