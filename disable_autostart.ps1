# 只取消开机自启，不碰正在跑的进程
$ErrorActionPreference = "SilentlyContinue"
$name = "AemeathFeishuBot"

Stop-ScheduledTask -TaskName $name
Unregister-ScheduledTask -TaskName $name -Confirm:$false

$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "$name.lnk"
if (Test-Path $lnk) { Remove-Item $lnk -Force }

Write-Host "已取消开机自启。" -ForegroundColor Green
Write-Host "正在运行的那个没动，现在照样能回消息。"
Write-Host ""
Write-Host "以后想启动：双击 start_bot.bat（无窗口）"
Read-Host "按回车关闭"
