#!/bin/bash
#
# BL-RMO — Provisioning au PREMIER BOOT (exécuté une seule fois)
#
# Génère tout ce qui est PROPRE À CET APPAREIL, puis démarre l'application :
#   - hostname unique  bl-rmo-XXXX  (XXXX dérivé du n° de série du Pi)
#   - certificat SSL auto-signé (openssl) pour ce hostname
#   - SECRET_KEY Flask aléatoire
#   - mot de passe Icecast aléatoire
#   - mot de passe admin par défaut 'password' (hash bcrypt) — À CHANGER
#   - config.json initial (valeurs par défaut)
#   - config Nginx finale (server_name rempli)
#   - démarrage des services
#   - marqueur /var/lib/blfmo/.provisioned pour ne jamais rejouer
#
# Lancé par le service systemd bl-rmo-firstboot (conditionné à l'absence
# du marqueur). Tourne en root.
#
set -euo pipefail

log() { echo "[firstboot] $1"; }

MARKER_DIR="/var/lib/blfmo"
MARKER="$MARKER_DIR/.provisioned"

# Sécurité : ne jamais rejouer.
if [ -f "$MARKER" ]; then
    log "Déjà provisionné — rien à faire."
    exit 0
fi

# ─── Utilisateur cible + chemins (l'app tourne sous cet utilisateur) ──────
BLFMO_USER="$(getent passwd 1000 | cut -d: -f1 || echo pi)"
BLFMO_HOME="$(getent passwd "$BLFMO_USER" | cut -d: -f6)"
INSTALL_DIR="$BLFMO_HOME/bl-rmo"

as_user() { sudo -u "$BLFMO_USER" -H bash -c "$1"; }

log "Utilisateur : $BLFMO_USER | app : $INSTALL_DIR"

# ─── 1. Hostname unique dérivé du n° de série du Pi ───────────────────────
# Le serial est dans /proc/cpuinfo (16 hex). On prend les 4 derniers.
SERIAL="$(awk '/Serial/ {print $3}' /proc/cpuinfo 2>/dev/null | tail -1)"
if [ -z "$SERIAL" ]; then
    # Repli : dérivé de l'adresse MAC eth0 si pas de serial
    SERIAL="$(cat /sys/class/net/eth0/address 2>/dev/null | tr -d ':' || echo 0000)"
fi
SUFFIX="$(echo "$SERIAL" | tail -c 5 | tr 'A-Z' 'a-z')"
HOSTNAME_NEW="bl-rmo-$SUFFIX"
log "Hostname : $HOSTNAME_NEW.local"

hostnamectl set-hostname "$HOSTNAME_NEW"
# Mettre à jour /etc/hosts
sed -i "s/127.0.1.1.*/127.0.1.1\t$HOSTNAME_NEW/" /etc/hosts 2>/dev/null || \
    echo -e "127.0.1.1\t$HOSTNAME_NEW" >> /etc/hosts

# ─── 2. Certificat SSL auto-signé pour ce hostname ────────────────────────
log "Génération du certificat SSL auto-signé…"
mkdir -p /etc/nginx/ssl
openssl req -x509 -newkey rsa:4096 -nodes \
    -out /etc/nginx/ssl/bl-rmo.crt \
    -keyout /etc/nginx/ssl/bl-rmo.key \
    -days 3650 \
    -subj "/C=FR/O=BL-RMO/CN=$HOSTNAME_NEW.local" \
    2>/dev/null
chmod 644 /etc/nginx/ssl/bl-rmo.crt
chmod 600 /etc/nginx/ssl/bl-rmo.key

# ─── 3. Config Nginx : remplir le hostname ────────────────────────────────
log "Finalisation de la config Nginx…"
sed -i "s/__MDNS_HOSTNAME__/$HOSTNAME_NEW/g" /etc/nginx/sites-available/bl-rmo

# ─── 4. Secrets : SECRET_KEY + mot de passe Icecast ───────────────────────
log "Génération des secrets…"
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
ICECAST_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"

# Appliquer le mot de passe Icecast dans sa config
if [ -f /etc/icecast2/icecast.xml ]; then
    sed -i "s#<source-password>[^<]*</source-password>#<source-password>$ICECAST_PW</source-password>#" /etc/icecast2/icecast.xml
    sed -i "s#<relay-password>[^<]*</relay-password>#<relay-password>$ICECAST_PW</relay-password>#" /etc/icecast2/icecast.xml
    sed -i "s#<admin-password>[^<]*</admin-password>#<admin-password>$ICECAST_PW</admin-password>#" /etc/icecast2/icecast.xml
fi

# ─── 5. config.json initial + hash mot de passe admin ─────────────────────
log "Création de config.json (si absent)…"
if [ ! -f "$INSTALL_DIR/config.json" ]; then
    PW_HASH="$(cd "$INSTALL_DIR" && ./venv/bin/python3 -c "from flask_bcrypt import Bcrypt; from flask import Flask; print(Bcrypt(Flask(__name__)).generate_password_hash('password').decode())")"
    cat > "$INSTALL_DIR/config.json" <<JSON
{
  "station": { "name": "Ma Radio FM", "frequency": "88.6M", "frequency_display": "88.6 MHz" },
  "rtl_sdr": { "frequency": "88.6M", "sample_rate": "1140000", "gain": "40", "device_index": "0", "ppm_error": 0 },
  "audio": { "output_rate": "44100", "silence_threshold": -40.0, "silence_duration": 30, "enabled": true },
  "flask": { "secret_key": "$SECRET_KEY" },
  "icecast": { "source_password": "$ICECAST_PW" },
  "auth": { "username": "admin", "password_hash": "$PW_HASH", "password_is_default": true },
  "email": { "sender_email": "", "sender_password": "", "recipient_emails": [], "alerts_enabled": false }
}
JSON
    chown "$BLFMO_USER:$BLFMO_USER" "$INSTALL_DIR/config.json"
    chmod 600 "$INSTALL_DIR/config.json"
    log "config.json créé (mot de passe admin par défaut : 'password')."
fi

# ─── 6. Démarrer les services ─────────────────────────────────────────────
# IMPORTANT : démarrages NON bloquants. Ce script est lui-même un service
# systemd (oneshot) ; un `systemctl start` bloquant sur un service ordonné
# après nous créerait un interblocage. --no-block évite ça. bl-rmo est
# de toute façon `enabled` et démarrera seul une fois ce oneshot terminé.
log "Démarrage des services…"
systemctl restart avahi-daemon || true
systemctl restart icecast2 || true
sleep 2
if nginx -t 2>/dev/null; then
    systemctl restart --no-block nginx || true
fi
systemctl start --no-block bl-rmo.service || true

# ─── 7. Marqueur : ne jamais rejouer ──────────────────────────────────────
mkdir -p "$MARKER_DIR"
{
    echo "provisioned: $(date -Iseconds)"
    echo "hostname: $HOSTNAME_NEW.local"
} > "$MARKER"

log "Provisioning terminé."
log "Accès : https://$HOSTNAME_NEW.local  (admin / password — À CHANGER)"

# Le service oneshot se termine ; il ne se relancera pas (marqueur présent).
exit 0
