# OpenFrequency Release Notes

---

# v3.9-alpha — 2026-04-29

> **Release Date**: 2026-04-29
> **Version**: **v3.9-alpha**
> **Status**: **Alpha**

<!-- en -->
This release is a major feature milestone. It introduces CPDLC datalink, metric RVSM for Chinese airspace, a full MSI installer pipeline, community plugin support, radar vectoring, auto-update via Cloudflare Workers, crash telemetry, two-tier LLM support, local TTS streaming, and dozens of bug fixes accumulated since v3.5-beta.
<!-- /en -->

<!-- zh -->
本版本是一次重大功能里程碑，引入了 CPDLC 数字放行通信、中国空域米制 RVSM 自动切换、完整 MSI 安装程序构建流程、社区插件支持、雷达引导、通过 Cloudflare Workers 自动更新、崩溃遥测上报、双层 LLM 配置、本地流式 TTS，以及自 v3.5-beta 以来积累的数十项错误修复。
<!-- /zh -->

---

## Bug Fixes

| Area | Fix |
|------|-----|
| Network | LAN devices could not access the dashboard |
| Career | Callsign was locked to the career mission after switching to free flight |
| Packaging | Some user data directories were not found in compiled builds |
| ATIS | Information letter was always "Alpha" on every fetch |
| ATIS | Some airport names were read as raw ICAO codes instead of local names |
| ATIS | ATIS was not re-announced (now replays in comms log on re-tune unless the content has already been shown) |
| Navigation | VFR guidance improved |
| Packaging | The STT module briefly flashed a console window in compiled builds |
| Ground routing | Airport auto-routing existed but was not reachable from the UI |
| Dashboard | SimBrief route prediction did not auto-display after becoming airborne |
| ATC | ATC proactive speaking logic improved |
| Audio | Radio noise/static effect improved |
| ATC | ATC frequency knowledge improved — ATC now knows actual assigned frequencies better |
| X-Plane | Frequency injection format corrected for X-Plane 12 aircraft model COM tuning |
| Instruments | V/S (vertical speed) calculation fixed |
| X-Plane | LiveTraffic AI aircraft status could not be read in X-Plane 12 |

---

## New Features

### MSI Installer
- WiX 4 MSI build pipeline via `installer/OpenFrequency.wxs` and `installer/build_installer.ps1`.
- Per-machine install with Start Menu and Desktop shortcuts, major-upgrade support, and Programs & Features entry.
- `version.txt` is the single source of truth for version numbers.

### CPDLC
- Controller–Pilot Data Link Communications support.
- Handles pre-departure clearance, oceanic, and en-route datalink messages.

### Metric RVSM for Chinese Airspace
- Automatically switches to metric altitude (metres) when operating in Chinese RVSM airspace.
- Correct metric flight levels used in ATC phraseology and clearances.

### Quick-Reply Templates
- Most common ATC read-backs are now pre-populated from templates.
- Reduces input time and improves phraseology consistency.

### Dual LLM Model Support
- Configure a lightweight model (fast, cheap) and a reasoning model (slower, higher quality).
- Routine read-backs use the lightweight model; complex clearances and ambiguous situations escalate to the reasoning model.

### Local Streaming TTS
- Support for switching between Edge-TTS (cloud) and a local streaming text-to-speech model.
- Local model files stored under `%APPDATA%\OpenFrequency\models`.

### Radar Vectoring
- ATC radar guidance panel on the dashboard.
- HDG / ALT / SPD vector inputs with live dispatch to the ATC logic engine.

### Plugin System
- `OpenFrequencyPlugin` base class with lifecycle hooks (`on_load`, `on_unload`, `on_event`, `on_config_update`).
- Plugin Manager discovers and loads plugins from the `plugins/` directory.
- DLC catalog browser built in; one-click FlyByWire A32NX download to MSFS.

