# Local build:  .\build.ps1   ->  dist\FramioConnect.exe
python -m pip install -r requirements.txt pyinstaller
python tools\make_icon.py
python -m PyInstaller --onefile --noconsole --name FramioConnect --icon assets\framio.ico --collect-all webview --collect-all pythonnet --collect-all clr_loader --add-data "framio/web;framio/web" --clean --noconfirm main.py
$h = (Get-FileHash dist\FramioConnect.exe -Algorithm SHA256).Hash.ToLower()
"$h  FramioConnect.exe" | Set-Content dist\FramioConnect.exe.sha256 -NoNewline
Write-Host "Built dist\FramioConnect.exe"

