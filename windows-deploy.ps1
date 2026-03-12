# ============================================================
# media-auto Windows Server 一键部署脚本
# 使用方法（以管理员身份运行 PowerShell）：
#   irm https://raw.githubusercontent.com/Neb1ter/media-auto/main/windows-deploy.ps1 | iex
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  media-auto 自媒体平台 Windows 部署脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查管理员权限
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[错误] 请以管理员身份运行 PowerShell！" -ForegroundColor Red
    exit 1
}

# ── 配置变量 ──────────────────────────────────────────────
$AppDir      = "C:\web-apps\media-auto"
$NginxDir    = "C:\nginx"
$NginxVer    = "1.25.4"
$PythonVer   = "3.11.9"
$NssmVer     = "2.24"
$RepoUrl     = "https://github.com/Neb1ter/media-auto.git"

# ── 辅助函数 ──────────────────────────────────────────────
function Step($n, $msg) {
    Write-Host ""
    Write-Host "[$n/5] $msg" -ForegroundColor Yellow
}

function OK($msg) {
    Write-Host "  ✓ $msg" -ForegroundColor Green
}

function Download($url, $dest) {
    Write-Host "  → 下载: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

# ════════════════════════════════════════════════════════════
# STEP 1 — 安装 Python 3.11
# ════════════════════════════════════════════════════════════
Step 1 "安装 Python $PythonVer"

$pythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
$needPython = $true
if ($pythonExe) {
    $ver = & python --version 2>&1
    if ($ver -like "*3.11*") { OK "Python 3.11 已安装，跳过"; $needPython = $false }
}

if ($needPython) {
    $installer = "$env:TEMP\python-$PythonVer-amd64.exe"
    Download "https://www.python.org/ftp/python/$PythonVer/python-$PythonVer-amd64.exe" $installer
    $p = Start-Process $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait -PassThru
    if ($p.ExitCode -ne 0) { Write-Host "[错误] Python 安装失败 (code $($p.ExitCode))" -ForegroundColor Red; exit 1 }
    Remove-Item $installer -Force
    # 刷新 PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
    OK "Python $PythonVer 安装成功"
}

# ════════════════════════════════════════════════════════════
# STEP 2 — 安装 Nginx
# ════════════════════════════════════════════════════════════
Step 2 "安装 Nginx $NginxVer"

if (Test-Path "$NginxDir\nginx.exe") {
    OK "Nginx 已存在，跳过"
} else {
    $nginxZip = "$env:TEMP\nginx.zip"
    Download "https://nginx.org/download/nginx-$NginxVer.zip" $nginxZip
    Expand-Archive $nginxZip "C:\" -Force
    if (Test-Path "C:\nginx-$NginxVer") { Rename-Item "C:\nginx-$NginxVer" $NginxDir }
    Remove-Item $nginxZip -Force
    OK "Nginx 安装到 $NginxDir"
}

# ════════════════════════════════════════════════════════════
# STEP 3 — 安装 NSSM 并注册服务
# ════════════════════════════════════════════════════════════
Step 3 "安装 NSSM 服务管理器"

$nssmExe = "$NginxDir\nssm.exe"
if (-not (Test-Path $nssmExe)) {
    $nssmZip = "$env:TEMP\nssm.zip"
    Download "https://nssm.cc/release/nssm-$NssmVer.zip" $nssmZip
    Expand-Archive $nssmZip "$env:TEMP\nssm_tmp" -Force
    $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
    Copy-Item "$env:TEMP\nssm_tmp\nssm-$NssmVer\$arch\nssm.exe" $nssmExe
    Remove-Item $nssmZip, "$env:TEMP\nssm_tmp" -Recurse -Force
    OK "NSSM 已安装到 $nssmExe"
} else {
    OK "NSSM 已存在，跳过"
}

# ════════════════════════════════════════════════════════════
# STEP 4 — 克隆 media-auto 项目并安装依赖
# ════════════════════════════════════════════════════════════
Step 4 "部署 media-auto 项目"

if (-not (Test-Path $AppDir)) { New-Item -ItemType Directory -Force -Path $AppDir | Out-Null }

if (Test-Path "$AppDir\.git") {
    Write-Host "  → 拉取最新代码..."
    Push-Location $AppDir; git pull; Pop-Location
} else {
    Write-Host "  → 克隆仓库到 $AppDir ..."
    git clone $RepoUrl $AppDir
}
OK "代码已就绪"

Write-Host "  → 升级 pip..."
& python -m pip install --upgrade pip -q

Write-Host "  → 安装 Python 依赖..."
& pip install -r "$AppDir\requirements.txt" -q
OK "Python 依赖安装完成"

Write-Host "  → 安装 Playwright Chromium..."
& playwright install --with-deps chromium
OK "Playwright 安装完成"

# ── 写入 .env 配置文件 ────────────────────────────────────
$envFile = "$AppDir\.env"
if (-not (Test-Path $envFile)) {
    @"
# ===== AI 模型配置（请补全完整 Key）=====
DEEPSEEK_API_KEY=sk-becc913fefdf41ac8c5766e66cb8
DASHSCOPE_API_KEY=sk-4a4de17b091f4fca867ff1cec04d
GEMINI_API_KEY=sk-01pqCmgetdxiSmLP13D3F553109e78369eD8

# ===== 服务配置 =====
PORT=8000
HOST=0.0.0.0
"@ | Set-Content $envFile -Encoding UTF8
    Write-Host "  ⚠ .env 已创建，请补全 API Keys: $envFile" -ForegroundColor Yellow
} else {
    OK ".env 已存在，跳过"
}

# ════════════════════════════════════════════════════════════
# STEP 5 — 注册 Windows 服务 & 写入 Nginx 配置
# ════════════════════════════════════════════════════════════
Step 5 "注册 Windows 服务并配置 Nginx"

# -- media-auto 服务 --
$svc = Get-Service "media-auto" -ErrorAction SilentlyContinue
if (-not $svc) {
    $pythonPath = (Get-Command python).Source
    & $nssmExe install media-auto $pythonPath "$AppDir\start.py"
    & $nssmExe set media-auto AppDirectory $AppDir
    & $nssmExe set media-auto DisplayName "Media Auto Platform"
    & $nssmExe set media-auto Description "自媒体运营自动化平台"
    & $nssmExe set media-auto Start SERVICE_AUTO_START
    OK "media-auto 服务已注册"
} else {
    OK "media-auto 服务已存在，跳过"
}

# -- Nginx 配置 --
$nginxConf = "$NginxDir\conf\nginx.conf"
$mediaConf = @"

    # media-auto 反向代理（media.get8.pro）
    server {
        listen       80;
        server_name  media.get8.pro;

        location / {
            proxy_pass         http://127.0.0.1:8000;
            proxy_set_header   Host \$host;
            proxy_set_header   X-Real-IP \$remote_addr;
            proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_http_version 1.1;
            proxy_set_header   Upgrade \$http_upgrade;
            proxy_set_header   Connection "upgrade";
            proxy_read_timeout 300s;
        }
    }
"@

# 检查是否已经添加过
if ((Get-Content $nginxConf -Raw) -notlike "*media.get8.pro*") {
    # 在最后一个 } 前插入新 server 块
    $content = Get-Content $nginxConf -Raw
    $content = $content -replace '(\s*\}\s*)$', "$mediaConf`n}"
    Set-Content $nginxConf $content -Encoding UTF8
    OK "Nginx 配置已更新"
} else {
    OK "Nginx 配置已包含 media.get8.pro，跳过"
}

# -- 开放防火墙端口 80 --
$fwRule = Get-NetFirewallRule -DisplayName "Nginx HTTP" -ErrorAction SilentlyContinue
if (-not $fwRule) {
    New-NetFirewallRule -DisplayName "Nginx HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow | Out-Null
    OK "防火墙已开放 80 端口"
}

# -- 启动服务 --
Write-Host ""
Write-Host "  → 启动 media-auto 服务..."
& $nssmExe start media-auto 2>&1 | Out-Null
Start-Sleep -Seconds 3

Write-Host "  → 启动 Nginx 服务..."
$nginxSvc = Get-Service "nginx" -ErrorAction SilentlyContinue
if (-not $nginxSvc) {
    & $nssmExe install nginx "$NginxDir\nginx.exe"
    & $nssmExe set nginx AppDirectory $NginxDir
    & $nssmExe set nginx Start SERVICE_AUTO_START
}
& $nssmExe start nginx 2>&1 | Out-Null

# ════════════════════════════════════════════════════════════
# 完成
# ════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  ✅ 部署完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  本地访问: http://localhost:8000" -ForegroundColor White
Write-Host "  公网访问: http://media.get8.pro  (DNS 生效后)" -ForegroundColor White
Write-Host "  API 文档: http://media.get8.pro/docs" -ForegroundColor White
Write-Host ""
Write-Host "  ⚠ 请检查并补全 $AppDir\.env 中的 API Keys！" -ForegroundColor Yellow
Write-Host ""
