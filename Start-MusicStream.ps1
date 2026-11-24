[CmdletBinding()]
param(
    [string]$NavidromeUrl,
    [string]$BindAddress = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($NavidromeUrl)) {
    $NavidromeUrl = Read-Host 'Navidrome server URL'
}
$navidromeUser = Read-Host 'Navidrome username'
$navidromePassword = Read-Host 'Navidrome password' -AsSecureString
if ([string]::IsNullOrWhiteSpace($NavidromeUrl) -or [string]::IsNullOrWhiteSpace($navidromeUser) -or $navidromePassword.Length -eq 0) {
    throw 'Navidrome server URL, username, and password are required.'
}

$apiToken = $null
if ($BindAddress -ne '127.0.0.1') {
    $apiToken = Read-Host 'Music Stream API token to use on the Mac' -AsSecureString
    if ($apiToken.Length -eq 0) {
        throw 'Set an API token before publishing Music Stream on the local network.'
    }
}

$variables = @{
    NAVIDROME_URL = $NavidromeUrl
    NAVIDROME_USERNAME = $navidromeUser
    NAVIDROME_PASSWORD = [Net.NetworkCredential]::new('', $navidromePassword).Password
    MUSIC_STREAM_BIND_IP = $BindAddress
    MUSIC_STREAM_PORT = [string]$Port
    MUSIC_STREAM_API_TOKEN = if ($apiToken) { [Net.NetworkCredential]::new('', $apiToken).Password } else { '' }
}
$previous = @{}
Push-Location -LiteralPath $PSScriptRoot
try {
    foreach ($name in $variables.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $variables[$name], 'Process')
    }
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose could not start Music Stream.'
    }
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    $variables.Clear()
    Pop-Location
}
