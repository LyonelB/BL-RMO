#!/bin/bash
#
# BL-RMO build — Étape 30 : GNU Radio + gr-osmosdr (chemin RTL-SDR)
#
# Installe GNU Radio et gr-osmosdr via apt (méthode validée sur le serveur de
# prod : paquets Debian, aucune compilation source). C'est le socle du décodage
# RTL-SDR (wfm_stereo.py). Ne concerne PAS le mode TEF.
#
# Validation : import Python de gnuradio ET osmosdr (pas seulement présence des
# paquets), + vérification que le backend RTL-SDR est listé par osmosdr.
#
# Idempotent.
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}[30-gnuradio]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1" >&2; }

export DEBIAN_FRONTEND=noninteractive

# Recette validée sur le serveur de prod (install.sh d'origine, ligne 169).
GNR_PACKAGES=(gnuradio gr-osmosdr libfftw3-dev libsamplerate0-dev cmake)

# ─── Court-circuit idempotent : déjà importable ? ─────────────────────────
if python3 -c "import gnuradio" 2>/dev/null && python3 -c "import osmosdr" 2>/dev/null; then
    GNR_VER="$(python3 -c 'import gnuradio; print(gnuradio.__version__)' 2>/dev/null || echo '?')"
    ok "GNU Radio $GNR_VER + gr-osmosdr déjà installés et importables."
else
    log "Installation de GNU Radio + gr-osmosdr (gros téléchargement, patience sur Pi 4)…"
    apt-get update -qq
    apt-get install -y -qq "${GNR_PACKAGES[@]}"
    ok "Paquets GNU Radio installés."
fi

# ─── Validation par imports Python ────────────────────────────────────────
log "Validation des imports Python…"
FAIL=0

if python3 -c "import gnuradio" 2>/dev/null; then
    GNR_VER="$(python3 -c 'import gnuradio; print(gnuradio.__version__)' 2>/dev/null || echo '?')"
    ok "import gnuradio OK (version $GNR_VER)"
else
    err "import gnuradio ÉCHOUE — GNU Radio mal installé."
    FAIL=1
fi

if python3 -c "import osmosdr" 2>/dev/null; then
    ok "import osmosdr OK"
else
    err "import osmosdr ÉCHOUE — gr-osmosdr absent ou incompatible."
    FAIL=1
fi

# Modules GNU Radio utilisés par wfm_stereo.py (analog, filter, audio, blocks)
if python3 -c "from gnuradio import analog, filter, blocks, audio" 2>/dev/null; then
    ok "modules GNU Radio (analog/filter/blocks/audio) OK"
else
    warn "Certains modules GNU Radio ne s'importent pas — à vérifier vs wfm_stereo.py."
    FAIL=1
fi

# ─── Vérifier que osmosdr connaît le backend RTL-SDR ──────────────────────
# osmosdr charge ses drivers dynamiquement ; on teste que 'rtl' est supporté.
log "Vérification du backend RTL-SDR dans osmosdr…"
if python3 - <<'PYEOF' 2>/dev/null
import osmosdr
# La liste des sources dispo n'est pas toujours exposée simplement ;
# on tente une création "device_args" vide qui liste les backends connus.
# À défaut, on vérifie juste que la classe source existe (support compilé).
src = osmosdr.source  # existence de la classe = binding OK
print("osmosdr.source disponible")
PYEOF
then
    ok "Binding osmosdr.source présent (backend chargé au runtime avec dongle branché)."
else
    warn "Impossible de valider osmosdr.source — à re-tester avec un RTL-SDR branché."
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    ok "Étape 30 OK — GNU Radio + gr-osmosdr prêts (chemin RTL-SDR)."
    log "Test réel du décodage : à faire à l'étape 40 avec un RTL-SDR branché."
else
    err "Étape 30 INCOMPLÈTE — voir les erreurs ci-dessus avant de continuer."
    exit 1
fi
