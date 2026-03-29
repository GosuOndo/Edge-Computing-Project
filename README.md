# Smart Medication Verification System

An edge-computing IoT system that automates real-time medication intake verification for patients. The system uses weight sensing, NFC/RFID identity verification, patient behaviour monitoring, and scheduled reminders — all processed locally on a Raspberry Pi with no cloud dependency for core operations.

---

## Table of Contents

- [System Overview](#system-overview)
- [Hardware Requirements](#hardware-requirements)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [NFC Tag Payload Format](#nfc-tag-payload-format)
- [Setup & Configuration](#setup--configuration)
- [Running the System](#running-the-system)
- [Firmware](#firmware)
- [Modules Reference](#modules-reference)
- [Workflow](#workflow)

---

## System Overview

The system supports up to two medication stations. Each station consists of:

- A **weight scale unit** (M5StickC Plus with HX711 load cell) that detects pill removal
- A **tag reader unit** (M5StickC Plus with MFRC522 module) mounted under the scale to identify the medicine bottle via NFC/RFID
- A central **Raspberry Pi** that orchestrates all sensors, runs the scheduler, evaluates compliance, and sends Telegram alerts

Key features:
- **Onboarding registration** — scan each medicine bottle once; the system learns the medicine name, dosage, schedule, and pill weight from the NFC tag
- **Automatic reminders** — audio + display alerts at scheduled medication times
- **Multi-factor verification** — NFC tag identity + weight delta + optional patient behaviour monitoring (MediaPipe) + optional OCR fallback
- **Telegram notifications** — real-time alerts to patient and caregiver on success, missed dose, or incorrect dosage
- **Fully offline** — all decisions made locally on the Pi; Telegram messages are queued when offline and sent when connectivity is restored
- **PASO profiling** — built-in edge-device performance instrumentation (latency, CPU, memory, temperature)

---

## Hardware Requirements

| Component | Quantity | Details |
|-----------|----------|---------|
| Raspberry Pi 4 (or 5) | 1 | Main compute unit |
| M5StickC Plus (scale unit) | 2 | One per station; drives HX711 load cell for weight sensing; communicates over MQTT via Wi-Fi |
| M5StickC Plus (tag reader unit) | 2 | One per station; drives MFRC522 RFID module for NFC identity verification; communicates over MQTT via Wi-Fi |
| MFRC522 RFID module | 2 | Wired to the tag reader M5StickC Plus units via SPI; mounted under each station's scale |
| NTAG213 NFC sticker tags | 1 per bottle | Affixed to the bottom of each medicine bottle |
| Display (HDMI) | 1 | 1024 × 600 touchscreen or monitor for the pygame UI |
| USB camera | 1 | For patient monitoring (MediaPipe) and optional OCR |
| Speaker / audio output | 1 | For espeak TTS announcements |
| MQTT broker (Mosquitto) | — | Runs locally on the Pi (`localhost:1883`) |

> **Per-station summary:** each station uses **2 × M5StickC Plus** units — one dedicated to weight sensing (HX711) and one dedicated to tag reading (MFRC522). Both communicate with the Pi over MQTT via Wi-Fi, so no wired sensor connections are required at the Pi.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                           Raspberry Pi                              │
│                                                                     │
│  main.py  ──► StateMachine ──► DecisionEngine                       │
│      │                                                              │
│      ├── WeightManager  ◄── MQTT ◄── M5StickC+ scale  (station 1)  │
│      │                  ◄── MQTT ◄── M5StickC+ scale  (station 2)  │
│      │                                                              │
│      ├── TagRuntimeService ◄── MQTT ◄── M5StickC+ reader (station 1)│
│      │                    ◄── MQTT ◄── M5StickC+ reader (station 2) │
│      │       └── TagManager (payload parser)                        │
│      │                                                              │
│      ├── IdentityManager (tag → QR → OCR fallback chain)            │
│      ├── RegistrationManager (onboarding flow)                      │
│      ├── MedicationScheduler (reminder scheduling)                  │
│      ├── PatientMonitor (MediaPipe swallow detection)               │
│      ├── Database (SQLite)                                          │
│      ├── DisplayManager (pygame UI)                                 │
│      ├── AudioManager (espeak TTS)                                  │
│      └── TelegramBot (caregiver/patient alerts)                     │
└─────────────────────────────────────────────────────────────────────┘

Per station (× 2):
  ┌─────────────────────────┐   ┌──────────────────────────────┐
  │  M5StickC Plus          │   │  M5StickC Plus               │
  │  (scale unit)           │   │  (tag reader unit)           │
  │  HX711 load cell        │   │  MFRC522 RFID module (SPI)   │
  │  MQTT → weight topic    │   │  MQTT → tag read/cmd topics  │
  └─────────────────────────┘   └──────────────────────────────┘
```

All four M5StickC Plus units communicate with the Pi exclusively over **MQTT** via the local Wi-Fi network. There are no wired sensor connections at the Pi.

---

## Project Structure

```
Edge-Computing-Project/
├── config/
│   └── config.example.yaml         # Copy to config/config.yaml and fill in values
│
├── firmware/
│   ├── station_1/
│   │   └── medication_scale_station/
│   │       └── medication_scale_station.ino   # M5StickC+ scale firmware (station 1)
│   ├── station_2/
│   │   └── medication_scale_station/
│   │       └── medication_scale_station.ino   # M5StickC+ scale firmware (station 2)
│   └── tag_firmware/
│       ├── medication_tag_reader_node_station1/
│       │   └── medication_tag_reader_node_station1.ino  # M5StickC+ tag reader (station 1)
│       ├── medication_tag_reader_node_station2/
│       │   └── medication_tag_reader_node_station2.ino  # M5StickC+ tag reader (station 2)
│       ├── medication_tag_writer_node/
│       │   └── medication_tag_writer_node.ino           # Tag write/verify utility
│       └── rc522_test_read_uid/
│           └── rc522_test_read_uid.ino                  # UID read test utility
│
└── raspberry_pi/
    ├── main.py                     # Top-level application entry point
    ├── modules/
    │   ├── audio_manager.py        # espeak TTS speech queue
    │   ├── database.py             # SQLite events and medicine registry
    │   ├── decision_engine.py      # Rule-based compliance decision logic
    │   ├── display_manager.py      # pygame UI screens
    │   ├── identity_manager.py     # NFC → QR → OCR identity fallback chain
    │   ├── medicine_scanner.py     # Tesseract OCR label scanning
    │   ├── patient_monitor.py      # MediaPipe swallow/hand-motion detection
    │   ├── qr_scanner.py           # QR code decoder
    │   ├── registration_manager.py # One-time medicine onboarding flow
    │   ├── tag_manager.py          # NFC tag payload parser and verifier
    │   ├── tag_runtime_service.py  # Live MQTT tag scan listener
    │   ├── telegram_bot.py         # Telegram notification client
    │   └── weight_manager.py       # HX711 weight event detection
    ├── services/
    │   ├── mqtt_client.py          # MQTT connection and callbacks
    │   ├── scheduler.py            # Medication reminder scheduler
    │   └── state_machine.py        # System state management
    └── utils/
        ├── config_loader.py        # YAML config with env-var overrides
        ├── logger.py               # Coloured console + rotating file logger
        └── profiler.py             # PASO edge-device performance profiler
```

---

## NFC Tag Payload Format

Each medicine bottle has an NTAG213 NFC sticker on its base. The tag stores a compact semicolon-delimited string written using `medication_tag_writer_node.ino`.

**Format:**
```
ID=<medicine_id>;N=<medicine_name>;D=<dosage>;T=<time_slots>;M=<meal_rule>;W=<pill_weight_mg>
```

**Example:**
```
ID=M001;N=ASPIRIN;D=2;T=08,20;M=AF;W=290
```

| Field | Key | Description | Example |
|-------|-----|-------------|---------|
| Medicine ID | `ID` | Unique medicine identifier (required) | `M001` |
| Medicine Name | `N` | Human-readable medicine name | `ASPIRIN` |
| Dosage | `D` | Number of pills per dose | `2` |
| Time Slots | `T` | Comma-separated hours (Pi expands to `HH:MM`) | `08,20` → `08:00,20:00` |
| Meal Rule | `M` | `AF`=after meal, `BF`=before meal, `NM`=no rule | `AF` |
| Pill Weight | `W` | Per-pill weight in milligrams | `290` |

**Maximum payload size:** 56 bytes (pages 4–17 of NTAG213).

> **Note:** `patient_id` and `station_id` are intentionally **not** stored on the tag.
> Patient association is resolved from the database using `medicine_id`.
> Station assignment is always determined by the physical station during onboarding — never from the tag.

---

## Setup & Configuration

### 1. Clone the repository

```bash
git clone https://github.com/your-org/Edge-Computing-Project.git
cd Edge-Computing-Project
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

Key dependencies: `paho-mqtt`, `pygame`, `mediapipe`, `pytesseract`, `python-telegram-bot`, `schedule`, `RPi.GPIO`

### 3. Install system packages (Raspberry Pi)

```bash
sudo apt-get install espeak aplay mosquitto mosquitto-clients tesseract-ocr
sudo systemctl enable mosquitto
sudo systemctl start mosquitto
```

### 4. Configure the system

```bash
cp config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml` and fill in:

- `telegram.bot_token` — from [@BotFather](https://t.me/BotFather)
- `telegram.patient_chat_id` and `telegram.caregiver_chat_id`
- `mqtt.broker_host` — `localhost` if Mosquitto runs on the Pi
- `hardware.audio.output_device` — ALSA device string for your speaker
- `weight_sensors.station_1.pill_weight_mg` and `station_2.pill_weight_mg` — fallback values only; overridden by `W=` field on the NFC tag at runtime

### 5. Flash firmware

There are **4 M5StickC Plus units** to flash in total — 2 scale units and 2 tag reader units:

| Unit | Firmware | Notes |
|------|----------|-------|
| Scale unit — station 1 | `firmware/station_1/medication_scale_station.ino` | Drives HX711 load cell |
| Scale unit — station 2 | `firmware/station_2/medication_scale_station.ino` | Drives HX711 load cell |
| Tag reader unit — station 1 | `firmware/tag_firmware/medication_tag_reader_node_station1/medication_tag_reader_node_station1.ino` | Drives MFRC522 via SPI |
| Tag reader unit — station 2 | `firmware/tag_firmware/medication_tag_reader_node_station2/medication_tag_reader_node_station2.ino` | Drives MFRC522 via SPI |

> Update the Wi-Fi SSID, password, and MQTT broker IP in **each** firmware file before flashing.

### 6. Write NFC tags

Use `firmware/tag_firmware/medication_tag_writer_node/medication_tag_writer_node.ino` to write the medicine payload to each NTAG213 sticker. Edit `TEST_PAYLOAD` in the sketch with the correct values for each medicine, then tap the sticker to the reader to write and verify.

---

## Running the System

```bash
cd raspberry_pi
python main.py
```

On first run, the system enters **onboarding mode** and prompts you to place each medicine bottle on its station. It reads the NFC tag, captures the baseline weight, and registers the medicine into the database. Once all medicines are registered, the system switches to normal operation.

---

## Firmware

### Scale Station (`medication_scale_station.ino`)

- Runs on **M5StickC Plus** (scale unit) with an HX711 load cell
- Publishes weight data to MQTT topic: `medication/weight/<station_id>`
- Receives dosing commands via MQTT and confirms pill-count verification
- Supports persistent EEPROM calibration

### Tag Reader Nodes (`medication_tag_reader_node_station*.ino`)

- Runs on **M5StickC Plus** (tag reader unit) wired to an **MFRC522** RFID module via SPI
- Subscribes to `medication/tag/command/tag_reader_<n>` for `start_scan` / `stop_scan` commands
- Publishes scan results (UID + raw payload) to `medication/tag/read/tag_reader_<n>`
- Implements retry logic (4 attempts, 120 ms delay) for reliable reads
- Communicates with the Pi exclusively over MQTT via Wi-Fi — no wired connection to the Pi required

### Tag Writer Utility (`medication_tag_writer_node.ino`)

- Writes and immediately reads back a medicine payload onto an NTAG213 sticker
- Used during initial tag preparation only — not part of the running system

---

## Modules Reference

| Module | Responsibility |
|--------|---------------|
| `main.py` | Orchestrates all modules; owns the main event loop and state transitions |
| `state_machine.py` | Tracks system state: `IDLE → REMINDER_ACTIVE → MONITORING → ...` |
| `weight_manager.py` | Detects bottle lift/replace events from HX711 weight streams |
| `tag_runtime_service.py` | Buffers live NFC scan messages; provides passive (coincident) and active (blocking) query modes |
| `tag_manager.py` | Parses NFC tag payloads; verifies scanned records against expected medicine/station context |
| `identity_manager.py` | Runs the identity verification chain: NFC tag → QR code → OCR |
| `registration_manager.py` | Runs the one-time onboarding flow: weight + tag scan → database record |
| `decision_engine.py` | Combines identity, weight, behaviour, and OCR results into a `DecisionResult` |
| `patient_monitor.py` | MediaPipe-based swallow and hand-motion detection via USB camera |
| `scheduler.py` | Fires medication reminders at scheduled times using the `schedule` library |
| `database.py` | SQLite persistence for registered medicines, medication events, and compliance history |
| `display_manager.py` | pygame-based UI with screens for idle, reminder, monitoring, success, error, and registration |
| `audio_manager.py` | Serial espeak TTS queue; async and blocking speak modes |
| `telegram_bot.py` | Sends alerts to patient and caregiver; queues messages offline and retries |
| `config_loader.py` | Loads `config.yaml` with dot-notation access and environment variable overrides |
| `logger.py` | Coloured console logger + rotating file handler |
| `profiler.py` | PASO CSV profiler for edge-device latency and resource measurements |

---

## Workflow

```
System start
    │
    ▼
Onboarding (if medicines not registered)
    Place bottle → stable weight detected → NFC tag read → saved to DB
    │
    ▼
Normal Operation
    │
    ├── Scheduler fires reminder
    │       Display + audio alert
    │       │
    │       ▼
    │   Bottle lifted (weight delta detected by scale M5StickC+)
    │       │
    │       ├── NFC tag verified (coincident scan by tag reader M5StickC+)
    │       ├── Weight delta → pill count estimated
    │       └── Patient behaviour monitored (MediaPipe)
    │               │
    │               ▼
    │           DecisionEngine evaluates all inputs
    │               │
    │               ├── SUCCESS   → log event, notify Telegram
    │               ├── INCORRECT_DOSAGE → alert caregiver
    │               ├── WRONG_MEDICINE   → alert caregiver
    │               └── NO_INTAKE        → alert caregiver
    │
    └── Missed reminder timeout → alert caregiver
```
