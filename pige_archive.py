#!/usr/bin/env python3
"""
pige_archive.py : extraits audio des 10 dernières alertes.

À chaque incident terminé, BL-RMO découpe dans la pige l'extrait qui va de
30 s avant le début à 30 s après le rétablissement, et le range dans
pige/archives/. Seuls les extraits des 10 derniers incidents sont gardés :
au onzième, le plus ancien est supprimé. La pige complète garde sa durée.

Un incident regroupe :
  - les alertes FM de BL-RMO (signal, modulation, RDS...), rétablies ;
  - les alertes des appareils de la chaîne (perte audio d'un codec, entrée AES
    du processeur...), revenues en service. Les sondes réseau sont ignorées
    (trop fréquentes, et sans effet audible le plus souvent).
Des alertes qui se chevauchent ou se suivent à moins d'une minute forment un
seul extrait : un blanc à l'antenne donne un fichier, pas cinq.

Réglages (config.json, section « pige », facultatifs) :
    "archive_keep": 10, "archive_max_minutes": 20
"""
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger('pige_archive')

PAD_S = 30              # marge avant le début et après la fin
MERGE_GAP_S = 60        # deux alertes à moins d'une minute : un seul extrait
SETTLE_S = 20           # attendre que la pige ait écrit la fin de l'extrait
LOOKBACK_H = 23         # au-delà, la pige complète peut ne plus avoir le passage (pige de 24 h)

FM_LOSS = {'signal_lost': 'signal perdu', 'no_modulation': 'absence de modulation',
           'rds_lost': 'RDS absent', 'rt_lost': 'RadioText absent'}
FM_RESTORE = {'signal_restored': 'signal_lost', 'modulation_restored': 'no_modulation',
              'rds_restored': 'rds_lost', 'rt_restored': 'rt_lost'}