### Crash Telemetry & Feedback
- Unhandled exceptions are PII-sanitised and (with consent) sent to Cloudflare Workers for analysis.
- Manual crash upload and user feedback form available in Settings.
- Reports received at the Workers `/api/crash` and `/api/feedback` endpoints.

### Auto-Update via Cloudflare Workers
- Version check throttled to once per 24 hours.
- Release metadata and installer assets proxied through Workers for faster China downloads.
- SHA-256 verification before launching the downloaded installer.

### Non-English ATC Toggle
- New "Allow non-English ATC comms (international flights)" switch for non-Chinese international airports.
- Disabled by default; labelled as non-realistic.

### Auto Busy / Standby
- When "Auto Busy Level" is enabled, workload level is derived from live nearby aircraft count.
- Traffic density dropdown hidden when auto-busy is active.

### SimConnect SDK Auto-Bundling
- `SimConnect.dll` from the PyPI package is automatically collected at build time — no manual download needed.

---

## Cloudflare Configuration Guide

### Workers (`workers/workers.js`)

The Workers backend handles version checks, asset proxying, crash reports, feedback, and usage pings.

**Steps:**

```
# 1. Install Wrangler
npm install -g wrangler

# 2. Authenticate
wrangler login

# 3. Create KV namespace
wrangler kv:namespace create OF_KV
# → Copy the returned id into workers/wrangler.toml  [[kv_namespaces]] id

wrangler kv:namespace create OF_KV --preview
# → Copy into preview_id

# 4. Set secrets (never commit)
wrangler secret put CLIENT_TOKEN     # shared token used by the app
wrangler secret put GITHUB_TOKEN     # optional — raises GH API rate limit

# 5. Edit workers/wrangler.toml
#    Set GITHUB_OWNER and GITHUB_REPO to your repository

# 6. Deploy
cd workers
wrangler deploy
```

**Environment variables in `wrangler.toml`:**

| Variable | Purpose |
|----------|---------|
| `GITHUB_OWNER` | GitHub repo owner |
| `GITHUB_REPO` | GitHub repo name |
| `MIN_REQUIRED_VERSION` | Force-update floor version (semver) |
| `CLIENT_TOKEN` *(secret)* | Token the app sends in `X-OF-Token` header |
| `GITHUB_TOKEN` *(secret)* | GitHub PAT — optional, raises rate limit |

### Pages (`workers/pages/index.html`)

The download landing page is a static site deployed to Cloudflare Pages.

```
# In the Cloudflare dashboard:
# 1. Pages → Create project → Connect to Git (or upload directly)
# 2. Set build output directory to:  workers/pages
# 3. No build command needed (pure static HTML)
# 4. Set the environment variable WORKERS_URL to your Workers subdomain:
#       e.g. https://openfrequency-api.<your-account>.workers.dev
#    (or update the download URL directly in index.html)
```

The download button on the page calls `GET /pub/dl/latest` on the Workers, which redirects to the latest MSI asset on GitHub.

---

## SHA-256 Checksums

```
<!-- sha256 block inserted by release workflow -->
```

---

# v3.9-beta — 2026-04-18

> **Release Date**: 2026-04-18
> **Version**: **v3.9-beta**
> **Status**: **Beta**

This release brings cloud-connected telemetry and auto-update infrastructure, international language support for non-Chinese ATC comms, traffic-aware automatic workload management, automatic SimConnect SDK bundling, a new radar vectoring panel, and a WiX 4 MSI installer pipeline.

<!-- sha256sum placeholder — filled by CI -->
<!-- zh -->
此版本新增云端崩溃上报、自动检测更新、国际非英语 ATC 通话开关、流量自适应待机、SimConnect 自动打包、雷达引导面板以及 WiX 4 MSI 安装程序。
<!-- /zh -->

### Cloud Services & Auto-Update

