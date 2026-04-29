# OpenFrequency 📡
> *The AI ATC for Everyone.*

![Version](https://img.shields.io/badge/Status-v3.9--alpha-orange) ![License](https://img.shields.io/badge/License-MIT-green) ![Simulator](https://img.shields.io/badge/Simulator-MSFS%20|%20X--Plane-blue)

> 📖 **[中文文档 README_zh-CN.md](README_zh-CN.md)**

> ⚠️ **Alpha Notice**: v3.9-alpha contains major new features — CPDLC, plugin system, MSI installer, auto-update, local TTS, and more. See [Release Notes](RELEASE_NOTES.md) before upgrading.

**OpenFrequency** is a next-generation, open-source Air Traffic Control system for flight simulators.

Born from the vision of creating a free, accessible, and highly intelligent alternative to paid services like SayIntentions.AI, OpenFrequency aims to democratize realistic simulation. By leveraging powerful Large Language Models (LLMs) like Google Gemini, it brings "human" controllers and cabin crew to your cockpit without the subscription fee.

## Why OpenFrequency? 🚀

Simulation enthusiasts deserve an immersion system that:
1. **Understands Context**: Remembers your request from 5 minutes ago and knows your flight plan.
2. **Speaks Naturally**: No more robotic "One-Two-Three". Hear natural accents, static, and hesitation.
3. **Features Depth**: From emergency checklists to cabin announcements, it covers the full flight experience.
4. **Costs Nothing**: Built on free/affordable APIs. No $30/mo subscriptions.

## Features ✨

### Core ATC Engine
- **🧠 Intelligent Core**: Powered by LLMs (Gemini, Gemma, or OpenAI), handling complex negotiations naturally.
- **📡 CPDLC**: Controller-Pilot Data Link Communications for pre-departure clearance, oceanic, and en-route messages.
- **🗺️ Radar Vectoring**: Issue HDG / ALT / SPD vectors from the dashboard radar panel.
- **🛬 VFR / IFR**: Full visual and instrument flight rule communications.
- **🔁 Auto Busy Level**: Workload adapts automatically to live nearby traffic count.

### ATIS & Airport Intelligence
- **📻 Bilingual ATIS**: Chinese airports generate full bilingual ATIS (English + Chinese) with correct CAAC-style phrasing.
- **🗄️ Metric RVSM**: Automatically switches to metric altitude levels in Chinese airspace.
- **🛣️ Weighted Taxi Routing**: Graph-based taxi path planning with runway crossing penalties, hotspot avoidance, and aircraft size constraints.
- **📋 Instruction Cards**: BeyondATC-style altitude, heading, speed, squawk, and taxi instruction cards.

### Career Mode
- **💼 Career Dashboard**: Job market, XP system, violation tracking, airline contracts, license progression.
- **🔗 SimBrief Integration**: One-click SimBrief dispatch link pre-filled with all mission parameters.
- **✅ Readiness Checks**: Pre-flight validation of aircraft type, position, cold-and-dark state.

### Audio & TTS
- **🎙️ Dual TTS**: Switch between Edge-TTS (cloud) and local streaming TTS model.
- **📦 Model Storage**: TTS and STT models stored in `%APPDATA%\OpenFrequency\models`.
- **📻 Radio Effects**: Configurable band-pass radio noise filter.
- **🔇 PTT Lock on ATIS**: ATIS frequency auto-locks PTT (listen-only).

### Simulator Support
- **MSFS / P3D / FSX** via SimConnect (SDK auto-bundled — no manual download needed)
- **X-Plane 12** via official Local Web API
- **AI Traffic Awareness**: LiveTraffic / MSFS AI aircraft state reading

### Plugin System
- **🔌 Plugin API**: Base class with lifecycle hooks, manifest metadata, dynamic loading.
- **🛒 Addon Installer**: Browse DLC catalog, one-click FlyByWire A32NX download.

### Packaging & Updates
- **📦 MSI Installer**: WiX 4 per-machine MSI with upgrade support.
- **🔄 Auto-Update**: Checks once per day; downloads and verifies new releases in-app.
- **🇨🇳 China Mirror**: All downloads proxied via Cloudflare Workers for faster China access.

### Other Features
- **🚨 Emergency Scenarios**: Bird strikes, engine fires, hydraulic failures with configurable probability.
- **👥 Crew & Cabin**: First Officer + Purser with distinct voices, proactive scenarios, and cabin media.
- **🎯 Head Tracking**: Zero-cost webcam-based head tracking.
- **📱 Responsive UI**: Dark mode dashboard with EN / 中文 / 日本語 support.
- **📊 Crash Telemetry**: PII-sanitized crash reporting (opt-in) and feedback form.

## Download

Go to the [Releases](https://github.com/XNGamingPanda/OpenFrequency/releases) page and download the latest `OpenFrequency-v3.9-alpha-Setup.msi`.

Or use the [Download Page](https://openfrequency.pages.dev) powered by Cloudflare Pages.

## Getting Started 🛠️

### Prerequisites
- Windows 10/11
- Microsoft Flight Simulator 2020/2024 or X-Plane 12
- Google Gemini API Key (Free tier available)

### Option A: MSI Installer (Recommended)
1. Download `OpenFrequency-v3.9-alpha-Setup.msi` from [Releases](https://github.com/XNGamingPanda/OpenFrequency/releases).
2. Run the installer — it creates Start Menu and Desktop shortcuts.
3. On first launch, OpenFrequency creates `%APPDATA%\OpenFrequency\config.json` from the example config.
4. Open the config, add your API key, and start flying.

### Option B: Developer Setup (Git)
```bash
git clone https://github.com/XNGamingPanda/OpenFrequency.git
cd OpenFrequency
pip install -r requirements.txt
# Copy config.example.json → config.json and add your API key
python app.py
```

### Option C: Build from Source
```powershell
# Requires: Python 3.11+, PyInstaller, WiX 4 dotnet tool
# Install WiX: dotnet tool install --global wix

powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
# Output: dist\OpenFrequency-v3.9-alpha-Setup.msi
```

## Roadmap 🗺️

- [x] Basic VFR/IFR Communications
- [x] SimBrief Integration
- [x] Visual Head Tracking
- [x] Emergency Scenarios
- [x] X-Plane Support
- [x] Dark Mode & i18n
- [x] Career Mode
- [x] Crew Management System
- [x] CPDLC Datalink
- [x] Metric RVSM (Chinese Airspace)
- [x] Plugin System & Addon Installer
- [x] MSI Installer
- [x] Auto-Update via Cloudflare Workers
- [x] Local TTS Streaming
- [x] Radar Vectoring
- [ ] Multiplayer Traffic Awareness
- [ ] Career Mode Leaderboards

## License 📄

MIT License — see [LICENSE](LICENSE) for details.
