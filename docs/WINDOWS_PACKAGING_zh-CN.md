# Windows EXE 打包说明

OpenFrequency 仍然保留现有 `Flask/Socket.IO + HTML` 架构。桌面版通过 `desktop_launcher.py` 启动本地 Web 服务，并自动打开默认浏览器：

```text
http://127.0.0.1:5000/dashboard
```

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

`OpenFrequency.exe` 是默认发布版，隐藏终端窗口。`OpenFrequency-Console.exe` 是调试版，会显示终端输出。两者都会写日志文件。

不要运行 `build\openfrequency` 下的 exe。那里是 PyInstaller 中间目录，不是最终发布产物。

## 关闭程序

打包版启动后会在 Windows 系统托盘显示 OpenFrequency 图标。

右键托盘图标可以执行：

- `Open Dashboard`：打开 Dashboard。
- `Open Logs`：打开日志目录。
- `Exit OpenFrequency`：关闭后台程序。

如果托盘不可用，也可以在 PowerShell 里强制关闭：

```powershell
Get-Process | Where-Object { $_.ProcessName -like "OpenFrequency*" } | Stop-Process -Force
```

## 运行时配置和日志

打包版不会把本机 `config.json` 打进 exe。首次运行时会从 `config.example.json` 复制一份到：

```text
%APPDATA%\OpenFrequency\config.json
```

隐藏终端运行时，stdout/stderr 会写入：

```text
%APPDATA%\OpenFrequency\logs\openfrequency_YYYYMMDD_HHMMSS.log
```

飞行日志和航迹也会写到同一个目录：

```text
%APPDATA%\OpenFrequency\logs\flight_log_YYYYMMDD_HHMMSS.txt
%APPDATA%\OpenFrequency\logs\track_YYYYMMDD_HHMMSS.csv
```

在当前机器上，这个目录通常是：

```text
C:\Users\HP\AppData\Roaming\OpenFrequency\logs
```

## 打包资源策略

会打包：

- `templates`
- `static`
- `data` 中的基础数据，例如机场数据、客舱脚本和本地化文件
- `models`
- `ffmpeg`
- `plugins`
- `app.py`
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