- **Crash reporting**: Unhandled exceptions and thread crashes are silently captured, PII-sanitized, and (with user consent) uploaded to the OpenFrequency cloud for analysis. Opt-out available in Settings → Privacy & Updates.
- **Manual log upload**: Users can click "Upload Recent Crash" in Settings to manually send a sanitized crash report or log excerpt at any time.
- **Auto-update check**: OpenFrequency checks for a new release once per day and whenever the user clicks "Check for Updates" in Settings.
- **In-app download**: New releases are downloaded directly inside the app with a progress bar; SHA-256 checksum is verified before the installer is launched.
- **China download acceleration**: All version metadata and release assets are proxied through the OpenFrequency Cloudflare Workers endpoint to improve download speeds.
- **Feedback form**: A built-in feedback form in Settings lets users submit bug reports or suggestions without leaving the app.
- **Privacy**: API keys, Bearer tokens, Windows user paths, and email addresses are stripped from all uploads. Sim telemetry (aircraft, airport, phase) is included only when "Include sim info" is enabled.

### International ATC Language Support

- **Non-English ATC toggle**: A new "Allow non-English ATC comms (international flights)" switch in Settings lets the AI respond in the local language for non-Chinese international airports. Disabled by default; labelled as non-realistic.
- **Japanese language path**: When Japanese STT language is active and the toggle is on, ATC and crew use Japanese phrasing.
- **Unchanged ICAO default**: International flights with the toggle off continue to use standard English-only ICAO phraseology.

### Traffic-Aware Auto-Busy / Standby

- **Auto-busy level**: When "Auto Busy Level" is enabled, the workload simulator derives `silent / low / medium / high` from the live nearby aircraft count (0 / 1–3 / 4–10 / 11+).
- **Live dashboard indicator**: The Settings page shows the current effective busy level and aircraft count, updated in real time via Socket.IO.
- **Traffic thresholds**: Standby probability and ignore probability are now traffic-density-aware, reducing unnecessary ATC responses in very quiet airspace.

### SimConnect SDK Auto-Bundling

- **Automatic DLL inclusion**: The `SimConnect.dll` shipped inside the `SimConnect` PyPI package is now automatically collected and bundled at build time. No manual download or PATH setup required.
- **Graceful fallback**: If `SimConnect` is not installed in the build environment, packaging still succeeds — the connector is simply unavailable at runtime.

### Radar Vector Follow Panel

- **New 📡 Radar Follow panel** on the dashboard: a toggle switch enables radar vectoring mode.
- **Manual vector inputs**: HDG, ALT, and SPD fields with ▶ send buttons let controllers issue individual vectors; Enter key also triggers send.
- **Socket events**: `set_radar_vector_mode` and `manual_radar_vector` events are dispatched to the backend for integration with the ATC logic engine.

### Plugin System

- **Plugin API**: `OpenFrequencyPlugin` base class with lifecycle hooks (`on_load`, `on_unload`, `on_event`, `on_config_update`).
- **Plugin Manager**: Discovers and dynamically loads plugins from the `plugins/` directory; supports manifest-based metadata.
- **Addon Installer**: One-click DLC / FlyByWire A32NX installer accessible from the Plugins page.

### MSI Installer Pipeline

- **WiX 4 MSI**: `installer/OpenFrequency.wxs` defines a full per-machine MSI with Start Menu + Desktop shortcuts, major-upgrade support, and Programs & Features entry.
- **Build script**: `installer/build_installer.ps1` reads `version.txt`, runs PyInstaller, then produces `dist/OpenFrequency-{version}-Setup.msi` in one command.
- **Version injection**: `$(env.OF_VERSION)` is passed from the build script to WiX via `-d` flag; `version.txt` is the single source of truth.

### SHA-256 Checksums

```
<!-- sha256 block inserted by release workflow -->
```

---

# v3.9-alpha (prior) — 2026-04-07

> **Release Date**: 2026-04-07
> **Version**: **v3.9-alpha**
> **Status**: **Beta**. This build is more complete than the 3.1 alpha line, but several systems remain under active tuning.

