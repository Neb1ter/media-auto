# media-auto Windows Server Deploy Script (PowerShell 5.x compatible)
# Run as Administrator:
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   $tmp="$env:TEMP\deploy.ps1"; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile("https://raw.githubusercontent.com/Neb1ter/media-auto/main/windows-deploy.ps1",$tmp); & $tmp

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================"
Write-Host "  media-auto Platform - Windows Deploy"
Write-Host "========================================"
Write-Host ""

# Check admin
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] Please run PowerShell as Administrator!" -ForegroundColor Red
    exit 1
}

# Config
$AppDir    = "C:\web-apps\media-auto"
$NginxDir  = "C:\nginx"
$NginxVer  = "1.25.4"
$PythonVer = "3.11.9"
$NssmVer   = "2.24"
$RepoUrl   = "https://github.com/Neb1ter/media-auto.git"

function Download-File($url, $dest) {
    Write-Host "  Downloading: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    (New-Object Net.WebClient).DownloadFile($url, $dest)
}

# ---- STEP 1: Install Python 3.11 ----
Write-Host ""
Write-Host "[1/5] Installing Python $PythonVer..." -ForegroundColor Yellow

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$needPython = $true
if ($pythonCmd) {
    $ver = (& python --version 2>&1).ToString()
    if ($ver -like "*3.11*") {
        Write-Host "  OK: Python 3.11 already installed, skipping." -ForegroundColor Green
        $needPython = $false
    }
}

if ($needPython) {
    $installer = "$env:TEMP\python-$PythonVer-amd64.exe"
    Download-File "https://www.python.org/ftp/python/$PythonVer/python-$PythonVer-amd64.exe" $installer
    $p = Start-Process $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        Write-Host "[ERROR] Python install failed (code $($p.ExitCode))" -ForegroundColor Red
        exit 1
    }
    Remove-Item $installer -Force
    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", [System.EnvironmentVariableTarget]::Machine)
    $userPath    = [System.Environment]::GetEnvironmentVariable("PATH", [System.EnvironmentVariableTarget]::User)
    $env:PATH    = $machinePath + ";" + $userPath
    Write-Host "  OK: Python $PythonVer installed." -ForegroundColor Green
}

# ---- STEP 2: Install Nginx ----
Write-Host ""
Write-Host "[2/5] Installing Nginx $NginxVer..." -ForegroundColor Yellow

if (Test-Path "$NginxDir\nginx.exe") {
    Write-Host "  OK: Nginx already exists, skipping." -ForegroundColor Green
} else {
    $nginxZip = "$env:TEMP\nginx.zip"
    Download-File "https://nginx.org/download/nginx-$NginxVer.zip" $nginxZip
    Expand-Archive $nginxZip "C:\" -Force
    if (Test-Path "C:\nginx-$NginxVer") {
        Rename-Item "C:\nginx-$NginxVer" $NginxDir
    }
    Remove-Item $nginxZip -Force
    Write-Host "  OK: Nginx installed to $NginxDir" -ForegroundColor Green
}

# ---- STEP 3: Install NSSM ----
Write-Host ""
Write-Host "[3/5] Installing NSSM..." -ForegroundColor Yellow

$nssmExe = "$NginxDir\nssm.exe"
if (-not (Test-Path $nssmExe)) {
    $nssmZip = "$env:TEMP\nssm.zip"
    Download-File "https://nssm.cc/release/nssm-$NssmVer.zip" $nssmZip
    Expand-Archive $nssmZip "$env:TEMP\nssm_tmp" -Force
    $arch = "win64"
    if (-not [Environment]::Is64BitOperatingSystem) { $arch = "win32" }
    Copy-Item "$env:TEMP\nssm_tmp\nssm-$NssmVer\$arch\nssm.exe" $nssmExe
    Remove-Item $nssmZip -Force
    Remove-Item "$env:TEMP\nssm_tmp" -Recurse -Force
    Write-Host "  OK: NSSM installed." -ForegroundColor Green
} else {
    Write-Host "  OK: NSSM already exists, skipping." -ForegroundColor Green
}

# ---- STEP 4: Clone repo and install deps ----
Write-Host ""
Write-Host "[4/5] Deploying media-auto project..." -ForegroundColor Yellow

if (-not (Test-Path $AppDir)) {
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
}

if (Test-Path "$AppDir\.git") {
    Write-Host "  Pulling latest code..."
    Push-Location $AppDir
    git pull
    Pop-Location
} else {
    Write-Host "  Cloning repo to $AppDir ..."
    git clone $RepoUrl $AppDir
}
Write-Host "  OK: Code ready." -ForegroundColor Green

