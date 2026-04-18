# OpenFrequency v3.9-beta Release Notes

## 2026-04-18 Update

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
- **Auto-update check**: OpenFrequency checks for a new release 8 seconds after startup and whenever the user clicks "Check for Updates" in Settings.
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

# OpenFrequency v3.5-beta Release Notes

## 2026-04-07 Update

> **Release Date**: 2026-04-07
> **Version**: **v3.5-beta**
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

## 2026-04-05 Update

> **Release Date**: 2026-04-05
> **Version**: **3.1**

This update focuses on simulator integration, airport frequency intelligence, ATIS realism, and dashboard workflow improvements. It also includes major frontend quality-of-life fixes for multilingual use, crew/ATC channel switching, and map presentation.

### New Features

- **X-Plane Local Web API support**: Replaced the legacy XPlaneConnect path with X-Plane 12 Local Web API support in the simulator bridge.
- **Airport frequency service**: Added local caching and lookup for `airports.csv`, `airport-frequencies.csv`, and `runways.csv` from OurAirports.
- **Nearby Airports panel**: Replaced the old radar traffic tab with a nearby airport and frequency browser, including one-click tuning.
- **Per-frequency context memory**: ATC chat history now persists per tuned frequency and restores when returning to a previous channel.
- **Settings UI for simulator selection**: Added frontend simulator provider switching and X-Plane host/port settings without editing JSON manually.
- **Flight plan editing in settings**: Added editable route/origin/destination/alternate/cruise altitude fields with SimBrief import backfill.
- **Automatic SimBrief callsign import**: Callsign can now be derived from SimBrief and written back to runtime state and config.

### ATIS Improvements

- **Real ATIS playback chain**: ATIS requests now trigger METAR fetch, ATIS generation, TTS playback, and comms log output correctly.
- **Repeated ATIS replay**: Re-tuning ATIS will replay the ATIS instead of only playing once.
- **ATIS logging**: Full ATIS text is now written into the communications log, not just shown as a system banner.
- **AviationWeather METAR timing**: ATIS now uses real `reportTime`/`obsTime` timing from AviationWeather.gov instead of generic placeholders.
- **Runway-in-use support**: Added runway selection from `runways.csv`, with runway-in-use included in generated ATIS.
- **Chinese airport bilingual ATIS**: Chinese airports now generate bilingual ATIS output regardless of UI language.
- **Improved phraseology**: Chinese ATIS wording was revised toward a more realistic CAAC-style format, and English ATIS phrasing was tightened toward operational radio style.
- **Display vs speech separation**: ATIS now displays normal digits in the UI while TTS uses aviation-style spoken forms.

### TTS and Language

- **English digit phraseology**: English TTS now uses aviation number pronunciation such as `Tree`, `Fower`, `Fife`, and `Niner`.
- **Digit expansion rules**: English TTS reads numbers digit-by-digit and supports `Hundred`, `Thousand`, and `Decimal` patterns.
- **Japanese voice behavior**: Japanese interaction mode now uses Japanese voices while converting spoken English-like content into Katakana for pronunciation, without changing displayed text.
- **Chinese speech formatting**: Chinese TTS can read displayed values such as visibility, cloud base, QNH, and wind using Chinese aviation reading rules.

### UI and UX

- **Crew channel fixes**: Crew mode text messages no longer leak into ATC; frontend and backend both enforce correct routing.
- **Crew button styling**: Crew radio mode now has proper active-state styling.
- **Multilingual cleanup**: Fixed missing Chinese/Japanese translations for several dynamic dashboard strings and simulator connection states.
- **Dark mode fixes**: Improved dark mode readability for helper text, form labels, and settings guidance text.
- **Altitude map gradient**: Map trail altitude coloring now uses a continuous gradient with a more accurate legend and dynamic redraw.
- **Persisted trail restoration**: Flight path history now restores more reliably after page refresh.
- **Sequential audio playback**: Browser playback now queues incoming audio to prevent overlapping ATIS or radio messages.

### Simulator and Failure Logic

