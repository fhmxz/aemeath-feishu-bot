# 只停止正在跑的进程，不动开机自启设置
$ErrorActionPreference = "SilentlyContinue"

$killed = 0
try {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*aemeath.py*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force
            $killed++
        }
} catch {
    Write-Host "查不到进程列表，请到任务管理器里结束 python.exe" -ForegroundColor Yellow
    Read-Host "按回车关闭"
    exit 1
}

if ($killed -gt 0) {
    Write-Host "已停止 $killed 个进程。" -ForegroundColor Green
} else {
    Write-Host "没找到正在运行的进程。" -ForegroundColor Yellow
}
Write-Host "开机自启的设置没动。"
Read-Host "按回车关闭"