Write-Host "  Upgrading pip..."
& python -m pip install --upgrade pip -q

Write-Host "  Installing Python dependencies..."
& pip install -r "$AppDir\requirements.txt" -q
Write-Host "  OK: Dependencies installed." -ForegroundColor Green

Write-Host "  Installing Playwright Chromium..."
& playwright install --with-deps chromium
Write-Host "  OK: Playwright installed." -ForegroundColor Green

# Write .env
$envFile = "$AppDir\.env"
if (-not (Test-Path $envFile)) {
    $envContent = "DEEPSEEK_API_KEY=sk-becc913fefdf41ac8c5766e66cb8`r`nDASHSCOPE_API_KEY=sk-4a4de17b091f4fca867ff1cec04d`r`nGEMINI_API_KEY=sk-01pqCmgetdxiSmLP13D3F553109e78369eD8`r`nPORT=8000`r`nHOST=0.0.0.0`r`n"
    [System.IO.File]::WriteAllText($envFile, $envContent, [System.Text.Encoding]::UTF8)
    Write-Host "  WARNING: .env created - please complete the API Keys in: $envFile" -ForegroundColor Yellow
} else {
    Write-Host "  OK: .env already exists, skipping." -ForegroundColor Green
}

# ---- STEP 5: Register services and configure Nginx ----
Write-Host ""
Write-Host "[5/5] Registering Windows services and configuring Nginx..." -ForegroundColor Yellow

# Register media-auto service
$svc = Get-Service "media-auto" -ErrorAction SilentlyContinue
if (-not $svc) {
    $pythonPath = (Get-Command python).Source
    & $nssmExe install media-auto $pythonPath "$AppDir\start.py"
    & $nssmExe set media-auto AppDirectory $AppDir
    & $nssmExe set media-auto DisplayName "Media Auto Platform"
    & $nssmExe set media-auto Start SERVICE_AUTO_START
    Write-Host "  OK: media-auto service registered." -ForegroundColor Green
} else {
    Write-Host "  OK: media-auto service already exists, skipping." -ForegroundColor Green
}

# Configure Nginx
$nginxConf = "$NginxDir\conf\nginx.conf"
$confContent = Get-Content $nginxConf -Raw
if ($confContent -notlike "*media.get8.pro*") {
    $serverBlock = @"

    server {
        listen       80;
        server_name  media.get8.pro;

        location / {
            proxy_pass         http://127.0.0.1:8000;
            proxy_set_header   Host `$host;
            proxy_set_header   X-Real-IP `$remote_addr;
            proxy_set_header   X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_http_version 1.1;
            proxy_set_header   Upgrade `$http_upgrade;
            proxy_set_header   Connection "upgrade";
            proxy_read_timeout 300s;
        }
    }

}
"@
    $confContent = $confContent -replace '\}\s*$', $serverBlock
    [System.IO.File]::WriteAllText($nginxConf, $confContent, [System.Text.Encoding]::UTF8)
    Write-Host "  OK: Nginx config updated." -ForegroundColor Green
} else {
    Write-Host "  OK: Nginx config already contains media.get8.pro, skipping." -ForegroundColor Green
}

# Open firewall port 80
$fwRule = Get-NetFirewallRule -DisplayName "Nginx HTTP" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    New-NetFirewallRule -DisplayName "Nginx HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow | Out-Null
    Write-Host "  OK: Firewall port 80 opened." -ForegroundColor Green
}

# Register Nginx service
$nginxSvc = Get-Service "nginx" -ErrorAction SilentlyContinue
if (-not $nginxSvc) {
    & $nssmExe install nginx "$NginxDir\nginx.exe"
    & $nssmExe set nginx AppDirectory $NginxDir
    & $nssmExe set nginx Start SERVICE_AUTO_START
    Write-Host "  OK: Nginx service registered." -ForegroundColor Green
}

# Start services
Write-Host "  Starting media-auto service..."
& $nssmExe start media-auto 2>&1 | Out-Null
Start-Sleep -Seconds 3

Write-Host "  Starting Nginx service..."
& $nssmExe start nginx 2>&1 | Out-Null

Write-Host ""
Write-Host "========================================"
Write-Host "  Deploy Complete!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "  Local:  http://localhost:8000"
Write-Host "  Public: http://media.get8.pro  (after DNS propagation)"
Write-Host "  API:    http://media.get8.pro/docs"
Write-Host ""
Write-Host "  WARNING: Please complete API Keys in: $AppDir\.env" -ForegroundColor Yellow
Write-Host ""
