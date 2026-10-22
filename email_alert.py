#!/usr/bin/env python3
"""
Module de gestion des alertes email pour le système de surveillance FM
"""

import smtplib
import json
import os
import sys
import threading
import time
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

        # File d'attente des mails qui n'ont pas pu partir (MAIL_QUEUE_PATCH) :
        # réseau ou serveur SMTP indisponible (Livebox qui redémarre, coupure
        # Internet). Les mails sont retentés toutes les RETRY_S secondes, dans
        # l'ordre, avec l'heure réelle de l'événement ; un mail suivant (le
        # rétablissement) attend son tour derrière l'alerte. Un fichier par
        # processus (bl-rmo, bl-rmo-dab) garde la file si le service redémarre.
        base = os.path.splitext(os.path.basename(sys.argv[0] or 'app'))[0] or 'app'
        self._queue_file = os.path.join(os.path.dirname(os.path.abspath(config_path)), f'email_queue_{base}.json')
        self._queue = []
        self._queue_lock = threading.RLock()
        self._retry_thread = None
        self._wake = threading.Event()
        self._load_queue()

    RETRY_S = 60                  # délai entre deux tentatives
    MAX_AGE_S = 24 * 3600         # au-delà, un mail en attente est abandonné

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

    def send_alert(self, alert_type, details="", skip_cooldown=False, on_sent=None):
        """Envoie une alerte email
        
        Args:
            alert_type: Type d'alerte
            details: Détails de l'alerte
            skip_cooldown: Si True, ignore le cooldown (pour les rétablissements)
            on_sent: appelé quand le mail est parti, tout de suite ou plus tard depuis la file

        Renvoie True si le mail est parti tout de suite. En cas d'échec, il est mis
        en attente et retenté (sauf l'email de test).
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

        item = {'type': alert_type, 'details': details, 'ts': time.time(), 'tries': 0}
        if alert_type == "Test":                   # le test dit tout de suite si l'envoi marche
            try:
                self._deliver(item)
                return True
            except Exception as e:
                logger.error(f"Erreur lors de l'envoi de l'email: {e}")
                return False
        if on_sent is not None:
            item['_cb'] = on_sent
        with self._queue_lock:
            waiting = bool(self._queue)
            if waiting:                            # garder l'ordre : alerte avant rétablissement
                self._queue.append(item)
                self._save_queue()
        if waiting:                                # le fil d'envoi s'en charge, dans l'ordre
            logger.info(f"Mail '{alert_type}' placé derrière {len(self._queue) - 1} mail(s) en attente")
            self._start_retry()
            self._wake.set()
            return False
        try:
            self._deliver(item)
            self._fire(item)
            return True
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de l'email: {e}")
            self._enqueue(item)
            return False

    # ── Envoi ───────────────────────────────────────────────────────────
    def _deliver(self, item):
        """Construit et envoie le mail ; lève une exception si l'envoi échoue."""
        alert_type, details = item['type'], item['details']
        recovered = "rétabli" in alert_type.lower()
        msg = MIMEMultipart('alternative')
        msg['Subject'] = (f"✅ RÉTABLI - {self.station_name} - {alert_type}" if recovered
                          else f"⚠️ ALERTE - {self.station_name} - {alert_type}")
        msg['From'] = self.config['sender_email']
        msg['To'] = ', '.join(self.config['recipient_emails'])

        timestamp = datetime.fromtimestamp(item['ts']).strftime('%d/%m/%Y %H:%M:%S')
        delay = time.time() - item['ts']
        late = ''
        if delay > 90:
            late = (f"Envoi différé : mail retenu {int(delay // 60)} min, le réseau ou le serveur de mail "
                    f"était indisponible au moment de l'événement.")
        text_content = f"""
ALERTE DE SURVEILLANCE
======================

Station: {self.station_name}
Fréquence: {self.frequency}
Type d'alerte: {alert_type}
Date et heure: {timestamp}
{late}
Détails:
{details}

---
BL-RMO · Surveillance radio
            """
        late_html = f'<div class="info" style="color:#b45309"><b>{late}</b></div>' if late else ''
        html_content = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    .alert-box {{
                        background-color: {"#d4edda" if recovered else "#fff3cd"};
                        border-left: 4px solid {"#28a745" if recovered else "#ffc107"};
                        padding: 20px;
                        margin: 20px 0;
                    }}
                    .header {{ color: {"#155724" if recovered else "#856404"}; font-size: 24px; font-weight: bold; }}
                    .info {{ margin: 10px 0; }}
                    .label {{ font-weight: bold; color: #333; }}
                    .footer {{ margin-top: 20px; color: #666; font-size: 12px; }}
                </style>
            </head>
            <body>
                <div class="alert-box">
                    <div class="header">{"✅" if recovered else "⚠️"} {alert_type.upper()}</div>
                    <hr>
                    <div class="info"><span class="label">Station:</span> {self.station_name}</div>
                    <div class="info"><span class="label">Fréquence:</span> {self.frequency}</div>
                    <div class="info"><span class="label">Type d'alerte:</span> {alert_type}</div>
                    <div class="info"><span class="label">Date et heure:</span> {timestamp}</div>
                    {late_html}
                    <hr>
                    <div class="info"><span class="label">Détails:</span><br>{details}</div>
                    <div class="footer">BL-RMO · Surveillance radio</div>
                </div>
            </body>
            </html>
            """
        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        # Cooldown mis à jour AVANT la tentative (pas de boucle en cas d'échec SMTP)
        self.last_alert_time = datetime.now()
        with smtplib.SMTP(self.config['smtp_server'], self.config['smtp_port'], timeout=30) as server:
            if self.config['use_tls']:
                server.starttls()
            server.login(self.config['sender_email'], self.config['sender_password'])
            server.sendmail(self.config['sender_email'], self.config['recipient_emails'], msg.as_string())
        logger.info(f"Alerte email envoyée: {alert_type}" + (f" (différée de {int(delay)} s)" if delay > 90 else ''))

    @staticmethod
    def _fire(item):
        cb = item.get('_cb')
        if cb:
            try:
                cb()
            except Exception as e:
                logger.debug(f"Mail envoyé, rappel en erreur : {e}")

    # ── File d'attente ──────────────────────────────────────────────────
    def _enqueue(self, item):
        with self._queue_lock:
            self._queue.append(item)
            self._save_queue()
            logger.warning(f"Mail '{item['type']}' mis en attente, nouvel essai dans {self.RETRY_S} s "
                           f"({len(self._queue)} en attente)")
        self._start_retry()

    def _start_retry(self):
        with self._queue_lock:
            if self._retry_thread and self._retry_thread.is_alive():
                return
            self._retry_thread = threading.Thread(target=self._retry_loop, daemon=True, name='email-retry')
            self._retry_thread.start()

    def _retry_loop(self):
        while True:
            self._wake.wait(self.RETRY_S)
            self._wake.clear()
            if self._flush_queue():
                with self._queue_lock:
                    if not self._queue:
                        self._retry_thread = None
                        return

    def _flush_queue(self):
        """Envoie les mails en attente dans l'ordre ; s'arrête au premier échec. True si la file est vide.
        Appelé seulement par le fil d'envoi : l'envoi se fait hors du verrou, pour ne jamais
        bloquer la surveillance pendant qu'un serveur ne répond pas."""
        while True:
            with self._queue_lock:
                if not self._queue:
                    return True
                item = self._queue[0]
                drop = None
                if time.time() - item['ts'] > self.MAX_AGE_S:
                    drop = 'en attente depuis plus de 24 h'
                elif not getattr(self, 'alerts_enabled', True):
                    drop = 'alertes coupées via toggle'
                if drop:
                    logger.error(f"Mail en attente '{item['type']}' abandonné ({drop})")
                    self._queue.pop(0)
                    self._save_queue()
                    continue
            try:
                self._deliver(item)
            except Exception as e:
                with self._queue_lock:
                    item['tries'] = item.get('tries', 0) + 1
                    self._save_queue()
                logger.warning(f"Mail '{item['type']}' toujours en attente (essai {item['tries']}) : {e}")
                return False
            with self._queue_lock:
                if self._queue and self._queue[0] is item:
                    self._queue.pop(0)
                self._save_queue()
            self._fire(item)

    def _save_queue(self):
        try:
            data = [{k: v for k, v in it.items() if not k.startswith('_')} for it in self._queue]
            if not data:
                if os.path.exists(self._queue_file):
                    os.remove(self._queue_file)
                return
            tmp = self._queue_file + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._queue_file)
        except OSError as e:
            logger.debug(f"File des mails non enregistrée : {e}")

    def _load_queue(self):
        try:
            with open(self._queue_file) as f:
                data = json.load(f)
            self._queue = [it for it in data if isinstance(it, dict) and 'type' in it and 'ts' in it]
        except (OSError, ValueError):
            self._queue = []
        if self._queue:
            logger.warning(f"{len(self._queue)} mail(s) en attente repris au démarrage")
            self._start_retry()

    def pending(self):
        """Mails en attente (pour l'affichage) : [{type, ts, tries}]."""
        with self._queue_lock:
            return [{'type': it['type'], 'ts': it['ts'], 'tries': it.get('tries', 0)} for it in self._queue]

    def send_recovery_alert(self):
        """Envoie une alerte de rétablissement du signal (même file d'attente que les alertes)."""
        return self.send_alert("Signal FM rétabli", "Le signal FM a été rétabli avec succès.", skip_cooldown=True)
