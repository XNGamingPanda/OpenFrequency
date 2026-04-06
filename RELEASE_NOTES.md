# OpenFrequency 3.1 Release Notes

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
