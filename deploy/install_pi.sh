#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/home/pi/boston}"
VOSK_MODEL_URL="${2:-https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip}"
PIPER_MODEL_URL="${3:-https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx}"
PIPER_CONFIG_URL="${4:-https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json}"

echo "[1/10] Ensuring apt dependencies..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg espeak espeak-ng libespeak1 libespeak-ng1 nginx wget unzip curl mpg123 vlc alsa-utils

echo "[2/10] Creating app directory: ${APP_DIR}"
mkdir -p "${APP_DIR}"

echo "[3/10] Creating virtual environment..."
python3 -m venv "${APP_DIR}/.venv"

echo "[4/10] Installing python dependencies..."
source "${APP_DIR}/.venv/bin/activate"
pip install --upgrade pip
pip install -r "${APP_DIR}/requirements.txt"
pip install --upgrade piper-tts || true

echo "[4.1/10] Ensuring Ollama local model service (optional)..."
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh || true
fi
if command -v ollama >/dev/null 2>&1; then
  sudo systemctl enable ollama || true
  sudo systemctl restart ollama || true
  ollama pull llama3.2:3b || true
fi

echo "[5/10] Ensuring Vosk speech model is present..."
mkdir -p "${APP_DIR}/models"
if [ ! -d "${APP_DIR}/models/vosk" ] || [ -z "$(ls -A "${APP_DIR}/models/vosk" 2>/dev/null)" ]; then
  cd "${APP_DIR}/models"
  if ! wget -O vosk.zip "${VOSK_MODEL_URL}"; then
    curl -L -o vosk.zip "${VOSK_MODEL_URL}"
  fi
  unzip -o vosk.zip
  rm -f vosk.zip
  rm -rf vosk
  extracted_dir="$(find . -maxdepth 1 -type d -name 'vosk-model*' | head -n 1)"
  if [ -z "${extracted_dir}" ]; then
    echo "ERROR: no vosk-model directory found after unzip"
    exit 1
  fi
  mv "${extracted_dir}" vosk
else
  echo "Vosk model already present, skipping download."
fi

if [ ! -f "${APP_DIR}/models/vosk/am/final.mdl" ]; then
  echo "ERROR: Vosk model is missing expected file ${APP_DIR}/models/vosk/am/final.mdl"
  exit 1
fi

echo "[5.1/10] Ensuring Piper natural voice model is present (best-effort)..."
mkdir -p "${APP_DIR}/models/piper"
if [ ! -f "${APP_DIR}/models/piper/en_US-lessac-medium.onnx" ]; then
  if ! wget -O "${APP_DIR}/models/piper/en_US-lessac-medium.onnx" "${PIPER_MODEL_URL}"; then
    curl -L -o "${APP_DIR}/models/piper/en_US-lessac-medium.onnx" "${PIPER_MODEL_URL}" || true
  fi
fi
if [ ! -f "${APP_DIR}/models/piper/en_US-lessac-medium.onnx.json" ]; then
  if ! wget -O "${APP_DIR}/models/piper/en_US-lessac-medium.onnx.json" "${PIPER_CONFIG_URL}"; then
    curl -L -o "${APP_DIR}/models/piper/en_US-lessac-medium.onnx.json" "${PIPER_CONFIG_URL}" || true
  fi
fi

echo "[6/10] Installing systemd services..."
sudo cp "${APP_DIR}/systemd/boston-referee.service" /etc/systemd/system/boston-referee.service
sudo cp "${APP_DIR}/systemd/boston-dashboard.service" /etc/systemd/system/boston-dashboard.service
sudo cp "${APP_DIR}/systemd/boston-volume.service" /etc/systemd/system/boston-volume.service

echo "[6.1/10] Configuring passwordless sudo for dashboard actions..."
DASHBOARD_USER="$(awk -F= '/^User=/{print $2}' "${APP_DIR}/systemd/boston-dashboard.service" | head -n 1 | tr -d '\r' | xargs)"
if [ -z "${DASHBOARD_USER}" ]; then
  DASHBOARD_USER="pi"
fi
printf '%s ALL=(root) NOPASSWD: /sbin/reboot, /sbin/shutdown, /bin/systemctl\n' "${DASHBOARD_USER}" | sudo tee /etc/sudoers.d/boston-dashboard >/dev/null
sudo chmod 440 /etc/sudoers.d/boston-dashboard
sudo visudo -cf /etc/sudoers.d/boston-dashboard

echo "[7/10] Configuring NGINX reverse proxy to port 80..."
sudo cp "${APP_DIR}/systemd/boston-nginx.conf" /etc/nginx/sites-available/boston
sudo ln -sf /etc/nginx/sites-available/boston /etc/nginx/sites-enabled/boston
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx

echo "[8/10] Reloading and enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable boston-volume
sudo systemctl enable boston-referee
sudo systemctl enable boston-dashboard

echo "[9/10] Restarting services..."
sudo systemctl start boston-volume
sudo systemctl restart boston-referee
sudo systemctl restart boston-dashboard

echo "[10/10] Verifying services..."
sudo systemctl --no-pager --full status boston-referee | sed -n '1,12p'
sudo systemctl --no-pager --full status boston-dashboard | sed -n '1,12p'

echo "Install complete. Dashboard available at http://<pi-ip>/"