class PigeArchiver:
    def __init__(self, pige_store, db_path='fm_monitor.db', cfg=None):
        self.pige = pige_store
        self.db_path = db_path
        cfg = cfg or {}
        self.keep = max(1, int(cfg.get('archive_keep', 10)))
        self.max_s = int(cfg.get('archive_max_minutes', 20)) * 60
        self.dir = os.path.join(self.pige.dir, 'archives')
        os.makedirs(self.dir, exist_ok=True)
        self._stop = threading.Event()
        self._failed = set()         # incidents impossibles à extraire : ne pas réessayer
        self._init_db()

    # ── Base ─────────────────────────────────────────────────────────────
    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA busy_timeout=5000')
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_db(self):
        with self._conn() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS pige_archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start REAL NOT NULL,          -- début de l'incident (epoch)
                end REAL NOT NULL,            -- fin de l'incident (epoch)
                clip_start REAL, clip_end REAL,
                label TEXT,                   -- résumé lisible
                causes TEXT,                  -- JSON : liste des alertes regroupées
                file TEXT, size INTEGER,
                created TEXT
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_pige_archives_start ON pige_archives(start)')

    # ── Incidents ────────────────────────────────────────────────────────
    @staticmethod
    def _epoch(s):
        try:
            return datetime.fromisoformat(str(s).replace('Z', '')).timestamp()
        except ValueError:
            return None

    def _fm_alerts(self, since):
        out = []
        with self._conn() as c:
            try:
                rows = c.execute('SELECT timestamp, alert_type, duration_seconds, message FROM alerts '
                                 'WHERE timestamp >= ? ORDER BY timestamp, id',
                                 (datetime.fromtimestamp(since - 3600).strftime('%Y-%m-%d %H:%M:%S'),)).fetchall()
            except sqlite3.Error:
                return out
        open_ = {}
        for r in rows:
            t = self._epoch(r['timestamp'])
            if t is None:
                continue
            kind = r['alert_type']
            if kind in FM_LOSS:
                # L'alerte est enregistrée à l'envoi du mail, après le délai de
                # confirmation : le défaut a commencé `duration_seconds` plus tôt.
                open_.setdefault(kind, t - (r['duration_seconds'] or 0))
            elif kind in FM_RESTORE and FM_RESTORE[kind] in open_:
                lost = FM_RESTORE[kind]
                t0 = open_.pop(lost)
                if 'automatiquement' in (r['message'] or ''):
                    continue             # clôture forcée au redémarrage du service : pas un vrai rétablissement
                out.append((t0, t, f"Réception FM : {FM_LOSS[lost]}"))
        return out

    def _chain_alerts(self, since):
        out = []
        with self._conn() as c:
            try:
                devs = {r['id']: json.loads(r['data']) for r in c.execute('SELECT id, data FROM chain_devices')}
                rows = c.execute("SELECT timestamp, device_id, device_name, to_state, message FROM chain_events "
                                 "WHERE timestamp >= ? AND (source IS NULL OR source = '') ORDER BY timestamp, id",
                                 (datetime.fromtimestamp(since - 3600).isoformat(timespec='seconds'),)).fetchall()
            except (sqlite3.Error, ValueError):
                return out
        open_ = {}
        for r in rows:
            d = devs.get(r['device_id']) or {}
            # Appareils seulement : ni sondes réseau, ni capteurs de site, ni réception FM (déjà couverte par les alertes FM)
            if d.get('kind') in ('probe', 'sensor') or d.get('probe') in ('burst', 'fm_local') or d.get('site') == 'Sondes réseau':
                continue
            t = self._epoch(r['timestamp'])
            if t is None:
                continue
            did = r['device_id']
            if r['to_state'] in ('warn', 'down', 'unstable'):
                if did not in open_:
                    msg = (r['message'] or '').replace('Valeur hors seuil : ', '')
                    open_[did] = (t, f"{r['device_name']} : {msg}" if msg else r['device_name'])
            elif r['to_state'] == 'up' and did in open_:
                t0, label = open_.pop(did)
                out.append((t0, t, label))
        return out

    def incidents(self, since):
        """Incidents terminés depuis `since` : [(début, fin, [causes])], regroupés."""
        items = sorted(self._fm_alerts(since) + self._chain_alerts(since))
        merged = []
        for t0, t1, label in items:
            if t1 < since:
                continue
            if merged and t0 <= merged[-1][1] + MERGE_GAP_S:
                m = merged[-1]
                m[1] = max(m[1], t1)
                if label not in m[2]:
                    m[2].append(label)
            else:
                merged.append([t0, t1, [label]])
        return merged

    # ── Archivage ────────────────────────────────────────────────────────
    def run_once(self):
        now = time.time()
        since = now - LOOKBACK_H * 3600
        with self._conn() as c:
            done = [(r['start'], r['end']) for r in c.execute('SELECT start, end FROM pige_archives WHERE end >= ?', (since - 3600,))]
        for t0, t1, causes in self.incidents(since):
            if t1 + PAD_S + SETTLE_S > now:
                continue                     # la fin de l'extrait n'est pas encore enregistrée
            if (t0, t1) in self._failed or any(a <= t1 and b >= t0 for a, b in done):
                continue                     # déjà archivé (ou chevauche un extrait existant)
            self._archive(t0, t1, causes)
            done.append((t0, t1))
        self.purge()

    def _archive(self, t0, t1, causes):
        c0 = t0 - PAD_S
        c1 = min(t1 + PAD_S, c0 + self.max_s)
        label = ' · '.join(causes)[:300]
        try:
            self.pige.reload()
            tmp = self.pige.export(c0, c1, title=f"Alerte {datetime.fromtimestamp(t0):%d/%m/%Y %H:%M:%S} : {label}")
        except Exception as e:
            self._failed.add((t0, t1))
            logger.warning(f"Extrait d'alerte {datetime.fromtimestamp(t0):%d/%m %H:%M:%S} non créé : {e}")
            return
        name = f"alerte_{datetime.fromtimestamp(t0):%Y%m%d-%H%M%S}.mp3"
        dst = os.path.join(self.dir, name)
        shutil.move(tmp, dst)
        size = os.path.getsize(dst)
        with self._conn() as c:
            c.execute('INSERT INTO pige_archives (start, end, clip_start, clip_end, label, causes, file, size, created) '
                      'VALUES (?,?,?,?,?,?,?,?,?)',
                      (t0, t1, c0, c1, label, json.dumps(causes, ensure_ascii=False), name, size,
                       datetime.now().isoformat(timespec='seconds')))
        logger.info(f"Extrait d'alerte archivé : {name} ({size // 1024} ko) : {label}")

    def purge(self):
        """Ne garde que les `keep` extraits les plus récents.

        La ligne d'un extrait supprimé reste deux jours (file vide) pour que
        l'incident ne soit pas ré-extrait tant qu'il est encore dans la pige."""
        with self._conn() as c:
            old = c.execute('SELECT id, file FROM pige_archives WHERE file IS NOT NULL '
                            'ORDER BY start DESC LIMIT -1 OFFSET ?', (self.keep,)).fetchall()
            for r in old:
                try:
                    os.remove(os.path.join(self.dir, os.path.basename(r['file'])))
                except OSError:
                    pass
                c.execute('UPDATE pige_archives SET file=NULL, size=0 WHERE id=?', (r['id'],))
            c.execute('DELETE FROM pige_archives WHERE file IS NULL AND end < ?', (time.time() - 2 * 86400,))

    # ── Lecture ──────────────────────────────────────────────────────────
    def list(self, limit=500):
        with self._conn() as c:
            rows = [dict(r) for r in c.execute('SELECT * FROM pige_archives WHERE file IS NOT NULL ORDER BY start DESC LIMIT ?', (limit,))]
        for r in rows:
            try:
                r['causes'] = json.loads(r['causes'] or '[]')
            except ValueError:
                r['causes'] = []
        return rows

    def path(self, archive_id):
        with self._conn() as c:
            r = c.execute('SELECT file FROM pige_archives WHERE id=? AND file IS NOT NULL', (archive_id,)).fetchone()
        if not r:
            return None
        p = os.path.join(self.dir, os.path.basename(r['file']))
        return p if os.path.isfile(p) else None

    # ── Boucle ───────────────────────────────────────────────────────────
    def start(self):
        threading.Thread(target=self._loop, name='pige-archive', daemon=True).start()
        logger.info(f"Archivage des extraits d'alertes : {self.keep} derniers incidents")

    def _loop(self):
        time.sleep(30)
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Archivage des extraits d'alertes")
            self._stop.wait(60)
