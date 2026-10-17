#!/bin/bash
#
# BL-RMO build — Étape 50 : déploiement de l'application
#
# Clone le dépôt dans le HOME de l'utilisateur principal, crée le venv Python
# et installe les dépendances. Ne génère AUCUN secret ni config (→ firstboot.sh).
#
# Point de vigilance ARM : scipy/numpy. Sur Pi 4, si pip ne trouve pas de wheel
# précompilé, la compilation peut être très longue (ou échouer par manque de RAM).
# Le script détecte ce cas et propose le repli sur les paquets système apt.
#
# Idempotent : si déjà cloné, git pull ; le venv est recréé proprement.
#
set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${BLUE}[50-app]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1" >&2; }

# ─── Utilisateur cible + chemins ──────────────────────────────────────────
BLFMO_USER="${BLFMO_USER:-${SUDO_USER:-$(id -un 1000 2>/dev/null || echo pi)}}"
BLFMO_HOME="$(getent passwd "$BLFMO_USER" | cut -d: -f6)"
INSTALL_DIR="${INSTALL_DIR:-$BLFMO_HOME/bl-rmo}"
GITHUB_REPO="${GITHUB_REPO:-https://github.com/LyonelB/BL-RMO.git}"

log "Utilisateur : $BLFMO_USER"
log "Installation : $INSTALL_DIR"

# Helper : exécuter une commande EN TANT QUE l'utilisateur cible (pas root).
as_user() { sudo -u "$BLFMO_USER" -H bash -c "$1"; }

# ─── 1. Clone / mise à jour du dépôt ──────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Dépôt déjà présent — git pull…"
    as_user "cd '$INSTALL_DIR' && git pull --ff-only" || warn "git pull a échoué (modifs locales ?) — on continue avec l'existant."
else
    if [ -e "$INSTALL_DIR" ]; then
        err "$INSTALL_DIR existe mais n'est pas un dépôt git. Déplace-le ou supprime-le."
        exit 1
    fi
    log "Clonage de BL-RMO…"
    as_user "git clone --quiet '$GITHUB_REPO' '$INSTALL_DIR'"
fi
ok "Code déployé dans $INSTALL_DIR"

# ─── 2. Environnement virtuel Python ──────────────────────────────────────
if [ ! -d "$INSTALL_DIR/venv" ]; then
    log "Création du venv…"
    as_user "cd '$INSTALL_DIR' && python3 -m venv venv"
else
    ok "venv déjà présent."
fi

log "Mise à jour pip/setuptools/wheel…"
as_user "cd '$INSTALL_DIR' && ./venv/bin/pip install --quiet --upgrade pip setuptools wheel"

# ─── 3. Dépendances : numpy/scipy d'abord (le point sensible ARM) ─────────
# On les installe séparément pour voir clairement si pip compile ou pose un wheel.
log "Installation de numpy et scipy (peut être long sur Pi si compilation)…"
if as_user "cd '$INSTALL_DIR' && timeout 1800 ./venv/bin/pip install --only-binary=:all: numpy scipy" 2>/dev/null; then
    ok "numpy + scipy installés depuis des wheels précompilés (rapide)."
else
    warn "Pas de wheel précompilé pour numpy/scipy (ou version épinglée indisponible)."
    warn "Repli sur les paquets système apt (python3-numpy, python3-scipy)…"
    apt-get install -y -qq python3-numpy python3-scipy
    # Recréer le venv avec accès aux paquets système pour numpy/scipy.
    log "Recréation du venv avec --system-site-packages (pour numpy/scipy système)…"
    as_user "cd '$INSTALL_DIR' && rm -rf venv && python3 -m venv --system-site-packages venv"
    as_user "cd '$INSTALL_DIR' && ./venv/bin/pip install --quiet --upgrade pip setuptools wheel"
    ok "Repli système en place."
fi

# ─── 4. Reste des dépendances ─────────────────────────────────────────────
log "Installation des dépendances restantes (requirements.txt)…"
as_user "cd '$INSTALL_DIR' && ./venv/bin/pip install -r requirements.txt"
ok "Dépendances Python installées."

# ─── 5. Vérification : les imports critiques passent-ils ? ────────────────
log "Vérification des imports critiques…"
FAIL=0
check_import() {
    if as_user "cd '$INSTALL_DIR' && ./venv/bin/python3 -c 'import $1' 2>/dev/null"; then
        ok "import $1 OK"
    else
        err "import $1 ÉCHOUE"
        FAIL=1
    fi
}
check_import flask
check_import numpy
check_import scipy
check_import serial      # pyserial
check_import sounddevice
check_import bcrypt

echo ""
if [ "$FAIL" -eq 0 ]; then
    ok "Étape 50 OK — application déployée, dépendances prêtes."
    log "Secrets et config : générés au premier boot (firstboot.sh)."
else
    err "Étape 50 INCOMPLÈTE — imports en échec ci-dessus."
    exit 1
fi