This update is a broad beta milestone focused on making OpenFrequency easier to run, more useful in career flights, and more tightly integrated with X-Plane and live airport data. The headline changes are the new packaged Windows build path, a rebuilt career workflow, richer SimBrief integration, improved airport ground intelligence, and more proactive ATC behavior.

### Desktop Packaging

- **Windows EXE packaging**: Added PyInstaller packaging for a Windows desktop build while keeping the existing Flask / Socket.IO / HTML architecture.
- **Desktop launcher**: Added a launcher that starts the local web server and opens the dashboard automatically.
- **Hidden-console runtime**: The packaged app can run without leaving a console window open.
- **Console debug build**: A separate console build remains available for troubleshooting.
- **Tray controls**: Added a system tray icon with dashboard, logs, and exit actions.
- **Runtime log path**: Packaged builds now write logs to `%APPDATA%\OpenFrequency\logs` instead of writing into the bundled app directory.
- **Config handling**: The packaged build excludes the local `config.json`; runtime config is created from `config.example.json` in the user data directory.
- **Resource filtering**: Temporary files, logs, user career data, debug audio, and local error files are excluded from packaging while required models and app assets are included.

### Career Mode Rework

- **Career dashboard fixes**: Fixed duplicate available-job labels and cleaned up the job market flow.
- **Automatic dashboard handoff**: Accepting a job now takes the pilot directly into the career dashboard flow.
- **Pilot nickname**: The displayed `STUDENT01` style pilot nickname is now editable and separated from operational flight callsigns.
- **Airline contracts**: Career jobs now require signing with a regional airline first; future jobs use that airline's callsign until the pilot transfers.
- **Regional airline pools**: Job generation now selects airline operators that better match the current airport region.
- **Passenger vs cargo consistency**: Cargo operators such as FDX no longer generate passenger missions, and passenger airlines no longer generate cargo-only jobs by default.
- **Installed aircraft discovery**: Career jobs now use installed simulator aircraft where possible instead of relying only on a small fixed aircraft list.
- **License-aware aircraft selection**: The aircraft pool is filtered by the pilot's license level.
- **Career readiness checks**: Before a career flight, the app checks origin airport, ground state, stopped state, aircraft type, and cold-and-dark readiness.
- **Repeated readiness display**: Career readiness warnings are now refreshed when re-entering the dashboard, so incorrect aircraft or location states remain visible.
- **Career callsign lock**: Active career jobs lock the runtime callsign to the mission callsign and prevent SimBrief or settings from overriding it.

### SimBrief and Flight Planning

- **Career SimBrief route links**: Career job cards and the active-job panel now include a SimBrief Dispatch URL prefilled with origin, destination, aircraft type, airline, flight number, callsign, flight type, and IFR rules.
- **Dashboard import flow**: Career preparation prompts now guide users to generate a SimBrief route and then import the OFP using the SimBrief username saved in Settings.
- **Active job route fallback**: Active jobs without an existing route use `DIRECT` internally until a SimBrief OFP is imported.
- **Legacy active-job compatibility**: Existing active career jobs are dynamically enriched with SimBrief route links when shown to the UI.

### X-Plane and Simulator Integration

- **X-Plane Local Web API path**: X-Plane integration continues to use the official Local Web API instead of the legacy XPlaneConnect dependency.
- **Aircraft identity detection**: X-Plane now reports current aircraft identity from aircraft datarefs where available; MSFS reads aircraft title/model through SimConnect.
- **COM frequency sync**: Simulator-side frequency changes can update OpenFrequency's ATC context without fighting browser-side tuning.
- **Connection status refresh**: Dashboard and mobile cockpit periodically refresh simulator connection state so disconnected simulators do not remain shown as connected.
- **Failure injection tuning**: Random failure rates were reduced, low mode is less aggressive, and X-Plane failure injection now only alerts when the simulator write succeeds.

