param(
    [string]$LogPath = "C:\Users\Martin\Documents\Codex\resumen_escolar\codex-ssh-key-candidates.log"
)

$ErrorActionPreference = "Continue"

$roots = @(
    "C:\Users\Martin\.ssh",
    "C:\Users\Martin\Documents",
    "C:\Users\Martin\Desktop"
) | Where-Object { Test-Path $_ }

$patterns = @(
    "oci_original.key",
    "*oci*key*",
    "*oracle*key*",
    "*original*key*",
    "id_rsa",
    "id_ed25519",
    "*worldcup2026_ssh*"
)

& {
    "START $(Get-Date -Format o)"
    foreach ($root in $roots) {
        "ROOT=$root"
        foreach ($pattern in $patterns) {
            Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter $pattern -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.FullName -notmatch '\\.git\\' -and
                    $_.FullName -notmatch '\\node_modules\\' -and
                    $_.FullName -notmatch '\\.runtime\\'
                } |
                Select-Object FullName, Length, LastWriteTime |
                Format-Table -AutoSize
        }
    }
    "END $(Get-Date -Format o)"
} *> $LogPath
