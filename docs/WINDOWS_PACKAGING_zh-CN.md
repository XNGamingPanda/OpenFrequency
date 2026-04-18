# Windows EXE 打包说明

OpenFrequency 仍然保留现有 `Flask/Socket.IO + HTML` 架构。桌面版现在通过 `launcher.py` 启动本地 Web 服务，并用 `pywebview` 直接打开桌面窗口，不再默认弹出系统浏览器。

本地服务地址仍然是：

```text
http://127.0.0.1:5000/dashboard
```

但正常用户看到的是应用窗口，而不是浏览器标签页。

## 构建命令

在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -Clean
```

输出位置：

```text
dist\OpenFrequency\OpenFrequency.exe
dist\OpenFrequency\OpenFrequency-Console.exe
```

- `OpenFrequency.exe`：默认发布版，隐藏终端，直接打开桌面窗口
- `OpenFrequency-Console.exe`：调试版，显示终端输出

不要运行 `build\openfrequency` 里的 exe。那是 PyInstaller 中间目录，不是最终发布产物。

## 关闭程序

桌面版关闭方式：

- 点击窗口右上角关闭按钮时，只会隐藏到后台，不会结束主程序
- 使用系统托盘菜单中的 `Show Window` 可重新打开窗口
- 只有使用系统托盘菜单中的 `Exit OpenFrequency`，才会真正结束后台程序

托盘菜单还包含：

- `Show Window`
- `Refresh Window`
- `Open Logs`

如果托盘不可用，也可以在 PowerShell 中强制关闭：

```powershell
Get-Process | Where-Object { $_.ProcessName -like "OpenFrequency*" } | Stop-Process -Force
```

## 运行时配置和日志

打包版不会把本机 `config.json` 打进 exe。首次运行时会从 `config.example.json` 复制一份到：

```text
%APPDATA%\OpenFrequency\config.json
```

隐藏终端模式下，stdout/stderr 会写入：

```text
%APPDATA%\OpenFrequency\logs\openfrequency_YYYYMMDD_HHMMSS.log
```

飞行日志和航迹也会写入同一目录：

```text
%APPDATA%\OpenFrequency\logs\flight_log_YYYYMMDD_HHMMSS.txt
%APPDATA%\OpenFrequency\logs\track_YYYYMMDD_HHMMSS.csv
```

在当前机器上，这通常是：

```text
C:\Users\HP\AppData\Roaming\OpenFrequency\logs
```

## 打包资源策略

会打包：

- `templates`
- `static`（不含 `static/cabin_media`）
- `data` 中的基础数据
- `ffmpeg`
- `plugins`
- `app.py`
- `launcher.py`
- `config.example.json`

不会打包：

- `config.json`
- `logs`
- `data/reports`
- `data/storage`
- `data/ground_cache`
- `tmp_dashboard.js`
- `llm_error.txt`
- `debug_tts.mp3`
- `static/cabin_media`
- `models`
- `__pycache__` 和临时文件

## 端口

打包版默认只监听本机：

```text
127.0.0.1:5000
```

如需临时改端口：

```powershell
$env:OPENFREQUENCY_PORT="5001"
.\dist\OpenFrequency\OpenFrequency.exe
```
