param(
    [Parameter(Mandatory = $true)][string]$Dist,
    [ValidateSet('Both', 'Lite', 'Full-Offline')][string]$Edition = 'Both'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$originalPath = $env:PATH
$originalLocalAppData = $env:LOCALAPPDATA
$originalProxy = @($env:HTTP_PROXY, $env:HTTPS_PROXY, $env:ALL_PROXY, $env:NO_PROXY)
$originalIndex = $env:ZT_PYPI_INDEX_URL
$originalPipIndex = $env:PIP_INDEX_URL
$originalPython = $env:ZT_PYTHON_EXE
$originalAddress = $env:ZT_SERVER_ADDRESS
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('ZT Windows ' + [guid]::NewGuid())
# Keep the app's Unicode/space path case separate from the Python DLL path.
$runtimeBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $originalLocalAppData }
$runtimeWork = Join-Path $runtimeBase ('zt-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $work, $runtimeWork | Out-Null
$regression = Join-Path $PSScriptRoot 'test_app.py'

function Invoke-Launch {
    param([string]$Entry, [string[]]$Options)
    & $Entry @Options
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher exit $LASTEXITCODE [$Entry] $Options"
    }
}

function Test-CrlfLock {
    param([string]$App, [string]$Entry)
    $lockPath = Join-Path $App 'requirements-lock-win-amd64-py313.txt'
    $manifestPath = Join-Path $App 'offline\manifest.json'
    $lockBytes = [IO.File]::ReadAllBytes($lockPath)
    $manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
    $utf8 = [Text.UTF8Encoding]::new($false)
    try {
        $crlfLock = $utf8.GetString($lockBytes) -replace '\r?\n', "`r`n"
        [IO.File]::WriteAllText($lockPath, $crlfLock, $utf8)
        $manifest = $utf8.GetString($manifestBytes) | ConvertFrom-Json
        $manifest.lock_sha256 = (Get-FileHash -LiteralPath $lockPath).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8), $utf8)
        Invoke-Launch $Entry @('--verify-offline')
    } finally {
        [IO.File]::WriteAllBytes($lockPath, $lockBytes)
        [IO.File]::WriteAllBytes($manifestPath, $manifestBytes)
    }
}

function Get-ActivePython {
    $environment = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'ZTColorStudio\studio-v14\environments') -Directory
    if (@($environment).Count -ne 1) { throw 'Expected a single environment key' }
    $active = Get-Content -Raw -LiteralPath (Join-Path $environment.FullName 'active.json') | ConvertFrom-Json
    return Join-Path $environment.FullName "$active\Scripts\python.exe"
}

