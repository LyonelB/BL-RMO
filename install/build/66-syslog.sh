#!/bin/bash
#
# BL-RMO build - Étape 66 : réception des journaux des appareils (syslog)
#
# Installe le service systemd fm-monitor-syslog : il écoute en UDP 514 et range
# les journaux envoyés par les switchs, faisceaux, codecs, émetteur... dans
# syslog.db (page « Journaux »). Indépendant de fm-monitor : la réception
# continue pendant un redémarrage de l'application.
#
# Le port 514 est réservé à root : le service tourne sous l'utilisateur BL-RMO
# avec la seule capacité CAP_NET_BIND_SERVICE (ouvrir un port < 1024).
#
# Idempotent. À lancer avec sudo.
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${BLUE}[66-syslog]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

BLFMO_USER="${BLFMO_USER:-${SUDO_USER:-$(id -un 1000 2>/dev/null || echo pi)}}"
BLFMO_HOME="$(getent passwd "$BLFMO_USER" | cut -d: -f6)"
INSTALL_DIR="${INSTALL_DIR:-$BLFMO_HOME/fm-monitor}"

if [ ! -f "$INSTALL_DIR/syslog_server.py" ]; then
    echo "syslog_server.py absent de $INSTALL_DIR" >&2
    exit 1
fi

# Un autre serveur syslog (rsyslog...) qui écouterait déjà en UDP 514 bloquerait le service
if command -v ss >/dev/null && ss -lun | awk '{print $4}' | grep -qE '(^|:)514$'; then
    if ! systemctl is-active --quiet fm-monitor-syslog.service; then
        warn "Le port UDP 514 est déjà utilisé :"
        ss -lunp | grep ':514 ' || true
        warn "Libérez-le (ex. rsyslog configuré en serveur) avant de continuer."
        exit 1
    fi
fi

log "Service fm-monitor-syslog…"
tee /etc/systemd/system/fm-monitor-syslog.service > /dev/null <<EOF
[Unit]
Description=BL-RMO - Réception des journaux des appareils (syslog)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$BLFMO_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/syslog_server.py
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable fm-monitor-syslog.service >/dev/null
# Démarrer seulement si l'appli est déjà configurée (pas pendant la construction d'image)
if [ -f "$INSTALL_DIR/config.json" ]; then
    systemctl restart fm-monitor-syslog.service
    sleep 2
    if systemctl is-active --quiet fm-monitor-syslog.service; then
        ok "Service fm-monitor-syslog démarré (UDP 514)"
    else
        warn "Le service n'a pas démarré : sudo journalctl -u fm-monitor-syslog -n 20 --no-pager"
        exit 1
    fi
else
    ok "Service fm-monitor-syslog activé (démarrera au prochain boot)"
fi
