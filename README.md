# OpenFrequency 📡
> *The AI ATC for Everyone.*

![Banner](https://img.shields.io/badge/Status-v3.0--alpha-orange) ![License](https://img.shields.io/badge/License-MIT-green) ![Simulator](https://img.shields.io/badge/Simulator-MSFS%20|%20P3D%20|%20X--Plane-blue)

> ⚠️ **Stability Warning**: This version (v3.0-alpha) contains experimental features and known issues (see [Release Notes](RELEASE_NOTES.md)). If you prioritize stability, we strongly recommend using the previous **v2.5-beta** release.

**OpenFrequency** is a next-generation, open-source Air Traffic Control system for flight simulators.

Born from the vision of creating a free, accessible, and highly intelligent alternative to paid services like SayIntentions.AI, OpenFrequency aims to democratize realistic simulation. By leveraging powerful Large Language Models (LLMs) like Google Gemini, it brings "human" controllers and cabin crew to your cockpit without the subscription fee.

## Why OpenFrequency? 🚀

Simulation enthusiasts deserve an immersion system that:
1.  **Understands Context**: Remembers your request from 5 minutes ago and knows your flight plan.
2.  **Speaks Naturally**: No more robotic "One-Two-Three". Hear natural accents, static, and hesitation.
3.  **Features Depth**: From emergency checklists to cabin announcements, it covers the full flight experience.
4.  **Costs Nothing**: Built on free/affordable APIs. No $30/mo subscriptions.

## Features ✨

*   **🧠 Intelligent Core**: Powered by LLMs (Gemini, Gemma, or OpenAI), handling complex negotiations naturally.
*   **wu Career Mode (Alpha)**:
    *   **XP System**: Earn points for smooth landings and safe operations.
    *   **Violation Tracking**: Penalties for speeding, hard landings, or unstable approaches.
    *   **Free Flight**: Toggle off for casual flying.
*   **👥 Real Crew Experience**:
    *   **Role Separation**: First Officer assists in cockpit; Purser manages the cabin.
    *   **Ambience**: Play boarding music/announcements directly from the UI.
    *   **Intercom**: Chat naturally with your crew via text or voice.
*   **🚨 Emergency Scenarios 2.0**:
    *   Bird strikes, engine fires, hydraulic failures.
    *   Configurable probability levels (None/Low/Medium/High).
    *   Specific system failure alerts.
*   **🌍 Multi-Simulator Support**:
    *   **MSFS** / **Prepar3D** / **FSX** via SimConnect
    *   **X-Plane 11/12** via XPlaneConnect (UDP)
*   **🎯 Visual Head Tracking**: Zero-cost webcam-based head tracking.
*   **🗣️ Voice of the Sky**: High-quality Edge-TTS voices with real-time radio effects.
*   **📱 Glass Cockpit UI**: Responsive web dashboard with dark mode and internationalization (EN/CN/JP).

## Getting Started 🛠️

### Prerequisites
*   Windows 10/11 (macOS/Linux for X-Plane)
*   Microsoft Flight Simulator / Prepar3D / X-Plane
*   Google Gemini API Key (Free tier available)

### Installation

#### Option A: All-in-One Pack (Recommended)
1.  Go to the [Releases](https://github.com/XNGamingPanda/OpenFrequency/releases) page.
2.  Download the latest `v3.0-alpha` zip package.
3.  Extract, rename `config.example.json` to `config.json`, add your API key, and run `python app.py`.

#### Option B: Developer Setup (Git)
1.  Clone the repo:
    ```bash
    git clone https://github.com/XNGamingPanda/OpenFrequency.git
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Configure `config.json` with your API key.
4.  Fly:
    ```bash
    python app.py
    ```

## Roadmap 🗺️

*   [x] Basic VFR/IFR Communications
*   [x] SimBrief Integration
*   [x] Visual Head Tracking
*   [x] Emergency Scenarios
*   [x] X-Plane Support
*   [x] Dark Mode & i18n
*   [x] **Career Mode (Basic)**
*   [x] **Crew Management System**
*   [ ] Multiplayer Traffic Awareness
*   [ ] Vectoring Logic
*   [ ] Career Mode Leaderboards

## License 📄

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
