#!/usr/bin/env bash
# One-shot bootstrap for an Oracle Cloud Always-Free VM in Mumbai (ap-mumbai-1).
# Run this on the VM after SSH'ing in. It is idempotent — safe to re-run.
#
# What it does:
#   1. Installs Python 3.11, git, and build deps
#   2. Generates a GitHub deploy key (if missing) and prints it for you to paste
#   3. Clones the repo via SSH (waits until you've added the deploy key)
#   4. Sets up a Python venv and installs requirements
#   5. Verifies the scanner can fetch NSE data from this VM's Indian IP
#   6. Installs a systemd timer to run weekdays at 8:30 AM IST and push results
#
# Idempotent: re-run any time to refresh the timer or re-test data fetch.

set -euo pipefail

REPO_URL="git@github.com:Rajkota03/investing-VCP.git"
REPO_DIR="$HOME/investing-VCP"
KEY_PATH="$HOME/.ssh/github_deploy"
TIMER_NAME="vcp-scan"

echo "▸ Updating apt and installing system deps..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3 python3-venv python3-pip git curl ca-certificates build-essential

echo "▸ Confirming this is an Indian IP..."
COUNTRY=$(curl -s https://ipapi.co/country_name/ || echo "unknown")
PUBLIC_IP=$(curl -s https://ipapi.co/ip/ || echo "?")
echo "    public IP: $PUBLIC_IP   country: $COUNTRY"
if [[ "$COUNTRY" != "India" ]]; then
  echo "    ⚠ WARNING: VM is not in India. NSE will geo-block this. Recreate VM in Mumbai region."
  exit 1
fi

# Generate a GitHub deploy key if not present
if [ ! -f "$KEY_PATH" ]; then
  echo "▸ Generating SSH deploy key..."
  mkdir -p "$HOME/.ssh"
  ssh-keygen -t ed25519 -N "" -C "vcp-mumbai-vm" -f "$KEY_PATH" >/dev/null
fi

# Configure SSH to use this key for github.com
SSH_CFG="$HOME/.ssh/config"
mkdir -p "$HOME/.ssh"
touch "$SSH_CFG"
chmod 600 "$SSH_CFG"
if ! grep -q "Host github.com" "$SSH_CFG"; then
  cat >> "$SSH_CFG" <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile $KEY_PATH
  StrictHostKeyChecking accept-new
EOF
fi

# Show the public key so the user can register it
echo
echo "──────────────────────────────────────────────────────────────"
echo "  ACTION REQUIRED"
echo "──────────────────────────────────────────────────────────────"
echo "  Add this deploy key to GitHub:"
echo "    https://github.com/Rajkota03/investing-VCP/settings/keys/new"
echo "  Title: oracle-mumbai-vm"
echo "  ✅ Check 'Allow write access'"
echo
cat "$KEY_PATH.pub"
echo "──────────────────────────────────────────────────────────────"
echo
read -p "Press ENTER once the deploy key is added on GitHub..." _

# Clone or pull
if [ ! -d "$REPO_DIR" ]; then
  echo "▸ Cloning repo..."
  git clone "$REPO_URL" "$REPO_DIR"
else
  echo "▸ Repo exists — pulling latest..."
  git -C "$REPO_DIR" pull --rebase
fi

# Set up venv + deps
cd "$REPO_DIR"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Test run
echo "▸ Test-running the scanner from this VM..."
if python3 run.py; then
  SETUPS=$(python3 -c "import json; print(json.load(open('output/meta.json'))['setups_total'])")
  echo "    ✅ scan returned $SETUPS setups"
else
  echo "    ❌ scan failed — check output above"
  exit 1
fi

# Configure git identity for the bot commits
git config user.name "VCP Scanner Bot (Mumbai)"
git config user.email "bot@vcp.mumbai.local"

# Push the test scan
git add output/
if ! git diff --cached --quiet; then
  git commit -m "scan: $(date '+%d %b %Y %H:%M') IST (Oracle Mumbai bootstrap)"
  git push
  echo "    ✅ pushed to GitHub — Vercel will redeploy in ~30s"
fi

# Install systemd service + timer
echo "▸ Installing systemd timer (weekdays 8:30 AM IST)..."

SERVICE_FILE="/etc/systemd/system/${TIMER_NAME}.service"
TIMER_FILE="/etc/systemd/system/${TIMER_NAME}.timer"
RUN_SCRIPT="$HOME/run-vcp-scan.sh"

# Wrapper script that runs the scan and pushes
cat > "$RUN_SCRIPT" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/investing-VCP"
git pull --rebase --autostash
source .venv/bin/activate
if python3 run.py; then
  git add output/
  if ! git diff --cached --quiet; then
    git commit -m "scan: $(date '+%d %b %Y %H:%M') IST"
    git push
  fi
else
  echo "scan failed — not pushing" >&2
  exit 1
fi
WRAPPER
chmod +x "$RUN_SCRIPT"

sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=VCP Scanner — daily NSE scan + push to GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$HOME/investing-VCP
ExecStart=$RUN_SCRIPT
StandardOutput=journal
StandardError=journal
EOF

sudo tee "$TIMER_FILE" >/dev/null <<'EOF'
[Unit]
Description=Trigger VCP Scanner weekdays at 8:30 AM IST

[Timer]
# 8:30 AM IST = 03:00 UTC, Mon-Fri
OnCalendar=Mon..Fri 03:00 UTC
# If the VM was off at the scheduled time, run as soon as it boots
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${TIMER_NAME}.timer"

echo
echo "──────────────────────────────────────────────────────────────"
echo "  ✅ DONE"
echo "──────────────────────────────────────────────────────────────"
echo "  Next run:   $(systemctl list-timers ${TIMER_NAME}.timer --no-pager | awk 'NR==2{print $1, $2, $3, $4}')"
echo "  Logs:       sudo journalctl -u ${TIMER_NAME}.service -n 50"
echo "  Manual run: $RUN_SCRIPT"
echo "──────────────────────────────────────────────────────────────"
