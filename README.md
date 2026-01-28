# OpenFrequency 📡
> *The AI ATC for Everyone.*

![Banner](https://img.shields.io/badge/Status-Beta-blue) ![License](https://img.shields.io/badge/License-MIT-green)

**OpenFrequency** is a next-generation, open-source Air Traffic Control system for Microsoft Flight Simulator. 

Born from the vision of creating a free, accessible, and highly intelligent alternative to paid services like SayIntentions.AI, OpenFrequency aims to democratize realistic simulation. By leveraging powerful Large Language Models (LLMs) like Google Gemini, it brings "human" controllers to your cockpit without the subscription fee.

## Why OpenFrequency? 🚀

Simulation enthusiasts deserve an ATC that:
1.  **Understands Context**: Remembers your request from 5 minutes ago and knows your flight plan.
2.  **Speaks Naturally**: No more robotic "One-Two-Three". Hear natural accents, static, and hesitation.
3.  **Costs Nothing**: Built on free/affordable APIs. No $30/mo subscriptions.

## Features ✨

*   **🧠 Intelligent Core**: Powered by LLMs (Gemini 2.0, Gemma, or OpenAI), it handles emergencies, VFR flight following, and complex negotiations naturally.
*   **🌍 Live Awareness**:
    *   **Real Weather**: reads live METARs (AviationWeather.gov) to give you accurate winds and altimeter settings.
    *   **Real Position**: Connects directly to MSFS via SimConnect for precise tracking.
*   **🗣️ Voice of the Sky**:
    *   **Edge-TTS Integration**: High-quality, neural voices for free.
    *   **Immersive Audio Engine**: Real-time radio static, VHF distortion, and background chatter.
*   **📱 Glass Cockpit UI**: A responsive web dashboard works on your iPad or second monitor, showing your flight path and comms log.
*   **📡 Proactive Control**: Unlike default ATC, OpenFrequency watches you. Deviate from altitude? Just like a real controller, **it will call you**.

## Getting Started 🛠️

### Prerequisites
*   Windows 10/11
*   Microsoft Flight Simulator
*   Google Gemini API Key (Free tier available)

### Installation

#### Option A: All-in-One Pack (Recommended)
1.  Go to the [Releases](https://github.com/XNGamingPanda/OpenFrequency/releases) page.
2.  Download the latest `zip` package (includes FFmpeg and Models).
3.  Extract, configure `config.json`, and run!

#### Option B: Developer Setup (Git)
1.  Clone the repo:
    ```bash
    git clone https://github.com/XNGamingPanda/OpenFrequency.git
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Configure:
    *   Rename `config.example.json` to `config.json` and add your API key.
4.  Fly:
    ```bash
    python app.py
    ```

## Roadmap 🗺️

*   [x] Basic VFR/IFR Communications
*   [x] SimBrief Integration
*   [x] Proactive ATC Monitoring
*   [ ] Multiplayer Traffic Awareness
*   [ ] Vectoring Logic

## License 📄

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. We believe in open skies and open code.