function Test-Health {
    param([string]$Executable)
    $socket = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $socket.Start()
    $port = $socket.LocalEndpoint.Port
    $socket.Stop()
    $previousProfile = $env:USERPROFILE
    $previousEmailPrompt = $env:STREAMLIT_SERVER_SHOW_EMAIL_PROMPT
    $freshProfile = Join-Path $work ('Streamlit profile ' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $freshProfile | Out-Null
    $env:USERPROFILE = $freshProfile
    $env:STREAMLIT_SERVER_SHOW_EMAIL_PROMPT = 'true'
    $server = $null
    $healthy = $false
    try {
        # Match the default interactive launch with no Streamlit credentials
        # and without the headless option that also suppresses the email prompt.
        $server = Start-Process -FilePath $Executable -ArgumentList @('--port', $port) -PassThru
        for ($i = 0; $i -lt 80; $i++) {
            $server.Refresh()
            if ($server.HasExited) { throw "Streamlit launcher exited $($server.ExitCode)" }
            try {
                $reply = Invoke-WebRequest -Uri "http://127.0.0.1:$port/_stcore/health" -NoProxy -TimeoutSec 2
                if ($reply.StatusCode -eq 200 -and $reply.Content.Trim() -eq 'ok') {
                    $healthy = $true
                    break
                }
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (!$healthy) { throw 'Streamlit health endpoint did not become ready' }
        if (Test-Path -LiteralPath (Join-Path $freshProfile '.streamlit\credentials.toml')) {
            throw 'Startup unexpectedly wrote Streamlit credentials'
        }
    } finally {
        if ($null -ne $server -and !$server.HasExited) {
            & taskkill.exe /PID $server.Id /T /F | Out-Null
        }
        $env:USERPROFILE = $previousProfile
        $env:STREAMLIT_SERVER_SHOW_EMAIL_PROMPT = $previousEmailPrompt
    }
}

function New-MaintenanceFixture {
    param([string]$App, [string]$Path)
    New-Item -ItemType Directory -Path $Path | Out-Null
    foreach ($name in @('ZTColorStudio.exe', 'distribution.json', 'app.py', 'start.bat', 'uninstall.bat')) {
        Copy-Item -LiteralPath (Join-Path $App $name) -Destination $Path
    }
    $data = Join-Path $Path 'data'
    New-Item -ItemType Directory -Path $data | Out-Null
    Copy-Item -LiteralPath (Join-Path $App 'data\fk_colors.csv') -Destination $data
}

function Test-Maintenance {
    param([string]$App)
    $previousLocal = $env:LOCALAPPDATA
    $env:LOCALAPPDATA = Join-Path $runtimeWork 'm'
    try {
        $appRoot = Join-Path $work 'uninstall copy'
        New-MaintenanceFixture $App $appRoot
        $old = Join-Path $work 'previous release'
        $oldData = Join-Path $old 'data'
        New-Item -ItemType Directory -Path $oldData | Out-Null
        Set-Content -LiteralPath (Join-Path $oldData 'fk_colors.csv') -Value 'test color library'
        Set-Content -LiteralPath (Join-Path $oldData 'history.db') -Value 'test history'
        Invoke-Launch (Join-Path $appRoot 'start.bat') @('--import-data', $old)
        $history = Join-Path $appRoot 'data\history.db'
        if ((Get-Content -Raw -LiteralPath $history).Trim() -ne 'test history') { throw 'Data migration failed' }

        $owned = Join-Path $env:LOCALAPPDATA 'ZTColorStudio\studio-v14\environments'
        $outside = Join-Path $env:LOCALAPPDATA 'ExistingPython'
        New-Item -ItemType Directory -Path $owned, $outside | Out-Null
        'Y' | & (Join-Path $appRoot 'uninstall.bat') --keep-runtime
        if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $appRoot) -or !(Test-Path -LiteralPath $owned)) {
            throw 'Uninstall while retaining shared dependencies failed'
        }
        $backups = Join-Path $env:LOCALAPPDATA 'ZTColorStudio\backups'
        $saved = Get-ChildItem -LiteralPath $backups -Directory
        $history = Join-Path $saved.FullName 'data\history.db'
        if ((Get-Content -Raw -LiteralPath $history).Trim() -ne 'test history') { throw 'Uninstall backup failed' }

        New-MaintenanceFixture $App $appRoot
        'Y' | & (Join-Path $appRoot 'uninstall.bat') --purge-runtime
        if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $appRoot) -or (Test-Path -LiteralPath $owned) -or
            !(Test-Path -LiteralPath $outside) -or !(Test-Path -LiteralPath $history)) {
            throw 'Complete uninstall or preservation of other data failed'
        }
        Write-Host 'Data migration and both uninstall modes passed'
    } finally {
        $env:LOCALAPPDATA = $previousLocal
    }
}

