# 把爱弥斯装成开机自启、无窗口的后台程序
# 不要直接右键运行这个文件，请双击同目录的 install_task.bat

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs  = Join-Path $here "start_hidden.vbs"
$name = "AemeathFeishuBot"
$wscript = Join-Path $env:WINDIR "System32\wscript.exe"

if (-not (Test-Path $vbs))        { throw "找不到 start_hidden.vbs" }
if (-not (Test-Path "$here\aemeath.py")) { throw "找不到 aemeath.py" }

Write-Host "目录：$here"
Write-Host ""

# 先把旧实例清掉，免得跑出两份
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*aemeath.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 800
} catch { }

# ---------- 方案一：任务计划程序（崩溃自动重启） ----------
function Install-Task {
    $action = New-ScheduledTaskAction `
        -Execute $wscript `
        -Argument "`"$vbs`"" `
        -WorkingDirectory $here

    $trigger = New-ScheduledTaskTrigger -AtLogOn

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 99 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew `
        -Hidden

    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Limited

    Register-ScheduledTask -TaskName $name `
        -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description "爱弥斯 · 飞书长连接（无窗口）" -Force | Out-Null

    Start-ScheduledTask -TaskName $name
}

# ---------- 方案二：启动文件夹 + 快捷方式 ----------
function Install-Startup {
    $startup = [Environment]::GetFolderPath("Startup")
    $lnkPath = Join-Path $startup "$name.lnk"

    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath       = $wscript
    $lnk.Arguments        = "`"$vbs`""
    $lnk.WorkingDirectory = $here
    $lnk.WindowStyle      = 7
    $lnk.Description      = "爱弥斯 · 飞书长连接"
    $lnk.Save()

    Write-Host "已放入启动文件夹：$lnkPath"
    Start-Process -FilePath $wscript -ArgumentList "`"$vbs`"" -WindowStyle Hidden
}

$done = $false
try {
    Install-Task
    Write-Host "√ 已注册任务计划：$name（崩了会自动重启）" -ForegroundColor Green
    $done = $true
} catch {
    Write-Host "× 任务计划程序用不了：$($_.Exception.Message)" -ForegroundColor Yellow
}

if (-not $done) {
    try {
        Install-Startup
        Write-Host "√ 已用启动文件夹方案" -ForegroundColor Green
        $done = $true
    } catch {
        Write-Host "× 启动文件夹也失败：$($_.Exception.Message)" -ForegroundColor Red
    }
}

if (-not $done) {
    Write-Host "两种都没成，把这段文字发给我。" -ForegroundColor Red
    Read-Host "按回车关闭"
    exit 1
}

Write-Host ""
Write-Host "等它连上飞书（大概十几秒）……" -ForegroundColor DarkGray
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path "$here\aemeath.log") {
        if (Select-String -Path "$here\aemeath.log" -Pattern "connected to wss" -Quiet) { $ok = $true; break }
    }
}
$n = (Get-Process python -ErrorAction SilentlyContinue | Measure-Object).Count

if ($ok) {
    Write-Host "√ 已连上飞书，后台运行中（看不见窗口是正常的）" -ForegroundColor Green
} elseif ($n -gt 0) {
    Write-Host "程序在跑，但还没看到连接成功的日志，可能要多等一会儿。" -ForegroundColor Yellow
} else {
    Write-Host "× 进程没起来，去看日志找原因。" -ForegroundColor Red
}

Write-Host ""
Write-Host "以后开机就会自己跑，不用再开 PowerShell。"
Write-Host "日志：$here\aemeath.log"
Read-Host "按回车关闭这个窗口"
