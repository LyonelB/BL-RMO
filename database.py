#!/usr/bin/env python3
"""
Module de gestion de la base de données SQLite
Enregistre l'historique des niveaux audio, alertes et RDS
"""
import sqlite3
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class FMDatabase:
    def __init__(self, db_path='fm_monitor.db'):
        """Initialise la base de données"""
        self.db_path = db_path
        # Activer WAL mode pour éviter les locks
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=5000')
        conn.close()
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        """Context manager pour les connexions DB"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Erreur base de données: {e}")
            raise
        finally:
            conn.close()
    
    def init_database(self):
        """Crée les tables si elles n'existent pas"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Table des niveaux audio (enregistrement toutes les 5s)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audio_levels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    level_db REAL NOT NULL,
                    signal_ok BOOLEAN NOT NULL
                )
            ''')
            
            # Index pour les requêtes temporelles
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_audio_timestamp 
                ON audio_levels(timestamp)
            ''')
            
            # Table des alertes
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    alert_type TEXT NOT NULL,
                    level_db REAL,
                    duration_seconds INTEGER,
                    message TEXT,
                    email_sent BOOLEAN DEFAULT 0
                )
            ''')
            
            # Table historique RDS (optionnel, pour analyse)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rds_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ps TEXT,
                    rt TEXT
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_rds_history_timestamp
                ON rds_history(timestamp)
            ''')
            
            logger.info("Base de données initialisée")
    
    def save_audio_level(self, level_db, signal_ok):
        """Enregistre un niveau audio"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO audio_levels (level_db, signal_ok)
                    VALUES (?, ?)
                ''', (level_db, signal_ok))
        except Exception as e:
            logger.error(f"Erreur sauvegarde niveau: {e}")
    
    def save_alert(self, alert_type, level_db, duration_seconds, message, email_sent=False):
        """Enregistre une alerte"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO alerts (timestamp, alert_type, level_db, duration_seconds, message, email_sent)
                    VALUES (datetime('now', 'localtime'), ?, ?, ?, ?, ?)
                ''', (alert_type, level_db, duration_seconds, message, email_sent))
                logger.info(f"Alerte enregistrée: {alert_type}")
        except Exception as e:
            logger.error(f"Erreur sauvegarde alerte: {e}")
    
    def save_rds(self, ps, rt):
        """Enregistre les données RDS (optionnel)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO rds_history (ps, rt)
                    VALUES (?, ?)
                ''', (ps, rt))
        except Exception as e:
            logger.error(f"Erreur sauvegarde RDS: {e}")
    
    # ── Historique des RadioText diffusés (RT_HISTORY_PATCH) ──────────────
    RT_HISTORY_KEEP = 1000   # lignes conservées dans rds_history

    def save_rt_title(self, ps, rt):
        """Enregistre un RadioText stable, horodaté en heure locale comme les alertes."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO rds_history (timestamp, ps, rt)
                    VALUES (datetime('now', 'localtime'), ?, ?)
                ''', (ps, rt))
                cursor.execute('''
                    DELETE FROM rds_history WHERE id <= (
                        SELECT id FROM rds_history ORDER BY id DESC LIMIT 1 OFFSET ?)
                ''', (self.RT_HISTORY_KEEP,))
        except Exception as e:
            logger.error(f"Erreur sauvegarde RadioText: {e}")

    def get_rt_history(self, limit=10):
        """Derniers RadioText enregistrés, du plus récent au plus ancien."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT timestamp, ps, rt FROM rds_history
                    WHERE rt IS NOT NULL AND rt != ''
                    ORDER BY id DESC LIMIT ?
                ''', (limit,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Erreur récupération historique RadioText: {e}")
            return []

    def get_audio_history(self, hours=24):
        """Récupère l'historique des niveaux audio"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                since = datetime.now() - timedelta(hours=hours)
                
                cursor.execute('''
                    SELECT timestamp, level_db, signal_ok
                    FROM audio_levels
                    WHERE timestamp >= ?
                    ORDER BY timestamp ASC
                ''', (since,))
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Erreur récupération historique: {e}")
            return []
    
    def get_alerts_history(self, limit=50):
        """Récupère l'historique des alertes"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT timestamp, alert_type, level_db, duration_seconds, message, email_sent
                    FROM alerts
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (limit,))
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Erreur récupération alertes: {e}")
            return []
    
    def get_alerts_history_grouped(self, limit=50):
        """Récupère l'historique des alertes regroupées par paires (perte + retour)"""

        # Correspondance type_perte → type_retour + libellé affiché
        ALERT_PAIRS = {
            'signal_lost':   ('signal_restored',      'Signal perdu'),
            'no_modulation': ('modulation_restored',  'Absence modulation'),
            'rds_lost':      ('rds_restored',         'RDS absent'),
            'rt_lost':       ('rt_restored',          'RadioText absent'),
        }
        RESTORE_TYPES = {v[0]: k for k, v in ALERT_PAIRS.items()}

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute('''
                    SELECT timestamp, alert_type, level_db, duration_seconds, message, email_sent
                    FROM alerts
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (limit * 3,))

                alerts = [dict(row) for row in cursor.fetchall()]

                grouped = []
                i = 0

                while i < len(alerts):
                    alert = alerts[i]
                    atype = alert['alert_type']

                    # --- Cas rétablissement : chercher la perte correspondante ---
                    if atype in RESTORE_TYPES:
                        lost_type = RESTORE_TYPES[atype]
                        label = ALERT_PAIRS[lost_type][1]
                        lost_alert = None

                        if i + 1 < len(alerts) and alerts[i + 1]['alert_type'] == lost_type:
                            lost_alert = alerts[i + 1]
                            i += 2
                        else:
                            grouped.append({
                                'alert_label': label,
                                'start_time': alert['timestamp'],
                                'end_time': alert['timestamp'],
                                'duration': alert['duration_seconds'] or 0,
                                'level_lost': alert['level_db'],
                                'level_restored': alert['level_db'],
                                'emails_sent': 1 if alert['email_sent'] else 0,
                                'status': 'restored_only'
                            })
                            i += 1
                            continue

                        grouped.append({
                            'alert_label': label,
                            'start_time': lost_alert['timestamp'],
                            'end_time': alert['timestamp'],
                            'duration': int((datetime.fromisoformat(alert['timestamp']) - datetime.fromisoformat(lost_alert['timestamp'])).total_seconds()),
                            'level_lost': lost_alert['level_db'],
                            'level_restored': alert['level_db'],
                            'emails_sent': (1 if lost_alert['email_sent'] else 0) + (1 if alert['email_sent'] else 0),
                            'status': 'complete'
                        })

                    # --- Cas perte sans rétablissement ---
                    elif atype in ALERT_PAIRS:
                        label = ALERT_PAIRS[atype][1]
                        grouped.append({
                            'alert_label': label,
                            'start_time': alert['timestamp'],
                            'end_time': None,
                            'duration': alert['duration_seconds'] or 0,
                            'level_lost': alert['level_db'],
                            'level_restored': None,
                            'emails_sent': 1 if alert['email_sent'] else 0,
                            'status': 'ongoing'
                        })
                        i += 1

                    else:
                        i += 1

                return grouped[:limit]

        except Exception as e:
            logger.error(f"Erreur récupération alertes groupées: {e}")
            return []
    

    def close_open_alerts(self):
        """
        Clôture les alertes ouvertes au redémarrage du service.
        Insère un événement _restored pour chaque alerte _lost sans rétablissement.
        """
        ALERT_PAIRS = {
            'signal_lost':   'signal_restored',
            'no_modulation': 'modulation_restored',
            'rds_lost':      'rds_restored',
            'rt_lost':       'rt_restored',
        }
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT timestamp, alert_type, level_db
                    FROM alerts
                    ORDER BY timestamp DESC
                    LIMIT 100
                ''')
                alerts = [dict(row) for row in cursor.fetchall()]

                # Chercher les alertes ouvertes (lost sans restored qui suit)
                closed = 0
                for i, alert in enumerate(alerts):
                    atype = alert['alert_type']
                    if atype not in ALERT_PAIRS:
                        continue
                    restore_type = ALERT_PAIRS[atype]
                    # Vérifier si un restored existe après (= plus tôt dans la liste DESC)
                    already_restored = any(
                        a['alert_type'] == restore_type
                        for a in alerts[:i]
                    )
                    if not already_restored:
                        cursor.execute('''
                            INSERT INTO alerts (timestamp, alert_type, level_db,
                                duration_seconds, message, email_sent)
                            VALUES (datetime('now', 'localtime'), ?, ?, 0, ?, 0)
                        ''', (
                            restore_type,
                            alert['level_db'],
                            f"Clôturé automatiquement au redémarrage du service"
                        ))
                        closed += 1
                        logger.info(f"Alerte {atype} clôturée au redémarrage → {restore_type}")

                if closed:
                    logger.info(f"{closed} alerte(s) ouverte(s) clôturée(s) au redémarrage")
                return closed
        except Exception as e:
            logger.error(f"Erreur close_open_alerts: {e}")
            return 0

    def cleanup_old_data(self, days=7):
        """Nettoie les données de plus de X jours"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cutoff = datetime.now() - timedelta(days=days)
                
                cursor.execute('DELETE FROM audio_levels WHERE timestamp < ?', (cutoff,))
                deleted = cursor.rowcount
                
                logger.info(f"Nettoyage: {deleted} enregistrements supprimés")
                return deleted
        except Exception as e:
            logger.error(f"Erreur nettoyage: {e}")
            return 0