try {
    $expectedHashes = @{}
    Get-Content -LiteralPath (Join-Path $Dist 'SHA256SUMS.txt') | ForEach-Object {
        $digest, $file = $_ -split '  ', 2
        $expectedHashes[$file] = $digest
    }
    $editions = if ($Edition -eq 'Both') { @('Full-Offline', 'Lite') } else { @($Edition) }
    $fullLocal = $null
    foreach ($mode in $editions) {
        $zip = @(Get-ChildItem -LiteralPath $Dist -Filter "*-Windows-$mode.zip")
        if ($zip.Count -ne 1) { throw "Expected one $mode archive" }
        if ((Get-FileHash -LiteralPath $zip[0].FullName).Hash -ne $expectedHashes[$zip[0].Name]) { throw 'ZIP hash mismatch' }

        $directory = Join-Path $work "$mode 渲染 测试"
        Expand-Archive -LiteralPath $zip[0].FullName -DestinationPath $directory
        $app = Join-Path $directory 'ZT-Color-Match-Studio'
        $entry = Join-Path $app 'start.bat'
        $editionDir = if ($mode -eq 'Full-Offline') { 'f' } else { 'l' }
        $env:LOCALAPPDATA = Join-Path $runtimeWork $editionDir
        New-Item -ItemType Directory -Path $env:LOCALAPPDATA | Out-Null
        $env:ZT_PYTHON_EXE = $null
        $env:ZT_SERVER_ADDRESS = '127.0.0.1'
        if ($mode -eq 'Full-Offline') {
            $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
            $env:HTTP_PROXY = 'http://127.0.0.1:1'
            $env:HTTPS_PROXY = $env:HTTP_PROXY
            $env:ALL_PROXY = $env:HTTP_PROXY
            $env:NO_PROXY = 'localhost,127.0.0.1'
            $env:PIP_INDEX_URL = 'https://example.invalid/simple'
            $env:ZT_PYPI_INDEX_URL = $env:PIP_INDEX_URL
            Invoke-Launch $entry @('--verify-offline')
            Test-CrlfLock $app $entry
            $manifest = Get-Content -Raw -LiteralPath (Join-Path $app 'offline\manifest.json') | ConvertFrom-Json
            $wheel = Join-Path (Join-Path $app 'offline') $manifest.wheels[0].path
            Move-Item -LiteralPath $wheel -Destination "$wheel.missing"
            try {
                & $entry --check
                if ($LASTEXITCODE -eq 0) { throw 'Incomplete offline package unexpectedly passed' }
            } finally {
                Move-Item -LiteralPath "$wheel.missing" -Destination $wheel
            }
        } else {
            $env:PATH = $originalPath
            $env:HTTP_PROXY, $env:HTTPS_PROXY, $env:ALL_PROXY, $env:NO_PROXY = $originalProxy
            $env:PIP_INDEX_URL = $originalPipIndex
            $env:ZT_PYPI_INDEX_URL = 'https://127.0.0.1:1/simple'
        }

        if ($mode -eq 'Lite' -and $fullLocal) {
            $liteLocal = $env:LOCALAPPDATA
            try {
                $env:LOCALAPPDATA = $fullLocal
                $previousPython = Get-ActivePython
                Invoke-Launch $entry @('--check')
                if ((Get-ActivePython) -ne $previousPython) { throw 'Lite did not reuse Full Offline dependencies' }
            } finally {
                $env:LOCALAPPDATA = $liteLocal
            }
        }
        Invoke-Launch $entry @('--check')
        $python = Get-ActivePython
        Invoke-Launch $entry @('--check')
        if ($python -ne (Get-ActivePython)) { throw "$mode did not reuse the environment" }
        if ($mode -eq 'Full-Offline') {
            & $python -I -m pip --disable-pip-version-check check
            if ($LASTEXITCODE -ne 0) { throw 'pip check failed' }
            & $python -I $regression $app
            if ($LASTEXITCODE -ne 0) { throw 'Application regression failed' }
        }
        Test-Health (Join-Path $app 'ZTColorStudio.exe')
        if ($mode -eq 'Full-Offline') {
            $envParent = Join-Path $env:LOCALAPPDATA 'ZTColorStudio\studio-v14\environments'
            Remove-Item -LiteralPath $envParent -Recurse -Force
            Invoke-Launch $entry @('--check')
            if (!(Test-Path -LiteralPath (Get-ActivePython))) { throw 'Offline environment was not rebuilt' }
            $fullLocal = $env:LOCALAPPDATA
        }
        Write-Host "$mode Windows smoke tests passed"
    }
    Test-Maintenance $app
} finally {
    $env:PATH = $originalPath
    $env:LOCALAPPDATA = $originalLocalAppData
    $env:HTTP_PROXY, $env:HTTPS_PROXY, $env:ALL_PROXY, $env:NO_PROXY = $originalProxy
    $env:ZT_PYPI_INDEX_URL = $originalIndex
    $env:PIP_INDEX_URL = $originalPipIndex
    $env:ZT_PYTHON_EXE = $originalPython
    $env:ZT_SERVER_ADDRESS = $originalAddress
    Write-Host "Smoke test extraction directory $work"
    Write-Host "Smoke test runtime directory $runtimeWork"
}
