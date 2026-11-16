# Framio Connect

**EN** - One keyboard and mouse for all your Windows PCs, shared clipboard, drag-and-drop file sending and LAN remote desktop. Single `.exe`, self-updating.
**TR** - Tüm Windows bilgisayarlarınız için tek klavye ve fare, ortak pano, sürükle-bırak dosya gönderme ve yerel ağda uzak masaüstü. Tek `.exe`, kendini günceller.

## Features / Özellikler
- Keyboard & mouse sharing - push the cursor to the screen edge to switch PCs (Scroll Lock = back to host)
- Clipboard sharing: text and copied files (Ctrl+C on one PC, Ctrl+V on the other)
- Drag-and-drop file sending to any connected PC (saved to `Downloads\Framio`)
- Remote desktop on the local network with ID + password and approval prompt
- Turkish / English, dark / light theme
- Automatic updates from GitHub Releases (SHA-256 verified)
- Modern glass UI (HTML/CSS in an embedded WebView2 window; falls back to an Edge/Chrome app window)

## Requirements / Gereksinimler
Windows 10/11 with the Microsoft Edge WebView2 runtime (preinstalled on Windows 11).

## Install / Kurulum
Download `FramioConnect.exe` from the latest [Release](../../releases/latest), run it on every PC, open **Settings -> Install on this PC**, and on the host also **Allow through firewall**.

## Security notes
- Connections are authenticated with a shared key / password (HMAC challenge-response, mutual). Traffic is **not encrypted** - use trusted networks (home / office LAN) only.
- Auto-update trusts this repository's releases: protect your GitHub account with 2FA.
- Remote desktop works on the local network only (no internet relay).

## Releasing an update
```
git tag v2.2.0
git push origin v2.2.0
```
GitHub Actions builds the exe and publishes the release; every installed copy updates itself within ~6 hours (or via Settings -> Check for updates).

## Develop
```
pip install -r requirements.txt
python main.py                # desktop window
python main.py --serve-only   # serve the UI for browser testing
python tools/selftest.py
.\build.ps1
```
