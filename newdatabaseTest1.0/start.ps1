$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($env:MYSQL_HOST)) { $env:MYSQL_HOST = '127.0.0.1' }
if ([string]::IsNullOrWhiteSpace($env:MYSQL_PORT)) { $env:MYSQL_PORT = '3306' }
if ([string]::IsNullOrWhiteSpace($env:MYSQL_USER)) { $env:MYSQL_USER = 'course_app' }
if ([string]::IsNullOrWhiteSpace($env:MYSQL_DATABASE)) { $env:MYSQL_DATABASE = 'course_registration' }
if ([string]::IsNullOrWhiteSpace($env:TEST_PAGE_PORT)) { $env:TEST_PAGE_PORT = '8090' }

$securePassword = Read-Host '请输入 course_app 的 MySQL 密码' -AsSecureString
$env:MYSQL_PASSWORD = [System.Net.NetworkCredential]::new('', $securePassword).Password

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
