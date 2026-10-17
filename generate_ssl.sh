#!/bin/bash
# Script de génération de certificat SSL auto-signé pour BL-RMO

echo "=========================================="
echo "Génération du certificat SSL"
echo "=========================================="
echo ""

cd ~/fm-monitor

# Générer une clé privée et un certificat auto-signé valide 10 ans
echo "Génération du certificat (valide 10 ans)..."
openssl req -x509 -newkey rsa:4096 -nodes \
  -out cert.pem \
  -keyout key.pem \
  -days 3650 \
  -subj "/C=FR/ST=France/L=Paris/O=BL-RMO/OU=Radio/CN=fm-monitor.local" \
  2>/dev/null

if [ $? -eq 0 ]; then
    echo "✅ Certificat généré avec succès"
    echo ""
    echo "Fichiers créés :"
    ls -lh cert.pem key.pem
    echo ""
    echo "Détails du certificat :"
    openssl x509 -in cert.pem -text -noout | grep -A2 "Subject:"
    echo ""
    echo "Validité :"
    openssl x509 -in cert.pem -text -noout | grep -A2 "Validity"
    echo ""
    
    # Permissions correctes
    chmod 644 cert.pem
    chmod 600 key.pem
    
    echo "✅ Permissions configurées"
    echo "   cert.pem : 644 (lecture publique)"
    echo "   key.pem  : 600 (lecture propriétaire seulement)"
else
    echo "❌ Erreur lors de la génération du certificat"
    exit 1
fi

echo ""
echo "=========================================="
echo "Certificat SSL prêt !"
echo "=========================================="
echo ""
echo "⚠️  IMPORTANT :"
echo "   Le certificat est auto-signé."
echo "   Votre navigateur affichera un avertissement."
echo "   C'est NORMAL et SANS DANGER."
echo ""
echo "Pour accepter le certificat :"
echo "   1. Cliquez sur 'Avancé' ou 'Advanced'"
echo "   2. Cliquez sur 'Continuer vers le site' ou 'Proceed'"
echo ""

