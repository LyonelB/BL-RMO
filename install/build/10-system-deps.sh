#!/bin/bash
#
# BL-RMO build — Étape 10 : dépendances système (apt)
#
# Installe UNIQUEMENT les paquets disponibles via apt. Les compilations
# (redsea) et les composants spécifiques (GNU Radio, drivers) sont dans les
# étapes suivantes. Non-interactif, idempotent.
#
# Variables héritées de build.sh : BLFMO_USER
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${BLUE}[10-deps]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

export DEBIAN_FRONTEND=noninteractive

# ─── Pré-configuration Icecast2 (silencieux, avant install) ───────────────
# Le vrai mot de passe source sera RÉGÉNÉRÉ aléatoirement au premier boot.
# Ici on met un placeholder juste pour que l'install apt ne pose pas de question.
log "Pré-configuration silencieuse d'Icecast2…"
debconf-set-selections <<'EOF'
icecast2 icecast2/icecast-setup boolean true
icecast2 icecast2/hostname string localhost
icecast2 icecast2/sourcepassword password CHANGEME_AT_FIRSTBOOT
icecast2 icecast2/relaypassword password CHANGEME_AT_FIRSTBOOT
icecast2 icecast2/adminpassword password CHANGEME_AT_FIRSTBOOT
EOF

# ─── Mise à jour des index apt ────────────────────────────────────────────
log "apt update…"
apt-get update -qq

# ─── Paquets système ──────────────────────────────────────────────────────
# Regroupés par rôle. Note : liquid-dsp est fourni par 'libliquid-dev' sur
# Bookworm — à VALIDER sur le Pi (sur Debian Trixie il fallait compiler la
# source ; Bookworm est antérieur et le paquet devrait convenir pour redsea).
PACKAGES=(
    # — Base Python / build —
    python3-pip python3-venv python3-dev
    build-essential cmake pkg-config git openssl coreutils
    # — Audio / streaming —
    ffmpeg icecast2 libportaudio2
    # — RTL-SDR (outils de base ; le fork driver est en étape 40) —
    rtl-sdr
    # — redsea : dépendances de compilation (build en étape 20) —
    meson ninja-build libsndfile1-dev libliquid-dev
    # — GNU Radio deps annexes (gnuradio lui-même en étape 30) —
    libfftw3-dev libsamplerate0-dev libopenblas-dev
    # — Réseau / accès —
    avahi-daemon nginx mkcert libnss3-tools
    # — Supervision de la chaîne (sondes ping et SNMP) —
    iputils-ping snmp
)

log "Installation des paquets système (${#PACKAGES[@]} paquets)…"
missing=()
for pkg in "${PACKAGES[@]}"; do
    if dpkg -l 2>/dev/null | grep -q "^ii  $pkg "; then
        :  # déjà installé
    else
        missing+=("$pkg")
    fi
done

if [ ${#missing[@]} -eq 0 ]; then
    ok "Tous les paquets système sont déjà présents."
else
    log "À installer : ${missing[*]}"
    apt-get install -y -qq "${missing[@]}"
    ok "Paquets système installés."
fi

# ─── Vérifications minimales ──────────────────────────────────────────────
log "Vérifications…"
FAIL=0
check_cmd() {
    if command -v "$1" >/dev/null 2>&1; then ok "$1 présent"; else warn "$1 ABSENT"; FAIL=1; fi
}
check_cmd python3
check_cmd ffmpeg
check_cmd git
check_cmd meson
check_cmd ninja
check_cmd nginx
check_cmd rtl_test
check_cmd mkcert

# liquid-dsp : header présent ? (déterminant pour la compilation de redsea)
if [ -f /usr/include/liquid/liquid.h ]; then
    ok "liquid-dsp (header) présent — redsea pourra compiler."
else
    warn "liquid.h introuvable — la compilation de redsea (étape 20) échouera."
    warn "Sur cette version d'OS, il faudra peut-être compiler liquid-dsp depuis la source."
    FAIL=1
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    ok "Étape 10 OK — dépendances système prêtes."
else
    warn "Étape 10 terminée AVEC avertissements — vérifie les points ci-dessus avant de continuer."
fi
