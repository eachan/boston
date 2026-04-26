# Boston — AI Karate Kumite Referee

Boston is a full-stack Raspberry Pi product for AI-assisted karate kumite refereeing with:

- **Voice interaction** with wake-word style command flow (`Hey Boston ...`)
- **Live match management** (start/stop/restart, timer handling, countdown, score calls)
- **Vision-based fighter visibility and scoring candidate detection** (white vs blue gi)
- **Rule-based kumite scoring decisions** (1/2/3 point mapping and decision criteria)
- **Onboard dashboard** (status, history timeline, settings/actions)
- **Windows deployment manager** for SSH/SCP deployment and one-click operations

---

## 1) Project Structure

- `boston/` — core application modules
  - `referee.py` — runtime orchestrator
  - `voice.py` — microphone + Vosk speech recognition
  - `vision.py` — camera analysis + scoring candidates
  - `rules.py` — kumite scoring rules engine
  - `dashboard.py` — FastAPI UI and API
  - `storage.py` — SQLite persistence (matches/events/settings/runtime)
  - `system_control.py` — service/system/wifi operations
- `templates/` + `static/` — dashboard UI pages and styling
- `config/boston.yaml` — deployment/runtime configuration
- `systemd/` — service units + nginx site config
- `deploy/`
  - `install_pi.sh` — Pi-side installer/provisioner
  - `windows_manager.py` — Windows GUI deployment/support tool

---

## 2) Hardware + Runtime Expectations

Target platform:

- Raspberry Pi 5 (64-bit Raspberry Pi OS)
- ReSpeaker Lite + USB audio output
- Sony IMX500 camera
- Hailo-8 accelerator

Boston monitors Hailo status via configured command (`hailortcli fw-control identify`) and exposes it on the dashboard.

---

## 3) Quick Start on Raspberry Pi

1. Copy project to Pi, e.g. `/home/pi/boston`
2. Run installer:

```bash
cd /home/pi/boston
chmod +x deploy/install_pi.sh
bash deploy/install_pi.sh /home/pi/boston
```

By default, installer also ensures these runtime assets:

- eSpeak/eSpeak-ng TTS system libraries
- nginx
- offline Vosk model at `models/vosk`
- optional Ollama runtime + `llama3.2:3b` pull (best-effort)
- media player dependencies for easter egg playback (`mpg123`, `vlc`)
- boot-time volume initialization service (sets output to 100% after reboot)

Optional: provide a custom Vosk ZIP URL as second arg:

```bash
bash deploy/install_pi.sh /home/pi/boston "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
```

3. Confirm services:

```bash
systemctl status boston-referee
systemctl status boston-dashboard
```

4. Open dashboard in browser:

```text
http://<pi-ip>/
```

Dashboard is served through **port 80** via nginx reverse proxy.

---

## 4) Run Manually (Development)

Create virtual env and install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run referee engine:

```bash
python -m boston.run_referee
```

Run dashboard:

```bash
python -m boston.run_dashboard
```

---

## 5) Windows Deployment Manager

Run:

```bash
python deploy/windows_manager.py
```

Features:

- Save SSH credentials between sessions
- Save local project directory between sessions for one-click release reuse
- Connect/disconnect SSH
- Upload/download files via SCP
- One-click deploy (upload + dependency and service commands)
- Auto-provision Vosk model (`models/vosk`) if missing
- Best-effort Ollama install/start and default model pull (`llama3.2:3b`)
- Start/stop/restart service buttons
- Add additional managed app/service buttons
- One-click reboot/shutdown

---

## 6) Voice Command Behaviors Implemented

- Status enquiries ("are you there", "are you ready", "how are you", etc.)
- IP address query (e.g. "Hey Boston, what's your IP address?")
- Camera visibility checks ("can you see us/fighters")
- Self-test command (e.g. "Hey Boston, can you do a self test")
- New match flow (e.g. "Hey Boston, start new match") with interactive timer prompt
- Match start flow via "ready" (requires both fighters visible)
- Default 2-minute timed matches unless changed
- Timer controls (e.g., "set timer 3 minutes", "no timer")
- Stop/restart match commands
- General conversation fallback through local model endpoint

---

## 7) Dashboard Pages

- `/` Dashboard
  - service states, runtime status, visibility, Hailo/audio/system stats, recent activity
- `/history`
  - match history with event timeline, points and winner
- `/settings`
  - add Wi-Fi network
  - restart/start/stop managed services
  - reboot/shutdown Pi

---

## 8) Data Persistence

SQLite database stores:

