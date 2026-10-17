#!/bin/bash
#
# BL-RMO build — Étape 60 : services (systemd + Nginx + firstboot)
#
# Installe la structure des services SANS démarrer l'application (pas encore de
# config ni de secrets — ceux-ci sont générés au premier boot par firstboot.sh).
#
# Ce qui est fait ici (générique, dans l'image) :
#   - service systemd bl-rmo (activé, PAS démarré)
#   - service systemd bl-rmo-firstboot (oneshot, activé pour le 1er boot)
#   - Nginx : config avec placeholder __MDNS_HOSTNAME__ (rempli au firstboot)
#   - dossier /etc/nginx/ssl (certificat généré au firstboot)
#   - installe firstboot.sh dans /usr/local/sbin
#
# Idempotent.
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}[60-services]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1" >&2; }

BLFMO_USER="${BLFMO_USER:-${SUDO_USER:-$(id -un 1000 2>/dev/null || echo pi)}}"
BLFMO_HOME="$(getent passwd "$BLFMO_USER" | cut -d: -f6)"
INSTALL_DIR="${INSTALL_DIR:-$BLFMO_HOME/bl-rmo}"
# Dossier de firstboot.sh à installer : passé par build.sh, sinon à côté d'ici.
SRC_DIR="${SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# ─── 0. Droits réseau : sudo nmcli sans mot de passe (WiFi + IP fixe/DHCP) ──
log "Règle sudoers nmcli pour $BLFMO_USER…"
echo "$BLFMO_USER ALL=(root) NOPASSWD: /usr/bin/nmcli" > /etc/sudoers.d/bl-rmo-nmcli
chmod 440 /etc/sudoers.d/bl-rmo-nmcli
visudo -cf /etc/sudoers.d/bl-rmo-nmcli >/dev/null && ok "sudoers nmcli" || { err "sudoers invalide"; rm -f /etc/sudoers.d/bl-rmo-nmcli; exit 1; }

# ─── 1. Service systemd de l'application (activé, pas démarré) ─────────────
log "Installation du service bl-rmo…"
tee /etc/systemd/system/bl-rmo.service > /dev/null <<EOF
[Unit]
Description=BL-RMO · Surveillance radio
After=network-online.target icecast2.service
Wants=network-online.target
Requires=icecast2.service

[Service]
Type=simple
User=$BLFMO_USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$INSTALL_DIR/venv/bin"
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
ok "Service bl-rmo installé."

# ─── 2. Nginx : config avec placeholder de hostname ───────────────────────
log "Configuration Nginx (template)…"
mkdir -p /etc/nginx/ssl
tee /etc/nginx/sites-available/bl-rmo > /dev/null <<'NGINX'
server {
    listen 80;
    server_name __MDNS_HOSTNAME__.local;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name __MDNS_HOSTNAME__.local;

    ssl_certificate     /etc/nginx/ssl/bl-rmo.crt;
    ssl_certificate_key /etc/nginx/ssl/bl-rmo.key;

    location / {
        proxy_pass https://127.0.0.1:5000;
        proxy_ssl_verify off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;

        # SSE (stats temps réel) : pas de buffering, connexion longue
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/bl-rmo /etc/nginx/sites-enabled/
# Retirer le site par défaut pour éviter les conflits de server_name
rm -f /etc/nginx/sites-enabled/default
ok "Nginx configuré (hostname à remplir au firstboot)."

# ─── 3. Installer firstboot.sh ────────────────────────────────────────────
if [ -f "$SRC_DIR/firstboot.sh" ]; then
    install -m 0755 "$SRC_DIR/firstboot.sh" /usr/local/sbin/blfmo-firstboot.sh
    ok "firstboot.sh installé dans /usr/local/sbin/blfmo-firstboot.sh"
else
    warn "firstboot.sh introuvable dans $SRC_DIR — à déposer avant le 1er boot."
fi

# ─── 4. Service oneshot de premier boot ───────────────────────────────────
log "Installation du service firstboot (oneshot)…"
tee /etc/systemd/system/bl-rmo-firstboot.service > /dev/null <<'EOF'
[Unit]
Description=BL-RMO — Provisioning au premier démarrage
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/var/lib/blfmo/.provisioned

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/blfmo-firstboot.sh
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
EOF

# ─── 5. Activer (pas démarrer) ────────────────────────────────────────────
log "Activation des services (démarrage différé au 1er boot)…"
systemctl daemon-reload
systemctl enable bl-rmo-firstboot.service >/dev/null 2>&1
systemctl enable bl-rmo.service >/dev/null 2>&1
systemctl enable nginx >/dev/null 2>&1
systemctl enable avahi-daemon >/dev/null 2>&1
systemctl enable icecast2 >/dev/null 2>&1
# NB : on NE démarre PAS bl-rmo ni nginx maintenant (config incomplète).
ok "Services activés (bl-rmo démarrera après le firstboot)."

echo ""
ok "Étape 60 OK — services installés et activés."
log "Au prochain boot : firstboot génère secrets + config, puis démarre l'app."
