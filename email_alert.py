#!/usr/bin/env python3
"""
Module de gestion des alertes email pour le système de surveillance FM
"""

import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class EmailAlert:
    def __init__(self, config_path='config.json'):
        """Initialise le système d'alertes email"""
        with open(config_path, 'r') as f:
            config = json.load(f)

        self.config = config['email']
        self.station_name = config['station']['name']
        self.frequency = config['station']['frequency_display']

        self.last_alert_time = None
        self.cooldown = timedelta(minutes=self.config.get('cooldown_minutes', 1))  # 1 minute par défaut

        # Toggle "alertes actives" (live, piloté par l'UI, sans redémarrage)
        self.alerts_enabled = self.config.get('alerts_enabled', True)

    def reload_config(self, config_path='config.json'):
        """Recharge la config email/station depuis le disque (à chaud, sans redémarrer le service).

        À appeler après chaque sauvegarde de config.json qui touche 'email' ou 'station',
        sinon les emails d'alerte continuent d'utiliser le nom/fréquence de station et les
        identifiants SMTP chargés au démarrage du processus.
        """
        with open(config_path, 'r') as f:
            config = json.load(f)

        self.config = config['email']
        self.station_name = config['station']['name']
        self.frequency = config['station']['frequency_display']
        self.cooldown = timedelta(minutes=self.config.get('cooldown_minutes', 1))
        self.alerts_enabled = self.config.get('alerts_enabled', True)

    def can_send_alert(self):
        """Vérifie si on peut envoyer une alerte (cooldown)"""
        if not self.config['enabled']:
            return False

        if self.last_alert_time is None:
            return True

        return datetime.now() - self.last_alert_time > self.cooldown

    def send_alert(self, alert_type, details="", skip_cooldown=False):
        """Envoie une alerte email
        
        Args:
            alert_type: Type d'alerte
            details: Détails de l'alerte
            skip_cooldown: Si True, ignore le cooldown (pour les rétablissements)
        """
        # Garde-fou toggle : couper les alertes automatiques quand désactivé
        # (l'email de test, alert_type="Test", reste toujours autorisé)
        if not getattr(self, 'alerts_enabled', True) and alert_type != "Test":
            logger.info(f"Alerte '{alert_type}' non envoyée (alertes coupées via toggle)")
            return False
        # Ignorer le cooldown si c'est un rétablissement OU si skip_cooldown=True
        if "rétabli" in alert_type.lower() or skip_cooldown:
            logger.info(f"Envoi de l'alerte '{alert_type}' (cooldown ignoré)")
        elif not self.can_send_alert():
            logger.info("Alerte non envoyée (cooldown actif)")
            return False

        try:
            # Créer le message
            msg = MIMEMultipart('alternative')
            
            # Sujet différent selon le type d'alerte
            if "rétabli" in alert_type.lower():
                msg['Subject'] = f"✅ RÉTABLI - {self.station_name} - {alert_type}"
            else:
                msg['Subject'] = f"⚠️ ALERTE - {self.station_name} - {alert_type}"
                
            msg['From'] = self.config['sender_email']
            msg['To'] = ', '.join(self.config['recipient_emails'])

            # Corps du message
            timestamp = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

            text_content = f"""
ALERTE DE SURVEILLANCE FM
========================

Station: {self.station_name}
Fréquence: {self.frequency}
Type d'alerte: {alert_type}
Date et heure: {timestamp}

Détails:
{details}

---
BL-RMO · Surveillance radio
            """

            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    .alert-box {{
                        background-color: {"#d4edda" if "rétabli" in alert_type.lower() else "#fff3cd"};
                        border-left: 4px solid {"#28a745" if "rétabli" in alert_type.lower() else "#ffc107"};
                        padding: 20px;
                        margin: 20px 0;
                    }}
                    .header {{ color: {"#155724" if "rétabli" in alert_type.lower() else "#856404"}; font-size: 24px; font-weight: bold; }}
                    .info {{ margin: 10px 0; }}
                    .label {{ font-weight: bold; color: #333; }}
                    .footer {{ margin-top: 20px; color: #666; font-size: 12px; }}
                </style>
            </head>
            <body>
                <div class="alert-box">
                    <div class="header">{"✅" if "rétabli" in alert_type.lower() else "⚠️"} {alert_type.upper()}</div>
                    <hr>
                    <div class="info"><span class="label">Station:</span> {self.station_name}</div>
                    <div class="info"><span class="label">Fréquence:</span> {self.frequency}</div>
                    <div class="info"><span class="label">Type d'alerte:</span> {alert_type}</div>
                    <div class="info"><span class="label">Date et heure:</span> {timestamp}</div>
                    <hr>
                    <div class="info"><span class="label">Détails:</span><br>{details}</div>
                    <div class="footer">BL-RMO · Surveillance radio</div>
                </div>
            </body>
            </html>
            """

            # Attacher les deux versions
            part1 = MIMEText(text_content, 'plain', 'utf-8')
            part2 = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part1)
            msg.attach(part2)

            # Envoyer l'email
            # Mettre à jour le cooldown AVANT la tentative d'envoi
            # pour éviter la boucle infinie en cas d'échec SMTP
            self.last_alert_time = datetime.now()

            with smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port']) as server:
                if self.config['use_tls']:
                    server.starttls()

                server.login(self.config['sender_email'], self.config['sender_password'])
                server.sendmail(self.config['sender_email'], self.config['recipient_emails'], msg.as_string())

            logger.info(f"Alerte email envoyée: {alert_type}")
            return True

        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de l'email: {e}")
            return False

    def send_recovery_alert(self):
        """Envoie une alerte de rétablissement du signal"""
        if not getattr(self, 'alerts_enabled', True):
            logger.info("Alerte de rétablissement non envoyée (alertes coupées via toggle)")
            return False
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"✅ RÉTABLI - {self.station_name}"
            msg['From'] = self.config['sender_email']
            msg['To'] = ', '.join(self.config['recipient_emails'])

            timestamp = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

            text_content = f"""
SIGNAL RÉTABLI
==============

Station: {self.station_name}
Fréquence: {self.frequency}
Date et heure: {timestamp}

Le signal FM a été rétabli avec succès.

---
BL-RMO · Surveillance radio
            """

            html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    .success-box {{
                        background-color: #d4edda;
                        border-left: 4px solid #28a745;
                        padding: 20px;
                        margin: 20px 0;
                    }}
                    .header {{ color: #155724; font-size: 24px; font-weight: bold; }}
                    .info {{ margin: 10px 0; }}
                    .label {{ font-weight: bold; color: #333; }}
                    .footer {{ margin-top: 20px; color: #666; font-size: 12px; }}
                </style>
            </head>
            <body>
                <div class="success-box">
                    <div class="header">✅ SIGNAL RÉTABLI</div>
                    <hr>
                    <div class="info"><span class="label">Station:</span> {self.station_name}</div>
                    <div class="info"><span class="label">Fréquence:</span> {self.frequency}</div>
                    <div class="info"><span class="label">Date et heure:</span> {timestamp}</div>
                    <hr>
                    <p>Le signal FM a été rétabli avec succès.</p>
                    <div class="footer">BL-RMO · Surveillance radio</div>
                </div>
            </body>
            </html>
            """

            part1 = MIMEText(text_content, 'plain', 'utf-8')
            part2 = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part1)
            msg.attach(part2)

            # Mettre à jour le cooldown AVANT la tentative d'envoi
            self.last_alert_time = datetime.now()

            with smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port']) as server:
                if self.config['use_tls']:
                    server.starttls()

                server.login(self.config['sender_email'], self.config['sender_password'])
                server.sendmail(self.config['sender_email'], self.config['recipient_emails'], msg.as_string())

            logger.info("Alerte de rétablissement envoyée")
            return True

        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de l'alerte de rétablissement: {e}")
            return False

