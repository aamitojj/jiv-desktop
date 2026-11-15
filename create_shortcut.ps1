# Creates a "Jiv Desktop" shortcut on the desktop pointing at start.bat
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desk = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $desk 'Jiv Desktop.lnk'))
$lnk.TargetPath = Join-Path $dir 'start.bat'
$lnk.WorkingDirectory = $dir
$lnk.WindowStyle = 7
$lnk.Description = 'Jiv Desktop - your local AI agent'
$lnk.Save()
Write-Host "Shortcut created on your desktop."
