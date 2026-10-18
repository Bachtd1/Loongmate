<#
.SYNOPSIS
  Tạo tác vụ Windows Task Scheduler: kéo SaveVault từ Pocket 1 về PC mỗi 6 giờ và khi đăng nhập.
.DESCRIPTION
  Chạy bằng tài khoản hiện tại (không cần quyền admin). Chỉ chạy khi có mạng; nếu lỡ lịch
  (PC tắt) thì chạy bù khi bật lại; không chạy chồng hai bản cùng lúc.
  Gỡ bằng uninstall_schedule.ps1 (cùng -TaskName).
  Mặc định truyền --keep 30 cho pull_saves.py (prune sau khi kéo thành công, giữ 30 snapshot mới nhất).
#>
param(
    [string]$TaskName = "LoongMate-Pull",
    [string]$Python = "",
    [int]$EveryHours = 6,
    [string[]]$ExtraArgs = @("--keep", "30")  # mặc định: giữ 30 snapshot trên PC; truyền -ExtraArgs @() để không dọn
)
$ErrorActionPreference = "Stop"
if (-not $Python) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "Không tìm thấy python.exe trong PATH. Truyền -Python <đường dẫn>." }
    $Python = $cmd.Source
}
$script = Join-Path $PSScriptRoot "pull_saves.py"
if (-not (Test-Path $script)) { throw "Không thấy $script" }

$argLine = "`"$script`" " + (($ExtraArgs | ForEach-Object { "`"$_`"" }) -join " ")
$action = New-ScheduledTaskAction -Execute $Python -Argument $argLine.Trim() -WorkingDirectory $PSScriptRoot

$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours $EveryHours)

$settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $repeat) `
    -Settings $settings -Description "LoongMate: kéo SaveVault từ Pocket 1 qua Tailscale" -Force | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
Write-Host "Đã tạo tác vụ '$TaskName' (trạng thái: $($t.State)). Chạy: $Python $argLine"
Write-Host "Log: $env:USERPROFILE\Pocket1Backup\pull.log"
