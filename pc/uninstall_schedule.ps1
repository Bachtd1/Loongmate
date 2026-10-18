<#
.SYNOPSIS
  Gỡ tác vụ Task Scheduler do install_schedule.ps1 tạo, rồi xác nhận đã gỡ.
#>
param([string]$TaskName = "LoongMate-Pull")
$ErrorActionPreference = "Stop"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Error "Tác vụ '$TaskName' VẪN còn sau khi gỡ."
    exit 1
}
Write-Host "Đã gỡ và xác nhận không còn tác vụ '$TaskName'."
