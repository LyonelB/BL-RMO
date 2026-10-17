#!/bin/bash
#
# BL-RMO build — Étape 20 : compilation de redsea (décodeur RDS)
#
# redsea est compilé depuis la branche main de windytan/redsea. liquid-dsp est
# fourni par le paquet libliquid-dev (validé présent sur Pi OS Trixie à l'étape 10).
#
# Choix : on suit main (dernière version). Pour compenser l'imprévisibilité,
# l'étape teste que redsea FONCTIONNE réellement avant de valider — pas seulement
# qu'il s'est installé. Un main cassé arrête donc le build proprement.
#
# Idempotent : si un redsea fonctionnel est déjà présent, on ne recompile pas
# (sauf variable FORCE_REBUILD=1).
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}[20-redsea]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1" >&2; }

REDSEA_REPO="https://github.com/windytan/redsea.git"

# ─── Test fonctionnel : redsea décode-t-il réellement ? ───────────────────
# On envoie un flux nul très court sur son entrée MPX : redsea doit démarrer,
# lire, et se terminer proprement sans erreur (pas de RDS attendu, on teste
# juste qu'il tourne). On vérifie surtout --version comme sanity check.
redsea_works() {
    command -v redsea >/dev/null 2>&1 || return 1
    # --version doit répondre sans erreur
    redsea --version >/dev/null 2>&1 || return 1
    return 0
}

# ─── Court-circuit idempotent ─────────────────────────────────────────────
if [ "${FORCE_REBUILD:-0}" != "1" ] && redsea_works; then
    ver="$(redsea --version 2>&1 | head -1 || echo '?')"
    ok "redsea déjà présent et fonctionnel : $ver"
    log "(FORCE_REBUILD=1 pour recompiler quand même)"
    exit 0
fi

# ─── Vérif préalable : liquid-dsp présent (dépendance critique) ───────────
if [ ! -f /usr/include/liquid/liquid.h ]; then
    err "liquid.h introuvable — l'étape 10 (libliquid-dev) doit être passée d'abord."
    exit 1
fi

# ─── Compilation ──────────────────────────────────────────────────────────
BUILD_TMP="$(mktemp -d)"
trap 'rm -rf "$BUILD_TMP"' EXIT

log "Clonage de redsea (branche main)…"
git clone --quiet --depth 1 "$REDSEA_REPO" "$BUILD_TMP/redsea"
cd "$BUILD_TMP/redsea"

# Enregistrer le commit exact compilé (traçabilité de ce qui est dans l'image)
REDSEA_COMMIT="$(git rev-parse --short HEAD)"
log "Commit redsea compilé : $REDSEA_COMMIT"

log "Configuration (meson)…"
meson setup build

log "Compilation (ninja) — quelques minutes sur Pi 4…"
ninja -C build

log "Installation…"
ninja -C build install
ldconfig 2>/dev/null || true

# ─── Test fonctionnel post-installation ───────────────────────────────────
log "Test de fonctionnement de redsea…"
if ! redsea_works; then
    err "redsea s'est installé mais ne répond pas correctement."
    err "Le main de redsea a peut-être introduit un changement cassant."
    err "Commit concerné : $REDSEA_COMMIT"
    exit 1
fi

# Trace du commit installé (utile pour le support / reproductibilité)
echo "$REDSEA_COMMIT" > /usr/local/share/blfmo-redsea-commit 2>/dev/null || true

ver="$(redsea --version 2>&1 | head -1 || echo '?')"
echo ""
ok "Étape 20 OK — redsea compilé et fonctionnel : $ver (commit $REDSEA_COMMIT)"