- **Simulator-aware failure injection**: Random failures are now restricted to supported simulators and injected only where backend support exists.
- **X-Plane bridge integration**: SimBridge now follows the configured simulator provider instead of always behaving like MSFS/SimConnect.
- **Improved diagnostics**: Added clearer X-Plane connection and ATIS behavior diagnostics during development.

### Known Limitations

- **Runway selection is heuristic**: Runway-in-use is currently inferred from runway data and wind, not from live airport operational configuration.
- **ATIS phraseology still evolving**: Chinese and English ATIS wording is improved but still not a full real-world phraseology engine.
- **X-Plane Web API required**: X-Plane support depends on the official Local Web API being available and reachable.

---

> **Release Date**: 2026-02-08
> **Status**: **ALPHA** (Expect bugs and rough edges)

This release introduces significant architectural changes, including a new Career Mode and a refactored Crew Communication system. Due to the complexity of these features and known limitations in SimConnect traffic scanning, we are releasing this as an **Alpha** build for community testing and feedback.

## New Features

### Career Mode (Major Update)
Separate your serious flying from casual sessions.
- **Dashboard**: New central hub for managing your pilot career.
- **Job Market**: Real-world route generator with rank-based distance filtering (e.g., PPL limited to <500km).
- **Economy & Licenses**: Bank account tracking, XP rewards, and purchasable pilot licenses (Student -> Master Aviator).
- **Violations**: Flight monitoring system that records infractions (speeding, unstable approach).

### Crew Communication Refactor
A more realistic, role-based interaction system.
- **First Officer (Cockpit)**: Monitors ATC and assists with checklists. Hears both ATC and Intercom.
- **Purser (Cabin)**: Manage passenger comfort and safety. Only hears Intercom.
- **Ambience Control**: Play Boarding/Deboarding environment sounds directly from the UI.

### Emergency System 2.0
More granular control and realism.
- **Probability Settings**: Adjustable frequency (None / Low / Medium / High).
- **Specific Failures**: Alerts now pinpoint specific systems (e.g., "Hydraulic System A", "Engine 1 Fire").
- **Logic Improvements**: Bird strikes only occur when airborne (>100ft).

### UI & UX Enhancements
- **Multi-Language Support**: Full translation support for English, Chinese (Simplified), and Japanese.
- **Clear Track**: New button on the map to clear flight path history.
- **Channel Selector**: Dedicated switch for ATC vs. Crew radio channels.
- **Cabin Emergency**: Distinct visual alert (Red Border) only active during actual emergencies.

---

## Bug Fixes

| Component | Fix |
|-----------|-----|
| **Core** | Fixed `NameError` crash related to `CabinCrew` module. |
| **Career** | Fixed `_save_profile` attribute error in Job Generator. |
| **Logic** | Optimized PPL route generation to prioritize regional airports (125-438km). |
| **UI** | Fixed "Accept Job" button failing to trigger (replaced onclick with event listeners). |
| **API** | Fixed locale loading route to correctly handle `.json` extensions. |
| **Settings** | Fixed PTT binding logic for joystick buttons. |

---

## Known Issues (Alpha)

### Requested by User Feedback
1. **Career Mode Language**: Language settings do not automatically refresh the page; a manual reload is required to apply changes.
2. **Career Dashboard Interaction**: Clicking on career cards (Jobs, Licenses, etc.) may fail to open the corresponding modal windows in certain states.
3. **Crew Interaction**: The crew interaction functions (Purser/FO communication) are currently unstable and may not function as expected.

### General Issues
- **SimConnect Traffic**: AI Traffic scanning is currently simulated (Mock) for stability testing. Real-time injection is planned for Beta.
- **Voice Latency**: LLM response times may vary based on API load.
- **Career Balance**: XP formulas and penalty thresholds are preliminary and may need tuning.

---

## Dependencies

No new Python packages required since v2.5. Ensure you have `ffmpeg` installed for audio features.

```bash
pip install -r requirements.txt
```

---

## Feedback

Please report issues on our GitHub Issues page. Your feedback is critical to moving from Alpha to Beta!
