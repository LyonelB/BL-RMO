#!/bin/bash
#
# BL-RMO build — Étape 40 : accès aux dongles (RTL-SDR + TEF)
#
# Sur Pi OS Trixie (validé sur matériel réel) :
#   - le paquet rtl-sdr 2.0.2 reconnaît nativement le RTL-SDR Blog V4
#     (pas besoin du fork rtlsdrblog) ;
#   - le TEF Lite SE s'énumère sur /dev/ttyACM0 (VID:PID 1209:6687).
#
# Cette étape DURCIT l'accès, elle ne débloque pas (le Pi est déjà permissif) :
#   1. Blacklist du driver noyau DVB (dvb_usb_rtl28xxu) — inconditionnel,
#      pour que le RTL-SDR ne soit jamais accaparé par le noyau en service 24/7.
#   2. Règles udev pour les DEUX dongles, accès universel (MODE=0666), afin que
#      l'accès ne dépende pas de l'utilisateur qui fait tourner le service.
#   3. Groupes plugdev/dialout garantis (ceinture + bretelles).
#
# Idempotent. Ne modifie PAS la logique applicative (ni RTL-SDR ni TEF).
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}[40-drivers]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

# Utilisateur cible (hérité de build.sh, sinon détection).
BLFMO_USER="${BLFMO_USER:-${SUDO_USER:-$(id -un 1000 2>/dev/null || echo pi)}}"

# ─── 1. Blacklist du driver DVB ───────────────────────────────────────────
DVB_BLACKLIST="/etc/modprobe.d/blacklist-rtl-dvb.conf"
log "Blacklist du driver noyau DVB (dvb_usb_rtl28xxu)…"
cat > "$DVB_BLACKLIST" <<'EOF'
# BL-RMO : empêche le driver TV/DVB de s'accaparer le RTL-SDR.
# Nécessaire pour l'usage SDR en service permanent.
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
ok "Blacklist écrite : $DVB_BLACKLIST"

# Décharger le module s'il est chargé maintenant (sinon effectif au prochain boot).
if lsmod | grep -q dvb_usb_rtl28xxu; then
    modprobe -r dvb_usb_rtl28xxu 2>/dev/null && ok "Module DVB déchargé à chaud." \
        || warn "Module DVB en cours d'utilisation — sera écarté au prochain boot."
else
    ok "Module DVB non chargé actuellement."
fi

# ─── 2. Règles udev — accès universel aux deux dongles ────────────────────
UDEV_RULES="/etc/udev/rules.d/99-blfmo-dongles.rules"
log "Écriture des règles udev (RTL-SDR + TEF)…"
cat > "$UDEV_RULES" <<'EOF'
# BL-RMO : accès aux dongles de monitoring FM, pour tout utilisateur.

# — RTL-SDR (Realtek RTL2832U, tous modèles dont Blog V4/V4L) —
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666", GROUP="plugdev", TAG+="uaccess"

# — TEF668X Headless (FMDX.org, Lite SE) : USB natif + port CDC-ACM —
SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="6687", MODE="0666", GROUP="dialout", TAG+="uaccess"
# Le /dev/ttyACM* correspondant (accès série)
SUBSYSTEM=="tty", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="6687", MODE="0660", GROUP="dialout", TAG+="uaccess"
EOF
ok "Règles udev écrites : $UDEV_RULES"

log "Rechargement des règles udev…"
udevadm control --reload-rules
udevadm trigger
ok "Règles udev rechargées."

# ─── 3. Groupes garantis pour l'utilisateur cible ─────────────────────────
log "Vérification des groupes de $BLFMO_USER…"
for grp in plugdev dialout; do
    if id -nG "$BLFMO_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$grp"; then
        ok "$BLFMO_USER déjà dans $grp"
    else
        usermod -aG "$grp" "$BLFMO_USER" && ok "$BLFMO_USER ajouté à $grp"
    fi
done

# ─── 4. Vérifications ─────────────────────────────────────────────────────
echo ""
log "Vérifications…"
FAIL=0

[ -f "$DVB_BLACKLIST" ] && ok "Blacklist DVB présente" || { warn "Blacklist DVB absente"; FAIL=1; }
[ -f "$UDEV_RULES" ] && ok "Règles udev présentes" || { warn "Règles udev absentes"; FAIL=1; }

# Détection à chaud (informative — dépend de ce qui est branché maintenant)
if lsusb | grep -q '0bda:283[28]'; then ok "RTL-SDR détecté (branché)"; else log "RTL-SDR non branché actuellement (normal)"; fi
if lsusb | grep -q '1209:6687'; then
    ok "TEF détecté (branché)"
    [ -e /dev/ttyACM0 ] && ok "/dev/ttyACM0 présent" || warn "TEF branché mais pas de /dev/ttyACM0 ?"
else
    log "TEF non branché actuellement (normal)"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    ok "Étape 40 OK — accès dongles configuré (blacklist DVB + udev universel)."
    warn "Le blacklist DVB devient pleinement effectif après un redémarrage."
else
    warn "Étape 40 terminée avec avertissements — voir ci-dessus."
fi
