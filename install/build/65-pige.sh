#!/bin/bash
#
# BL-FMO build - Étape 65 : pige audio 24 h
#
# Installe le service systemd fm-monitor-pige (enregistreur autonome qui lit
# le flux Icecast local et le découpe en segments MP3 de 5 min).
# Indépendant de fm-monitor : la pige continue pendant un redémarrage de l'appli.
#
# Volume : 128 kbps ≈ 58 Mo/heure ≈ 1,4 Go pour 24 h.
#
# Idempotent. À lancer avec sudo.
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
log() { echo -e "${BLUE}[65-pige]${NC} $1"; }
ok()  { echo -e "${GREEN}[ok]${NC} $1"; }

BLFMO_USER="${BLFMO_USER:-${SUDO_USER:-$(id -un 1000 2>/dev/null || echo pi)}}"
BLFMO_HOME="$(getent passwd "$BLFMO_USER" | cut -d: -f6)"
INSTALL_DIR="${INSTALL_DIR:-$BLFMO_HOME/fm-monitor}"

log "Dossier de pige…"
install -d -o "$BLFMO_USER" -g "$BLFMO_USER" "$INSTALL_DIR/pige"
ok "$INSTALL_DIR/pige"

log "Service fm-monitor-pige…"
tee /etc/systemd/system/fm-monitor-pige.service > /dev/null <<EOF
[Unit]
Description=BL-FMO - Pige audio 24 h
After=network-online.target icecast2.service
Wants=icecast2.service

[Service]
Type=simple
User=$BLFMO_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/pige.py
Restart=on-failure
RestartSec=5
Nice=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable fm-monitor-pige.service >/dev/null
# Démarrer seulement si l'appli est déjà configurée (pas pendant la construction d'image)
if [ -f "$INSTALL_DIR/config.json" ]; then
    systemctl restart fm-monitor-pige.service
    ok "Service fm-monitor-pige activé et démarré"
else
    ok "Service fm-monitor-pige activé (démarrera au prochain boot)"
fi