### Airport Data, Ground Routing, and EFB Features

- **Simulator-native ground data**: X-Plane airport ground layout is read from `apt.dat` instead of Little Navmap as the primary source.
- **OpenStreetMap ground data option**: Added OSM / Overpass support for taxiways, aprons, runways, and parking nodes as a third-party ground source.
- **Ground data source settings**: Settings can choose simulator-native or third-party sources for frequency and ground layout data.
- **Weighted taxi routing**: Taxi route planning now models airports as a graph and includes penalties for runway crossings, hotspots, turn complexity, low visibility, and larger aircraft constraints.
- **Ground ATC awareness**: Ground ATC prompts now receive taxi layout summaries and suggested routes instead of inventing taxiway names blindly.
- **Instruction cards**: Added BeyondATC-style latest instruction cards for altitude, heading, speed, QNH/altimeter, approach, frequency, squawk, and taxi route.
- **Taxiway map highlighting**: Taxi instructions can highlight matching taxiway segments on the dashboard map.
- **Fit navigation view**: Added a map control to fit the view to the highlighted navigation path or flight trail.

### ATC, Crew, Cabin, and Audio

- **ATC proactive monitor**: Added a local-rule-first ATC monitor that tracks altitude, heading, speed, handoff, hold-short, and clearance-related deviations before asking the AI whether to speak.
- **Frequency switch cleanup**: Switching frequencies now stops active audio and avoids duplicate `SYSTEM: Tuned` messages from browser/simulator echo.
- **Crew routing fixes**: Crew mode text and voice inputs are forced to the cabin/crew path and no longer leak to ATC.
- **Crew voice separation**: First Officer and Purser responses can use distinct voices.
- **Crew proactive scenarios**: Crew can initiate context-aware messages for cabin readiness, climb, service, descent prep, and landing rollout.
- **Cabin media support**: Cabin announcements can reference local audio or video files; videos display in a small dashboard media window.
- **ANA template support**: Added support for ANA-style cabin media entries such as safety and disembarking videos.

### Known Limitations

- **MSFS native airport BGL parsing**: MSFS scenery package scanning exists, but compiled BGL decoding is not yet a full native ground-layout solution; OSM remains the fallback.
- **Ground routing data quality**: OSM airport data can be incomplete or inconsistent at some airports, so highlighted taxi paths may be best-effort.
- **Media licensing**: Users are responsible for ensuring cabin announcement audio/video files are legally obtained and usable.
- **Career balance**: Rewards, licenses, and operator availability are still beta tuning areas.

---

# v3.1 — 2026-04-05

> **Release Date**: 2026-04-05

This update focuses on simulator integration, airport frequency intelligence, ATIS realism, and dashboard workflow improvements.

### New Features

- X-Plane Local Web API support.
- Airport frequency service with local CSV caching.
- Nearby Airports panel replacing the old radar traffic tab.
- Per-frequency context memory.
- Settings UI for simulator selection and X-Plane host/port.
- Flight plan editing in settings with SimBrief import backfill.
- Automatic SimBrief callsign import.

### ATIS Improvements

- Real ATIS playback chain.
- Bilingual ATIS for Chinese airports.
- CAAC-style Chinese ATIS phraseology.
- Display vs speech separation (digits in UI, aviation spoken forms in TTS).

### TTS and Language

- English digit phraseology (Tree, Fower, Fife, Niner).
- Digit expansion rules (Hundred, Thousand, Decimal).
- Japanese Katakana voice path.
- Chinese aviation reading rules for TTS.

### Known Limitations

- Runway selection is heuristic.
- X-Plane Web API required.

---

# v2.5 / v2.0 Alpha — 2026-02-08

> **Status**: **ALPHA**

Initial public alpha. Introduced Career Mode, Crew Communication refactor, Emergency System 2.0, multi-language support, and basic flight monitoring.

See the archived release notes in git history for full details.
