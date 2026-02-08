# OpenFrequency v3.0-alpha Release Notes 🛠️

> **Release Date**: 2026-02-08
> **Status**: **ALPHA** (Expect bugs and rough edges)

This release introduces significant architectural changes, including a new Career Mode and a refactored Crew Communication system. Due to the complexity of these features and known limitations in SimConnect traffic scanning, we are releasing this as an **Alpha** build for community testing and feedback.

## ✨ New Features

### 🎖️ Career Mode (Major Update)
Separate your serious flying from casual sessions.
- **Dashboard**: New central hub for managing your pilot career.
- **Job Market**: Real-world route generator with rank-based distance filtering (e.g., PPL limited to <500km).
- **Economy & Licenses**: Bank account tracking, XP rewards, and purchasable pilot licenses (Student -> Master Aviator).
- **Violations**: Flight monitoring system that records infractions (speeding, unstable approach).

### 👥 Crew Communication Refactor
A more realistic, role-based interaction system.
- **First Officer (Cockpit)**: Monitors ATC and assists with checklists. Hears both ATC and Intercom.
- **Purser (Cabin)**: Manage passenger comfort and safety. Only hears Intercom.
- **Ambience Control**: Play Boarding/Deboarding environment sounds directly from the UI.

### 🚨 Emergency System 2.0
More granular control and realism.
- **Probability Settings**: Adjustable frequency (None / Low / Medium / High).
- **Specific Failures**: Alerts now pinpoint specific systems (e.g., "Hydraulic System A", "Engine 1 Fire").
- **Logic Improvements**: Bird strikes only occur when airborne (>100ft).

### 🎨 UI & UX Enhancements
- **Multi-Language Support**: Full translation support for English, Chinese (Simplified), and Japanese.
- **Clear Track**: New button on the map to clear flight path history.
- **Channel Selector**: Dedicated switch for ATC vs. Crew radio channels.
- **Cabin Emergency**: Distinct visual alert (Red Border) only active during actual emergencies.

---

## 🐛 Bug Fixes

| Component | Fix |
|-----------|-----|
| **Core** | Fixed `NameError` crash related to `CabinCrew` module. |
| **Career** | Fixed `_save_profile` attribute error in Job Generator. |
| **Logic** | Optimized PPL route generation to prioritize regional airports (125-438km). |
| **UI** | Fixed "Accept Job" button failing to trigger (replaced onclick with event listeners). |
| **API** | Fixed locale loading route to correctly handle `.json` extensions. |
| **Settings** | Fixed PTT binding logic for joystick buttons. |

---

## ⚠️ Known Issues (Alpha)

### Requested by User Feedback:
1.  **Career Mode Language**: Language settings do not automatically refresh the page; a manual reload is required to apply changes.
2.  **Career Dashboard Interaction**: Clicking on career cards (Jobs, Licenses, etc.) may fail to open the corresponding modal windows in certain states.
3.  **Crew Interaction**: The crew interaction functions (Purser/FO communication) are currently unstable and may not function as expected.

### General Issues:
- **SimConnect Traffic**: AI Traffic scanning is currently simulated (Mock) for stability testing. Real-time injection is planned for Beta.
- **Voice Latency**: LLM response times may vary based on API load.
- **Career Balance**: XP formulas and penalty thresholds are preliminary and may need tuning.

---

## 📦 Dependencies

No new Python packages required since v2.5. Ensure you have `ffmpeg` installed for audio features.

```bash
pip install -r requirements.txt
```

---

## 🙏 Feedback

Please report issues on our GitHub Issues page. Your feedback is critical to moving from Alpha to Beta!
