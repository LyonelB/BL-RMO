#!/bin/bash
#
# BL-RMO build - Étape 67 : réception DAB+ (reprise de BL-DMO)
#
# - Service systemd bl-rmo-dab (dab_monitor.py). Il tourne en veille tant
#   que la réception DAB+ n'est pas activée dans la page DAB+ (config.json,
#   section "dab").
# - Clé RTL-SDR : paquet rtl-sdr, pilote TNT du noyau écarté, accès USB pour
#   l'utilisateur, script de réinitialisation USB autorisé sans mot de passe.
# - welle-cli : compilé depuis le fork welle-monitor si BUILD_WELLE=1
#   (environ 10 min sur un mini PC), sinon on garde celui qui est installé.
#
# Idempotent. À lancer avec sudo.
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${BLUE}[67-dab]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

BLFMO_USER="${BLFMO_USER:-${SUDO_USER:-$(id -un 1000 2>/dev/null || echo pi)}}"
BLFMO_HOME="$(getent passwd "$BLFMO_USER" | cut -d: -f6)"
INSTALL_DIR="${INSTALL_DIR:-$BLFMO_HOME/bl-rmo}"
BUILD_WELLE="${BUILD_WELLE:-0}"
WELLE_REPO="${WELLE_REPO:-https://github.com/LyonelB/welle-monitor.git}"

log "Paquets RTL-SDR…"
apt-get install -y -qq rtl-sdr usbutils >/dev/null
ok "rtl-sdr"

log "Pilote TNT du noyau écarté (sinon la clé n'est pas utilisable)…"
tee /etc/modprobe.d/blacklist-rtl-dvb.conf > /dev/null <<'B'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
B
modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
ok "dvb_usb_rtl28xxu écarté"

log "Accès USB à la clé pour $BLFMO_USER…"
usermod -a -G plugdev "$BLFMO_USER"
tee /etc/udev/rules.d/20-rtlsdr.rules > /dev/null <<'U'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0666"
U
udevadm control --reload-rules && udevadm trigger
ok "Règle udev RTL-SDR"

log "Script de réinitialisation USB de la clé…"
tee /usr/local/bin/rtlsdr-usb-reset.sh > /dev/null <<'R'
#!/bin/bash
# Réinitialise la clé RTL-SDR (0bda:2838 / 0bda:2832) sans la débrancher
for d in /sys/bus/usb/devices/*; do
    [ -f "$d/idVendor" ] || continue
    if [ "$(cat "$d/idVendor")" = "0bda" ] && grep -qE '^(2838|2832)$' "$d/idProduct"; then
        echo 0 > "$d/authorized"; sleep 1; echo 1 > "$d/authorized"
    fi
done
R
chmod 755 /usr/local/bin/rtlsdr-usb-reset.sh
echo "$BLFMO_USER ALL=(root) NOPASSWD: /usr/local/bin/rtlsdr-usb-reset.sh" > /etc/sudoers.d/rtlsdr-reset
chmod 440 /etc/sudoers.d/rtlsdr-reset
ok "/usr/local/bin/rtlsdr-usb-reset.sh"

if [ "$BUILD_WELLE" = "1" ]; then
    log "Compilation de welle-cli (fork welle-monitor)…"
    # xxd : welle-cli intègre ses pages web (index.html, index.js, favicon) à la compilation
    apt-get install -y -qq git cmake g++ pkg-config xxd libfaad-dev libfftw3-dev librtlsdr-dev \
        libmpg123-dev libmp3lame-dev libsoapysdr-dev >/dev/null
    SRC=/usr/local/src/welle-monitor
    if [ -d "$SRC/.git" ]; then git -C "$SRC" pull --quiet; else git clone --quiet "$WELLE_REPO" "$SRC"; fi
    # Dossier de compilation neuf : un cache CMake d'un essai raté garde les outils introuvables
    rm -rf "$SRC/build" && mkdir -p "$SRC/build"
    cd "$SRC/build"
    cmake .. -Wno-dev -DRTLSDR=1 -DBUILD_WELLE_IO=OFF -DBUILD_WELLE_CLI=ON >/dev/null
    make -j"$(nproc)" welle-cli >/dev/null
    install -m 755 welle-cli /usr/local/bin/welle-cli
    cd - >/dev/null
    ok "welle-cli installé dans /usr/local/bin"
elif command -v welle-cli >/dev/null; then
    ok "welle-cli présent : $(command -v welle-cli)"
else
    warn "welle-cli absent. Relancer avec BUILD_WELLE=1 pour le compiler."
fi

log "Service bl-rmo-dab…"
install -d -o "$BLFMO_USER" -g "$BLFMO_USER" "$INSTALL_DIR/logs"
tee /etc/systemd/system/bl-rmo-dab.service > /dev/null <<EOF2
[Unit]
Description=BL-RMO - Réception DAB+
After=network-online.target

[Service]
Type=simple
User=$BLFMO_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/dab_monitor.py
KillMode=mixed
TimeoutStopSec=15
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF2

systemctl daemon-reload
systemctl enable bl-rmo-dab.service >/dev/null
if [ -f "$INSTALL_DIR/config.json" ]; then
    systemctl restart bl-rmo-dab.service
    ok "Service bl-rmo-dab activé et démarré (en veille tant que le DAB+ est désactivé)"
else
    ok "Service bl-rmo-dab activé (démarrera au prochain boot)"
fi
