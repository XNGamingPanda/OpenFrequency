# OpenFrequency v2.0 Beta Release 🚀

We are thrilled to announce the first public beta of **OpenFrequency**, the open-source AI ATC that brings your flight simulator to life!

## What's New?

*   **🎙️ Natural Conversation**: Talk to ATC naturally using **Google Gemini** (or local LLMs). No strict phraseology required.
*   **📡 SimConnect Integration**: Direct connection to MSFS for real-time Altitude, Heading, and Position tracking.
*   **🌍 Real-World Weather**: Integrated live METAR data from AviationWeather.gov. ATC knows the *actual* winds and pressure at your location.
*   **📱 Glass Cockpit Dashboard**: A beautiful, responsive web UI for PC, Tablet, or Phone. Includes a moving map with flight path visualization.
*   **🧠 Context Awareness**:
    *   **Short-term Memory**: Remembers your previous requests and instructions.
    *   **SimBrief Support**: Imports your OFP to understand your Route, Origin, and Destination.
    *   **Role Awareness**: Automatically switches roles (Ground, Tower, Approach) based on your tuned frequency.
*   **🔊 Immersive Audio**:
    *   **Neural TTS**: High-quality Edge-TTS voices with regional accents (Chinese/English).
    *   **Radio Effects**: Realistic VHF static and transmission simulation.
*   **🕹️ Joystick PTT**: Built-in support for mapping your joystick button to Push-to-Talk.

## Installation (Easy Mode)

1.  **Download** the `OpenFrequency_v2.0_Beta.zip` from the Assets below.
2.  **Extract** to a folder.
3.  **Rename** `config.example.json` to `config.json`.
4.  **Edit** `config.json` and paste your **Google API Key** (Get one for free at aistudio.google.com).
5.  **Run** `python app.py` (Ensure Python 3.10+ is installed).

## Notes
This is a **Beta** release. Bugs may exist. Please report issues on our GitHub Issues page.
Legacy code name: "OpenSky-ATC".
