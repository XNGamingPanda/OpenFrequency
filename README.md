# OpenATC DataBridge

X-Plane 11/12 插件：向 OpenATC 客户端提供 UDP JSON 遥测，并为
**OpenFrequency** 提供 X-Plane 12 Local Web API 兼容层。

- 版本：1.2
- 平台：Windows x64，X-Plane 11.55+ / X-Plane 12
- 构建：Visual Studio 2026（v145 工具集），C++20，静态 CRT（/MT）

## 功能

- **UDP 遥测**：10 Hz JSON，发送到 `127.0.0.1:49010` 及 `49020–49029`
- **UDP 控制**：`127.0.0.1:49031`，支持调频、应答机、模拟交通
- **TCAS 交通注入**：官方 `override_TCAS` 接口，可选可见 AI 机模
- **OpenFrequency 兼容**：在 `127.0.0.1:8086` 模拟 X-Plane 12 Local Web API
  的 REST 子集，OpenFrequency 无需改代码即可连接 X-Plane 11
- **线程安全**：XPLM 只在模拟器主线程访问；HTTP 线程只读写内存快照和队列

## 构建

需要 Visual Studio 2026（v145）和 Windows 10/11 SDK。
X-Plane SDK 4.3.0 已包含在 `XPSDK430/SDK`，无需另行下载。

```bat
msbuild OpenATC_DataBridge.vcxproj /p:Configuration=Release /p:Platform=x64
```

产物：`x64/Release/OpenATC_DataBridge.xpl`（约 385 KB）。

仓库同时附带预编译成品：`dist/64/win.xpl`（Release x64，可直接安装）。

> 如果使用 VS 2022（v143），请在工程属性中把 `PlatformToolset` 改为 `v143`。

## 安装

```
X-Plane 11/Resources/plugins/OpenATC_DataBridge/64/win.xpl
X-Plane 11/Resources/plugins/OpenATC_DataBridge/64/OpenATC_DataBridge.ini
```

1. 可以直接使用随仓库提供的 `dist/64/win.xpl`；如果自行构建，把
   `OpenATC_DataBridge.xpl` 重命名为 `win.xpl`。
2. 把 `OpenATC_DataBridge.ini.example` 复制为 `OpenATC_DataBridge.ini`，
   与 `win.xpl` 放在同一个 `64\` 目录。
3. 启动 X-Plane，在 `Log.txt` 中搜索 `Local Web API` 确认服务已启动。

## 网络接口

| 用途 | 地址 | 说明 |
|------|------|------|
| UDP 遥测 | `127.0.0.1:49010`、`49020–49029` | 10 Hz JSON，OpenATC 客户端 |
| UDP 控制 | `127.0.0.1:49031` | `SET COM1` / `SET SQUAWK` / `TRAFFIC` 等 |
| OpenFrequency Web API | `127.0.0.1:8086` | HTTP/1.1 REST，仅环回地址 |

浏览器直接查看（OpenFrequency 关闭时再访问，避免长连接排队）：

```
http://127.0.0.1:8086/api/capabilities
http://127.0.0.1:8086/api/v3/datarefs
http://127.0.0.1:8086/api/v3/datarefs/1/value
http://127.0.0.1:8086/api/v3/commands
```

完整的传输内容、网络实现、端点定义、值编码和错误码见
[OPENFREQUENCY_BRIDGE.md](OPENFREQUENCY_BRIDGE.md)。

## 配置

见 `OpenATC_DataBridge.ini.example`，主要配置项：

| 键 | 默认值 | 说明 |
|----|--------|------|
| `WebApiEnabled` | `1` | OpenFrequency 兼容服务开关 |
| `WebApiPort` | `8086` | HTTP 端口（仅绑定 127.0.0.1） |
| `ControlPort` | `49031` | UDP 控制端口 |
| `DataPort` / `DataPortCount` | `49020` / `10` | UDP 遥测端口段 |
| `PrimaryPort` | `49010` | 旧版兼容端口，`0` 表示关闭 |
| `EnableTcasTraffic` | `1` | 是否允许 TCAS 交通注入 |
| `EnableAiPlanes` | `1` | 是否同时生成可见 AI 机模 |

## 目录结构

```
dllmain.cpp                  插件入口、UDP 遥测/控制、TCAS 注入
LocalWebApi.{h,cpp}          OpenFrequency 兼容 HTTP 服务
XplmWebApiBackend.{h,cpp}    dataref / 命令后端
pch.* / framework.h          预编译头
OpenATC_DataBridge.vcxproj   Visual Studio 工程
OpenATC_DataBridge.ini.example
LICENSE                      MIT 许可证
OPENFREQUENCY_BRIDGE.md      网络协议详细文档
XPSDK430/SDK                 X-Plane SDK 4.3.0（含 license.txt）
```

## 第三方

- X-Plane SDK 4.3.0：许可证见 `XPSDK430/SDK/license.txt`。
- TCAS 交通注入行为参考 OpenFrequency（MIT）的 `OpenFrequencyTrafficBridge.py`。

## License

本插件源码采用 MIT License，见 [LICENSE](LICENSE)。
X-Plane SDK 4.3.0 不属于本许可证范围，使用其自带许可证
（`XPSDK430/SDK/license.txt`）。
