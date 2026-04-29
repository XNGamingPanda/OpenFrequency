# OpenFrequency 📡
> *面向所有人的 AI 空中交通管制*

![版本](https://img.shields.io/badge/状态-v3.9--beta-orange) ![许可证](https://img.shields.io/badge/许可证-MIT-green) ![模拟器](https://img.shields.io/badge/模拟器-MSFS%20|%20X--Plane-blue)

> 📖 **[English README](README.md)**

> ⚠️ **Alpha 提示**：v3.9-beta 包含大量新功能——CPDLC、插件系统、MSI 安装程序、自动更新、本地 TTS 等。升级前请阅读[发布说明](RELEASE_NOTES_zh-CN.md)。

**OpenFrequency** 是面向飞行模拟爱好者的新一代开源空中交通管制系统。

它的愿景是打造一个免费、易用、高度智能的替代品，对标 SayIntentions.AI 等付费服务，让每个人都能享受真实的 ATC 模拟体验。通过接入 Google Gemini 等强大的大语言模型，OpenFrequency 在驾驶舱中带来"真人管制员"和"机组成员"的交互体验——无需月费。

## 为什么选择 OpenFrequency？🚀

飞行模拟爱好者值得拥有一个真正懂你的沉浸系统：
1. **理解上下文**：记住你 5 分钟前的请求，知晓你的飞行计划。
2. **自然语音**：告别机械的"One-Two-Three"，听到真实口音、静电和停顿。
3. **功能深度**：从紧急清单到客舱广播，覆盖完整飞行体验。
4. **完全免费**：基于免费/低价 API 构建，无需每月 30 美元的订阅费。

## 功能特性 ✨

### 核心 ATC 引擎
- **🧠 智能核心**：由大语言模型（Gemini、Gemma 或 OpenAI）驱动，自然处理复杂 ATC 交互。
- **📡 CPDLC**：管制员-飞行员数据链通信，支持起飞前放行、洋区及航路数据链消息。
- **🗺️ 雷达引导**：从仪表盘雷达面板下达 HDG / ALT / SPD 引导指令。
- **🛬 VFR / IFR**：目视和仪表飞行规则通信均支持。
- **🔁 自动忙碌等级**：根据附近飞机实时数量自动调整工作负载。

### ATIS 与机场智能
- **📻 双语 ATIS**：中国机场自动生成英文 + 中文双语 ATIS，符合 CAAC 用语规范。
- **🗄️ 米制 RVSM**：在中国空域自动切换为公制飞行高度层。
- **🛣️ 加权滑行寻路**：基于图算法的滑行路径规划，考虑跑道穿越惩罚、热点规避及机型限制。
- **📋 指令卡片**：类似 BeyondATC 的高度、航向、速度、应答机和滑行指令卡。

### 生涯模式
- **💼 生涯仪表盘**：任务市场、XP 系统、违规记录、航司签约、执照晋升。
- **🔗 SimBrief 集成**：一键生成预填所有任务参数的 SimBrief 调度链接。
- **✅ 飞行准备检查**：起飞前验证机型、位置、冷舱状态。

### 音频与 TTS
- **🎙️ 双 TTS 引擎**：可在 Edge-TTS（云端）和本地流式 TTS 模型之间切换。
- **📦 模型存储路径**：TTS 和 STT 模型统一存放于 `%APPDATA%\OpenFrequency\models`。
- **📻 无线电效果**：可配置带通滤波无线电噪声效果。
- **🔇 ATIS PTT 锁定**：调到 ATIS 频率时自动锁定 PTT（仅收听）。

### 模拟器支持
- **MSFS / P3D / FSX**：通过 SimConnect（SDK 自动打包，无需手动下载）
- **X-Plane 12**：通过官方 Local Web API
- **AI 流量感知**：支持读取 LiveTraffic / MSFS AI 飞机状态

### 插件系统
- **🔌 插件 API**：基类提供生命周期钩子、manifest 元数据和动态加载。
- **🛒 Addon 安装器**：浏览 DLC 目录，一键下载 FlyByWire A32NX。

### 打包与更新
- **📦 MSI 安装程序**：WiX 4 全机安装，支持大版本升级。
- **🔄 自动更新**：每天检查一次；在应用内下载并验证新版本。
- **🇨🇳 国内加速**：所有下载通过 Cloudflare Workers 代理，改善国内访问速度。

### 其他功能
- **🚨 紧急事件**：鸟击、发动机火警、液压故障，概率可调。
- **👥 机组与客舱**：副驾驶 + 乘务长各有独立声线，支持主动场景和客舱媒体。
- **🎯 头部追踪**：基于摄像头的零成本头部追踪。
- **📱 响应式界面**：深色模式仪表盘，支持中文 / English / 日本語。
- **📊 崩溃遥测**：PII 脱敏的崩溃上报（需用户同意）及反馈表单。

## 下载

前往 [Releases 页面](https://github.com/XNGamingPanda/OpenFrequency/releases) 下载最新的 `OpenFrequency-v3.9-beta-Setup.msi`。

也可以使用由 Cloudflare Pages 提供的[下载页面](https://openfrequency.pages.dev)。

## 快速上手 🛠️

### 运行环境
- Windows 10/11
- 微软模拟飞行 2020/2024 或 X-Plane 12
- Google Gemini API 密钥（免费额度可用）

### 方式 A：MSI 安装程序（推荐）
1. 从 [Releases](https://github.com/XNGamingPanda/OpenFrequency/releases) 下载 `OpenFrequency-v3.9-beta-Setup.msi`。
2. 运行安装程序，将创建开始菜单和桌面快捷方式。
3. 首次启动时，OpenFrequency 会从示例配置创建 `%APPDATA%\OpenFrequency\config.json`。
4. 打开配置文件，填入 API 密钥，即可开始飞行。

### 方式 B：开发者模式（Git）
```bash
git clone https://github.com/XNGamingPanda/OpenFrequency.git
cd OpenFrequency
pip install -r requirements.txt
# 将 config.example.json 复制为 config.json 并填入 API 密钥
python app.py
```

### 方式 C：从源码构建
```powershell
# 需要：Python 3.11+、PyInstaller、WiX 4 dotnet 工具
# 安装 WiX：dotnet tool install --global wix

powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
# 输出：dist\OpenFrequency-v3.9-beta-Setup.msi
```

## 开发路线图 🗺️

- [x] 基础 VFR/IFR 通信
- [x] SimBrief 集成
- [x] 视觉头部追踪
- [x] 紧急事件系统
- [x] X-Plane 支持
- [x] 深色模式与国际化
- [x] 生涯模式
- [x] 机组管理系统
- [x] CPDLC 数据链
- [x] 米制 RVSM（中国空域）
- [x] 插件系统与 Addon 安装器
- [x] MSI 安装程序
- [x] 通过 Cloudflare Workers 自动更新
- [x] 本地流式 TTS
- [x] 雷达引导
- [ ] 多人流量感知
- [ ] 生涯模式排行榜

## 许可证 📄

MIT 许可证 — 详见 [LICENSE](LICENSE)。
