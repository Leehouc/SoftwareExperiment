$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($env:MYSQL_HOST)) { $env:MYSQL_HOST = '127.0.0.1' }
if ([string]::IsNullOrWhiteSpace($env:MYSQL_PORT)) { $env:MYSQL_PORT = '3306' }
if ([string]::IsNullOrWhiteSpace($env:MYSQL_USER)) { $env:MYSQL_USER = 'course_app' }
if ([string]::IsNullOrWhiteSpace($env:MYSQL_DATABASE)) { $env:MYSQL_DATABASE = 'course_registration' }
if ([string]::IsNullOrWhiteSpace($env:CATALOG_API_URL)) { $env:CATALOG_API_URL = 'http://127.0.0.1:8000' }
if ([string]::IsNullOrWhiteSpace($env:ADMINISTER_PORT)) { $env:ADMINISTER_PORT = '8040' }

$securePassword = Read-Host '请输入 MySQL 应用账号密码' -AsSecureString
$env:MYSQL_PASSWORD = [System.Net.NetworkCredential]::new('', $securePassword).Password
$exitCode = 0

try {
    $serverPath = Join-Path $PSScriptRoot 'server.py'
    py -3 $serverPath
    $exitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
}

if ($exitCode -ne 0) {
    exit $exitCode
}
