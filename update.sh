#!/bin/bash
#
# BL-RMO - Script de mise à jour
#

set -e

# Couleurs
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_step() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[i]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

echo -e "${BLUE}"
echo "=========================================="
echo "   BL-RMO - Mise à jour"
echo "=========================================="
echo -e "${NC}"

INSTALL_DIR="$HOME/fm-monitor"

# Vérifier que BL-RMO est installé
if [ ! -d "$INSTALL_DIR" ]; then
    echo "BL-RMO n'est pas installé dans $INSTALL_DIR"
    exit 1
fi

cd "$INSTALL_DIR"

# Services BL-RMO présents sur cette machine
SERVICES="fm-monitor"
for s in fm-monitor-pige fm-monitor-syslog; do
    [ -f "/etc/systemd/system/$s.service" ] && SERVICES="$SERVICES $s"
done

# Arrêter les services
print_step "Arrêt des services ($SERVICES)..."
sudo systemctl stop $SERVICES

# Sauvegarder la configuration
print_step "Sauvegarde de la configuration..."
BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp config.json "$BACKUP_DIR/" 2>/dev/null || true
cp .env "$BACKUP_DIR/" 2>/dev/null || true
print_info "Backup: $BACKUP_DIR"

# Mettre à jour depuis Git
print_step "Téléchargement de la dernière version..."
if [ -d ".git" ]; then
    git fetch origin
    git pull origin main
else
    print_warning "Pas de dépôt Git détecté"
    print_info "Mise à jour manuelle requise"
fi

# Activer le venv
source venv/bin/activate

# Mettre à jour les dépendances Python
print_step "Mise à jour des dépendances Python..."
pip install --quiet --upgrade pip
pip install --quiet --upgrade -r requirements.txt

deactivate

# Restaurer la configuration
print_step "Restauration de la configuration..."
if [ -f "$BACKUP_DIR/config.json" ]; then
    cp "$BACKUP_DIR/config.json" config.json
fi
if [ -f "$BACKUP_DIR/.env" ]; then
    cp "$BACKUP_DIR/.env" .env
fi

# Recharger systemd si nécessaire
if [ -f "/etc/systemd/system/fm-monitor.service" ]; then
    sudo systemctl daemon-reload
fi

# Redémarrer les services
print_step "Redémarrage des services..."
sudo systemctl start $SERVICES

sleep 2

# Vérifier le statut
ALL_OK=1
for s in $SERVICES; do
    if sudo systemctl is-active --quiet "$s"; then
        print_info "$s : actif"
    else
        ALL_OK=0
        print_warning "$s ne s'est pas démarré. Logs : sudo journalctl -u $s -n 50"
    fi
done
[ "$ALL_OK" = 1 ] && print_step "Mise à jour terminée avec succès ! (BL-RMO $(python3 -c 'from version import __version__; print(__version__)' 2>/dev/null))"

echo ""
print_info "Changelog: https://github.com/LyonelB/fm-monitor/releases"
echo ""