- `matches` — match boundaries, scores, winner
- `events` — transcripts, intents, points, service/settings actions
- `settings` — runtime settings and runtime status snapshot

Default DB path: `data/boston.db`

---

## 9) Notes

- Place a compatible offline Vosk model at `models/vosk` (or update config path).
- Configure local LLM endpoint/model in `config/boston.yaml`.
- Camera scoring logic is heuristic and rule-gated; model-based vision can be integrated by replacing candidate generation in `vision.py`.

## 10) Voice Tuning (less robotic TTS)

You can tune Boston voice in `config/boston.yaml` under `tts`:

- `rate`: lower values usually sound more natural (default now `155`)
- `preferred_voice_tokens`: tokens used to choose the best available voice
- `fallback_voice_id`: explicit fallback voice id (default `english-us+f3`)

Example:

```yaml
tts:
  enabled: true
  rate: 150
  volume: 1.0
  preferred_voice_tokens: ["female", "en-us"]
  fallback_voice_id: "english-us+f3"
```

After changes:

```bash
sudo systemctl restart boston-referee
```

For the most natural local voice, Boston now supports **Piper TTS** backend (with pyttsx3 fallback):

```yaml
tts:
  enabled: true
  backend: "auto"   # auto | piper | pyttsx3
  piper_command: ".venv/bin/piper"
  piper_model_path: "models/piper/en_US-lessac-medium.onnx"
  piper_model_config_path: "models/piper/en_US-lessac-medium.onnx.json"
```

Status query now reports active speaker backend, e.g. `using piper`.

## 10.1) Speech Recognition Quality (better understanding)

Boston now includes improved ASR defaults for command accuracy:

- stronger default Vosk model download: `vosk-model-en-us-0.22-lgraph`
- smaller audio blocks (`4000`) for quicker recognition updates
- input gain boost (`audio.input_gain`) to better catch quiet speech
- optional command grammar biasing (`audio.use_command_grammar`) for referee phrases

Relevant config keys in `config/boston.yaml`:

```yaml
audio:
  vosk_model_path: models/vosk
  blocksize: 4000
  input_gain: 1.6
  use_command_grammar: true
  partial_min_words: 2
  command_phrases:
    - "hey boston"
    - "status"
    - "camera check"
    - "ready"
    - "stop the match"
    - "self test"
```

If grammar feels too restrictive for free-form chat, set:

```yaml
audio:
  use_command_grammar: false
```

## 11) Easter Egg: Special Move

Boston now supports an easter egg command:

- “Hey Boston, give me a special move”
- “Hey Boston, special move”
- “Hey Boston, hadouken”

Behavior:

- Plays `hadouken.mp3` (path configured by `audio.special_move_sound_path`, default project root `hadouken.mp3`).
- If no player succeeds, Boston falls back to speaking “Hadouken.”

## 12) Troubleshooting: No Referee Voice During Match

If Boston is not speaking point calls or final winner announcements:

1. Ask: **"Hey Boston, status"** and confirm speaker is reported as ready.
2. Run: **"Hey Boston, can you do a self test"** for a spoken diagnostic summary.
3. Check service logs:

```bash
sudo journalctl -u boston-referee -n 200 --no-pager
```

Boston now uses runtime fallback for TTS:

- preferred: `piper`
- fallback: `pyttsx3`
- final fallback: `espeak-ng` / `espeak`

So even if one backend fails during a match, referee calls should still be spoken.

### Settings Page Reboot/Shutdown Permissions

The settings page runs through the `boston-dashboard` service user and now uses non-interactive sudo (`sudo -n`).

Installer/deploy now provisions `/etc/sudoers.d/boston-dashboard` with passwordless permissions for:

- `/sbin/reboot`
- `/sbin/shutdown`
- `/bin/systemctl`

If reboot/shutdown/service buttons still report sudo password errors, rerun deploy/install so this sudoers file is refreshed.

## 13) New Match Timer Dialogue

Boston now supports a match-start dialogue flow:

1. User says: **"Hey Boston, start new match"**
2. Boston asks: **"How long should the timer be for this match?"**
3. User replies with duration (examples):
   - "1 minute"
   - "2 minutes"
   - "1 and a half minutes"
4. Boston confirms timer, starts kumite/refereeing, and ends the match automatically when time expires with normal final score/winner announcement.

## 14) IP Address Voice Response + Pronunciation

- Saying **"Hey Boston, what's your IP address?"** makes Boston read the Pi IP address digit-by-digit (with "dot" between octets).
- TTS pronunciation normalization now speaks **"kumite"** as **"koo-mee-teh"**.
