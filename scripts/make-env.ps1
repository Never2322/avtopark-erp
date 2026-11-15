$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$example = Join-Path $root '.env.example'
$target = Join-Path $root '.env'
if (Test-Path $target) { exit 0 }

function New-Secret([int]$length) {
    $chars = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $bytes = New-Object byte[] (4 * $length)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    $sb = New-Object System.Text.StringBuilder
    for ($i = 0; $i -lt $length; $i++) {
        $n = [int]([BitConverter]::ToUInt32($bytes, 4 * $i) % $chars.Length)
        [void]$sb.Append($chars[$n])
    }
    return $sb.ToString()
}

$text = [System.IO.File]::ReadAllText($example, [System.Text.Encoding]::UTF8)
$dbPass = New-Secret 24
$adminPass = New-Secret 14
$text = $text -replace '(?m)^POSTGRES_PASSWORD=[^\r\n]*', ('POSTGRES_PASSWORD=' + $dbPass)
$text = $text -replace '(?m)^ADMIN_PASSWORD=[^\r\n]*', ('ADMIN_PASSWORD=' + $adminPass)
[System.IO.File]::WriteAllText($target, $text, (New-Object System.Text.UTF8Encoding($false)))
