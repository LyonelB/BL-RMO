#!/usr/bin/env python3
"""
BL-RMO - Réception DAB+ (reprise de BL-DMO, anciennement dabplus-monitor)

Deux usages :
  1. Processus de surveillance autonome (service systemd bl-rmo-dab) :
         python3 dab_monitor.py
     Lance welle-cli (fork welle-monitor) sur le canal configuré, interroge
     son API HTTP (/mux.json) toutes les 2 s, mesure l'ensemble (SNR, qualité
     FIC, gain) et chaque service (présence, niveaux audio, DLS, débit),
     déclenche les alertes (mail + base) et publie l'état dans dab_state.json.
     Indépendant de Flask : un plantage de welle-cli ou du clé USB ne touche
     ni l'interface, ni la réception FM.

  2. Module importé par app.py (DabReader) : état courant, événements,
     disponibilité 24 h des services.

Aucun matériel n'est requis pour l'interface : sans clé RTL-SDR, le service
indique « récepteur absent » et réessaie toutes les 5 min.
"""
import json
import logging
import math
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

import requests

logger = logging.getLogger('dab')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
DB_FILE = os.path.join(BASE_DIR, 'fm_monitor.db')
STATE_FILE = os.path.join(BASE_DIR, 'dab_state.json')
WELLE_LOG = os.path.join(BASE_DIR, 'logs', 'welle-cli.log')

DEFAULTS = {
    'enabled': False,
    'channel': '9A',
    'frequency_mhz': 202.928,
    'ensemble_name': '',          # libellé attendu (affichage seulement)
    'welle_cli': 'welle-cli',     # chemin du binaire (fork welle-monitor conseillé)
    'port': 7979,
    'gain': -1,                   # -1 = AGC de welle-cli ; sinon indice dans la table de gains de la clé
                                  # (15 = 28 dB) : un gain fixe synchronise bien plus vite
    'carousel_size': 2,           # 0 = décoder tous les services (-D), N = carrousel (-C N, N >= 2)
    'poll_interval': 2,
    'snr_threshold': 5.0,         # dB
    'fic_quality_threshold': 80,  # %
    'ensemble_loss_duration': 60,     # s avant alerte ensemble dégradé
    'service_loss_duration': 60,      # s avant alerte service absent
    'silence_threshold': -60.0,       # dBFS
    'silence_duration': 30,           # s avant alerte silence
    'receiver_loss_duration': 300,    # s avant alerte récepteur hors service
    'startup_grace': 180,             # s sans alerte service après (re)démarrage de welle-cli
    'watched_sids': [],               # vide = tous les services
    'usb_reset_cmd': '',              # vide : script installé par 67-dab.sh s'il existe
    'sync_timeout': 300,              # s sans ensemble décodé (FIC) avant de relancer welle-cli
                                      # (au démarrage, la synchro prend déjà 2 à 3 min sur la clé V4)
    'listen_max_minutes': 15,         # écoute depuis l'interface (protection de la clé)
    # Mails par type d'alerte (toutes restent enregistrées dans les journaux)
    'email_kinds': {'receiver': True, 'ensemble': True, 'absent': True, 'silence': True},
}

# Canaux de la bande III (kHz)
BAND_III = {
    '5A': 174928, '5B': 176640, '5C': 178352, '5D': 180064,
    '6A': 181936, '6B': 183648, '6C': 185360, '6D': 187072,
    '7A': 188928, '7B': 190640, '7C': 192352, '7D': 194064,
    '8A': 195936, '8B': 197648, '8C': 199360, '8D': 201072,
    '9A': 202928, '9B': 204640, '9C': 206352, '9D': 208064,
    '10A': 209936, '10B': 211648, '10C': 213360, '10D': 215072,
    '11A': 216928, '11B': 218640, '11C': 220352, '11D': 222064,
    '12A': 223936, '12B': 225648, '12C': 227360, '12D': 229072,
    '13A': 230784, '13B': 232496, '13C': 234208, '13D': 235776,
    '13E': 237488, '13F': 239200,
}

GHOST_LABELS = ('', '.', '..')
STATE_STALE_S = 15            # état plus vieux : service DAB+ arrêté


def load_dab_config(config_file=CONFIG_FILE):
    cfg = dict(DEFAULTS)
    try:
        with open(config_file) as f:
            cfg.update(json.load(f).get('dab', {}) or {})
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Lecture config DAB+ impossible ({e}), valeurs par défaut")
    cfg['watched_sids'] = [str(s).lower() for s in (cfg.get('watched_sids') or [])]
    if str(cfg.get('channel')).upper() in BAND_III:
        cfg['channel'] = str(cfg['channel']).upper()
        cfg['frequency_mhz'] = BAND_III[cfg['channel']] / 1000
    return cfg


def raw_to_dbfs(raw):
    """Niveau welle-cli (0..32768) vers dBFS."""
    try:
        raw = int(raw)
    except (TypeError, ValueError):
        return -100.0
    if raw <= 0:
        return -100.0
    return round(20.0 * math.log10(min(raw, 32768) / 32768.0), 1)


def is_real_service(label):
    return (label or '').strip() not in GHOST_LABELS and len((label or '').strip()) > 2


def sid_decimal(sid):
    """'0xfa41' -> '64065' (welle-cli sert /slide et /mp3 en décimal ou hexa selon les versions)."""
    try:
        return str(int(str(sid), 16)) if str(sid).lower().startswith('0x') else str(int(sid))
    except ValueError:
        return str(sid)


def parse_mux(data):
    """Extrait de /mux.json ce dont la surveillance a besoin (fonction pure, testable)."""
    demod = data.get('demodulator', {}) or {}
    ens = data.get('ensemble', {}) or {}
    out = {
        'snr': float(demod.get('snr', 0) or 0),
        'freq_corr': float(demod.get('frequencycorrection', 0) or 0),
        'fic_errors': int((demod.get('fic', {}) or {}).get('numcrcerrors', 0) or 0),
        'gain': float(((data.get('receiver', {}) or {}).get('hardware', {}) or {}).get('gain', 0) or 0),
        'hardware': ((data.get('receiver', {}) or {}).get('hardware', {}) or {}).get('name', ''),
        'ensemble_label': ((ens.get('label', {}) or {}).get('label', '') or '').strip(),
        'ensemble_id': str(ens.get('id', '') or ''),
        'carousel_sid': str(data.get('current_carousel_sid') or '').lower(),
        'services': [],
    }
    for s in data.get('services', []) or []:
        sid = str(s.get('sid', '') or '').lower()
        if not sid:
            continue
        comps = s.get('components', []) or []
        prim = next((c for c in comps if c.get('primary')), comps[0] if comps else {}) or {}
        sub = prim.get('subchannel', {}) or {}
        audio = s.get('audiolevel', {}) or {}
        err = s.get('errorcounters', {}) or {}
        out['services'].append({
            'sid': sid,
            'label': ((s.get('label', {}) or {}).get('label', '') or '').strip(),
            'dls': ((s.get('dls', {}) or {}).get('label', '') or '').strip(),
            'pty': s.get('ptystring', '') or '',
            'mode': prim.get('ascty', '') or 'DAB+',
            'bitrate': int(sub.get('bitrate', 0) or 0),
            'protection': sub.get('protection', '') or '',
            'language': sub.get('languagestring', '') or '',
            'samplerate': int(s.get('samplerate', 0) or 0),
            'channels': int(s.get('channels', 2) or 2),
            'raw_audio': (audio.get('left'), audio.get('right'), audio.get('time')),
            'audio_l': raw_to_dbfs(audio.get('left', 0)),
            'audio_r': raw_to_dbfs(audio.get('right', 0)),
            'err_aac': int(err.get('aacerrors', 0) or 0),
            'err_frame': int(err.get('frameerrors', 0) or 0),
            'err_rs': int(err.get('rserrors', 0) or 0),
            'mot_time': int((s.get('mot', {}) or {}).get('time', 0) or 0),
        })
    return out


# ════════════════════════════════════════════════════════════════════
# Base de données (tables dab_* dans fm_monitor.db)
# ════════════════════════════════════════════════════════════════════

SCHEMA = '''
CREATE TABLE IF NOT EXISTS dab_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,          -- heure locale ISO, début réel du défaut pour 'lost'
    kind TEXT NOT NULL,               -- receiver | ensemble | absent | silence
    sid TEXT DEFAULT '',
    label TEXT DEFAULT '',
    state TEXT NOT NULL,              -- lost | restored
    channel TEXT DEFAULT '',
    message TEXT DEFAULT '',
    email_sent INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dab_events_ts ON dab_events(timestamp);
CREATE TABLE IF NOT EXISTS dab_uptime (
    channel TEXT NOT NULL,
    sid TEXT NOT NULL,
    slot TEXT NOT NULL,               -- début du bloc de 30 min, heure locale
    checks INTEGER NOT NULL,
    present INTEGER NOT NULL,
    PRIMARY KEY (channel, sid, slot)
);
CREATE TABLE IF NOT EXISTS dab_services (
    channel TEXT NOT NULL,
    sid TEXT NOT NULL,
    label TEXT,
    last_seen TEXT,
    PRIMARY KEY (channel, sid)
);
'''

KIND_LABELS = {
    'receiver': 'Récepteur DAB+ hors service',
    'ensemble': 'Ensemble dégradé',
    'absent': 'Service absent',
    'silence': 'Silence audio',
}


def _connect(db_path):
    c = sqlite3.connect(db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db(db_path=DB_FILE):
    with _connect(db_path) as c:
        c.executescript(SCHEMA)


# ════════════════════════════════════════════════════════════════════
# Surveillance (processus bl-rmo-dab)
# ════════════════════════════════════════════════════════════════════

class _Svc:
    def __init__(self, sid, label):
        self.sid, self.label = sid, label
        self.info = {}
        self.present = False
        self.lost_start = None
        self.lost_alerted = False
        self.silence_start = None
        self.silence_alerted = False
        self.last_raw = None
        self.last_fresh = 0.0
        self.silent = False


class DabMonitor:
    def __init__(self, config_file=CONFIG_FILE, db_path=DB_FILE, state_file=STATE_FILE, email=True):
        self.config_file = config_file
        self.db_path = db_path
        self.state_file = state_file
        self.cfg = load_dab_config(config_file)
        self._cfg_mtime = self._mtime(config_file)
        self.running = False
        self.proc = None
        self.proc_started = 0.0
        self.backoff = 5
        self.welle_up = False
        self.welle_since = None
        self.receiver_down_since = time.time()
        self.receiver_alerted = False
        self.last_error = ''
        self.rf = {}
        self.fic_prev = None
        self.rf_window = []          # (t, snr, fic, gain), moyenne glissante 15 s
        self.ens_bad_since = None
        self.ens_alerted = False
        self.services = {}
        self.slot = None
        self.slot_stats = {}
        self.lock = threading.Lock()
        self._force_reset = False
        self._nosync_since = None
        self._nosync_restarts = 0
        self._synced_once = False
        self.email = None
        if email:
            try:
                from email_alert import EmailAlert
                self.email = EmailAlert(config_file)
            except Exception as e:
                logger.warning(f"Mail indisponible ({e}) : alertes en base seulement")
        init_db(db_path)

    @staticmethod
    def _mtime(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0

    @property
    def url(self):
        return f"http://127.0.0.1:{int(self.cfg['port'])}"

    def reload_if_changed(self):
        m = self._mtime(self.config_file)
        if m == self._cfg_mtime:
            return
        self._cfg_mtime = m
        old = self.cfg
        self.cfg = load_dab_config(self.config_file)
        if self.email:
            try:
                self.email.reload_config(self.config_file)
            except Exception:
                pass
        keys = ('channel', 'welle_cli', 'port', 'gain', 'carousel_size')
        if any(old.get(k) != self.cfg.get(k) for k in keys):
            logger.info("Réglages du récepteur modifiés : relance de welle-cli")
            if old.get('channel') != self.cfg.get('channel'):
                with self.lock:
                    self.services.clear()
            self._kill_welle()

    # ── welle-cli ────────────────────────────────────────────────────────
    def _cmd(self):
        c = self.cfg
        cmd = [c['welle_cli'], '-c', str(c['channel']), '-g', str(int(c.get('gain', -1)))]
        n = int(c.get('carousel_size', 0) or 0)
        # Avec -C 1, welle-cli perd la liste des services (tous indiqués
        # absents) ; le carrousel commence donc à 2 services.
        if n == 1:
            n = 2
        cmd += ['-C', str(n)] if n > 0 else ['-D']
        cmd += ['-w', str(int(c['port']))]
        return cmd

    def _start_welle(self):
        os.makedirs(os.path.dirname(WELLE_LOG), exist_ok=True)
        try:
            if os.path.getsize(WELLE_LOG) > 5 * 1024 * 1024:
                os.replace(WELLE_LOG, WELLE_LOG + '.1')
        except OSError:
            pass
        reset = self.cfg.get('usb_reset_cmd') or (
            'sudo -n /usr/local/bin/rtlsdr-usb-reset.sh' if os.path.exists('/usr/local/bin/rtlsdr-usb-reset.sh') else '')
        if reset and (self.backoff > 5 or self._force_reset):
            logger.info("Réinitialisation USB de la clé RTL-SDR")
            try:
                subprocess.run(reset, shell=True, timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                logger.warning("Réinitialisation USB : délai dépassé")
            time.sleep(5)
        self._force_reset = False
        cmd = self._cmd()
        logger.info("Lancement : " + ' '.join(cmd))
        log = open(WELLE_LOG, 'a')
        try:
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
            self.proc_started = time.time()
        except FileNotFoundError:
            self.proc = None
            self.last_error = f"{cmd[0]} introuvable (installer welle-monitor)"
            logger.error(self.last_error)
        finally:
            log.close()

    def _kill_welle(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
        self._set_welle_up(False)

    def _check_sync(self, now=None):
        """welle-cli tourne et la clé reçoit (SNR), mais aucun ensemble n'est
        décodé : cas vu après un redémarrage (FIC 0 %, EId 0x0000, aucun
        service). welle-cli ne s'en sort pas seul : on le relance, avec
        réinitialisation USB de la clé, puis à intervalles croissants."""
        now = now or time.time()
        if not (self.welle_up and self.proc):
            self._nosync_since = None
            return
        with self.lock:
            any_present = any(s.present for s in self.services.values())
        eid = str(self.rf.get('ensemble_id') or '').lower()
        synced = any_present or (eid not in ('', '0', '0x0', '0x0000') and self.rf.get('fic_quality', 0) > 0)
        if synced:
            self._nosync_since = None
            self._nosync_restarts = 0
            if not self._synced_once:
                self._synced_once = True
                logger.info(f"DAB+ : ensemble décodé {int(now - (self.proc_started or now))} s après le lancement "
                            f"(gain {'automatique' if int(self.cfg.get('gain', -1)) < 0 else 'fixe, indice ' + str(self.cfg.get('gain'))})")
            return
        self._nosync_since = self._nosync_since or now
        wait = min(float(self.cfg.get('sync_timeout', 300)) * (2 ** self._nosync_restarts), 1800)
        if now - self._nosync_since >= wait and now - self.proc_started >= wait:
            self._nosync_restarts += 1
            self.last_error = f"aucun ensemble décodé depuis {int(now - self._nosync_since)} s"
            logger.warning(f"DAB+ : {self.last_error}, relance de welle-cli avec réinitialisation USB "
                           f"(tentative {self._nosync_restarts})")
            self._force_reset = True
            self._nosync_since = None
            self._kill_welle()

    def _supervise(self):
        """Relance welle-cli s'il s'est arrêté, avec un délai croissant (5 s à 5 min)."""
        if self.proc and self.proc.poll() is None:
            return
        if self.proc is not None:
            lived = time.time() - self.proc_started
            code = self.proc.returncode
            self.proc = None
            self._set_welle_up(False)
            self.last_error = f"welle-cli arrêté (code {code}) après {int(lived)} s"
            logger.warning(self.last_error)
            self.backoff = 5 if lived > 120 else min(self.backoff * 2, 300)
            self._next_start = time.time() + self.backoff
            return
        if time.time() >= getattr(self, '_next_start', 0):
            self._start_welle()
            if self.proc is None:
                self.backoff = 300
                self._next_start = time.time() + self.backoff

    def _set_welle_up(self, up):
        now = time.time()
        if up and not self.welle_up:
            self.welle_up, self.welle_since = True, now
            self._synced_once = False
            self.backoff = 5
            self.last_error = ''
            self.fic_prev = None
            self.rf_window.clear()
            self.receiver_down_since = None
            if self.receiver_alerted:
                self._event('receiver', 'restored', message='welle-cli répond de nouveau.')
                self.receiver_alerted = False
        elif not up and self.welle_up:
            self.welle_up, self.welle_since = False, None
            self.receiver_down_since = now
            with self.lock:
                for s in self.services.values():
                    s.present = False
        elif not up and self.receiver_down_since is None:
            self.receiver_down_since = now

    # ── Événements et alertes ───────────────────────────────────────────
    def _event(self, kind, state, sid='', label='', message='', started=None):
        name = KIND_LABELS[kind] + (f" : {label}" if label else '')
        title = f"DAB+ · {name}" + (" rétabli" if state == 'restored' else '')
        details = (f"{message}\nCanal : {self.cfg['channel']} ({self.cfg['frequency_mhz']} MHz)"
                   + (f"\nService : {label} ({sid})" if sid else ''))
        sent = False
        kinds = self.cfg.get('email_kinds') or {}
        if self.email and kinds.get(kind, True):
            try:
                sent = bool(self.email.send_alert(title, details, skip_cooldown=True))
            except Exception as e:
                logger.warning(f"Mail DAB+ non envoyé : {e}")
        ts = datetime.fromtimestamp(started or time.time()).isoformat(timespec='seconds')
        try:
            with _connect(self.db_path) as c:
                c.execute('INSERT INTO dab_events (timestamp, kind, sid, label, state, channel, message, email_sent) '
                          'VALUES (?,?,?,?,?,?,?,?)',
                          (ts, kind, sid, label, state, str(self.cfg['channel']), message, int(sent)))
        except sqlite3.Error as e:
            logger.error(f"Enregistrement événement DAB+ : {e}")
        (logger.warning if state == 'lost' else logger.info)(title)

    def _watched(self, sid):
        w = self.cfg.get('watched_sids') or []
        return not w or sid in w

    def check_alerts(self, now=None):
        now = now or time.time()
        c = self.cfg
        # Récepteur (welle-cli ou clé absents)
        if not self.welle_up:
            down = self.receiver_down_since or now
            if not self.receiver_alerted and now - down >= float(c['receiver_loss_duration']):
                self._event('receiver', 'lost', started=down,
                            message=self.last_error or 'welle-cli ne répond pas (clé RTL-SDR débranchée ?)')
                self.receiver_alerted = True
            return
        up_for = now - (self.welle_since or now)
        # Ensemble. Au démarrage, welle-cli met 2 à 3 min à se synchroniser :
        # pas d'alerte avant la première synchro, sauf si elle n'arrive jamais
        # (sync_timeout), et le défaut compte alors depuis le lancement.
        if self._synced_once or up_for >= float(c.get('sync_timeout', 300)):
            if not self._synced_once and self.ens_bad_since is None:
                self.ens_bad_since = self.welle_since or now
            ok = (self.rf.get('snr', 0) >= float(c['snr_threshold'])
                  and self.rf.get('fic_quality', 0) >= float(c['fic_quality_threshold']))
            if not ok:
                if self.ens_bad_since is None:
                    self.ens_bad_since = now
                elif not self.ens_alerted and now - self.ens_bad_since >= float(c['ensemble_loss_duration']):
                    self._event('ensemble', 'lost', started=self.ens_bad_since,
                                message=f"SNR {self.rf.get('snr', 0):.1f} dB (seuil {c['snr_threshold']}), "
                                        f"FIC {self.rf.get('fic_quality', 0):.0f} % (seuil {c['fic_quality_threshold']})")
                    self.ens_alerted = True
            else:
                if self.ens_alerted:
                    self._event('ensemble', 'restored',
                                message=f"SNR {self.rf.get('snr', 0):.1f} dB, FIC {self.rf.get('fic_quality', 0):.0f} %")
                self.ens_bad_since, self.ens_alerted = None, False
        # Services
        grace = up_for < float(c['startup_grace'])
        with self.lock:
            svcs = list(self.services.values())
        for s in svcs:
            if not is_real_service(s.label):
                continue
            watched = self._watched(s.sid)
            if s.present:
                if s.lost_alerted:
                    self._event('absent', 'restored', s.sid, s.label, 'Le service est de nouveau dans le bouquet.')
                s.lost_start, s.lost_alerted = None, False
            elif s.lost_start and watched and not grace and not s.lost_alerted \
                    and now - s.lost_start >= float(c['service_loss_duration']):
                self._event('absent', 'lost', s.sid, s.label, 'Le service n\'est plus dans le bouquet.',
                            started=s.lost_start)
                s.lost_alerted = True
            if s.silence_start and s.present and watched and not grace and not s.silence_alerted \
                    and now - s.silence_start >= float(c['silence_duration']):
                lvl = max(s.info.get('audio_l', -100), s.info.get('audio_r', -100))
                self._event('silence', 'lost', s.sid, s.label,
                            f"Niveau {lvl:.1f} dBFS (seuil {c['silence_threshold']} dBFS)", started=s.silence_start)
                s.silence_alerted = True

    def _silence_ended(self, s):
        if s.silence_alerted:
            self._event('silence', 'restored', s.sid, s.label, 'Niveau audio normal.')
        s.silence_start, s.silence_alerted = None, False

    # ── Relevé ──────────────────────────────────────────────────────────
    def ingest(self, data, now=None):
        """Intègre une réponse /mux.json (appelé à chaque relevé, testable sans welle-cli)."""
        now = now or time.time()
        m = parse_mux(data)
        # Qualité FIC : erreurs CRC apparues depuis le relevé précédent (compteur cumulatif)
        delta = 0 if self.fic_prev is None or m['fic_errors'] < self.fic_prev else m['fic_errors'] - self.fic_prev
        self.fic_prev = m['fic_errors']
        fic_q = max(0.0, 100.0 - min(delta * 2.0, 100.0))
        self.rf_window.append((now, m['snr'], fic_q, m['gain']))
        self.rf_window = [w for w in self.rf_window if w[0] >= now - 15]
        n = len(self.rf_window)
        self.rf = {
            'snr': round(sum(w[1] for w in self.rf_window) / n, 1),
            'fic_quality': round(sum(w[2] for w in self.rf_window) / n, 1),
            'gain': round(sum(w[3] for w in self.rf_window) / n, 1),
            'snr_now': m['snr'], 'freq_corr': m['freq_corr'], 'fic_errors': m['fic_errors'],
            'hardware': m['hardware'], 'ensemble_label': m['ensemble_label'],
            'ensemble_id': m['ensemble_id'], 'carousel_sid': m['carousel_sid'],
        }
        thr = float(self.cfg['silence_threshold'])
        seen = set()
        new, ended = [], []
        with self.lock:
            for d in m['services']:
                sid = d['sid']
                seen.add(sid)
                s = self.services.get(sid)
                if s is None:
                    s = self.services[sid] = _Svc(sid, d['label'])
                    new.append((sid, d['label']))
                if is_real_service(d['label']):
                    s.label = d['label']
                s.info = d
                s.present = True
                # Un niveau n'est pris en compte que s'il vient d'être mis à jour :
                # en mode carrousel, seuls les services en cours de décodage sont frais.
                fresh = d['raw_audio'] != s.last_raw or sid == m['carousel_sid']
                s.last_raw = d['raw_audio']
                if fresh:
                    s.last_fresh = now
                    s.silent = max(d['audio_l'], d['audio_r']) <= thr
                    if s.silent:
                        s.silence_start = s.silence_start or now
                    elif s.silence_start:
                        ended.append(s)
            for sid, s in self.services.items():
                if sid not in seen and s.present:
                    s.present = False
                    s.lost_start = now
        for s in ended:
            self._silence_ended(s)
        if new:
            self._save_services(new)
        self._account(now)

    def _save_services(self, items):
        ts = datetime.now().isoformat(timespec='seconds')
        try:
            with _connect(self.db_path) as c:
                for sid, label in items:
                    c.execute('INSERT INTO dab_services (channel, sid, label, last_seen) VALUES (?,?,?,?) '
                              'ON CONFLICT(channel, sid) DO UPDATE SET label=excluded.label, last_seen=excluded.last_seen',
                              (str(self.cfg['channel']), sid, label, ts))
        except sqlite3.Error as e:
            logger.debug(f"dab_services : {e}")

    def _account(self, now):
        """Disponibilité par blocs de 30 min (un relevé = une vérification)."""
        dt = datetime.fromtimestamp(now)
        slot = dt.replace(minute=30 if dt.minute >= 30 else 0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M')
        if self.slot and slot != self.slot:
            self.flush_slot()
        self.slot = slot
        with self.lock:
            for sid, s in self.services.items():
                if not is_real_service(s.label):
                    continue
                st = self.slot_stats.setdefault(sid, [0, 0])
                st[0] += 1
                st[1] += 1 if s.present else 0

    def flush_slot(self):
        if not self.slot or not self.slot_stats:
            return
        stats, self.slot_stats = self.slot_stats, {}
        try:
            with _connect(self.db_path) as c:
                for sid, (checks, present) in stats.items():
                    c.execute('INSERT INTO dab_uptime (channel, sid, slot, checks, present) VALUES (?,?,?,?,?) '
                              'ON CONFLICT(channel, sid, slot) DO UPDATE SET checks=checks+excluded.checks, '
                              'present=present+excluded.present',
                              (str(self.cfg['channel']), sid, self.slot, checks, present))
                c.execute("DELETE FROM dab_uptime WHERE slot < ?",
                          ((datetime.now() - timedelta(days=31)).strftime('%Y-%m-%d %H:%M'),))
                c.execute("DELETE FROM dab_events WHERE timestamp < ?",
                          ((datetime.now() - timedelta(days=90)).isoformat(timespec='seconds'),))
        except sqlite3.Error as e:
            logger.error(f"dab_uptime : {e}")

    def poll_once(self):
        try:
            r = requests.get(self.url + '/mux.json', timeout=(3, 3), headers={'Connection': 'close'})
            if r.status_code == 200:
                self._fails = 0
                self._set_welle_up(True)
                self.ingest(r.json())
                return True
        except (requests.RequestException, ValueError):
            pass
        self._fails = getattr(self, '_fails', 0) + 1
        if self._fails >= 3 and self.welle_up:
            # /mux.json peut rester bloqué sur un verrou interne alors que welle-cli décode :
            # le fork welle-monitor expose /status, qui ne se bloque jamais.
            try:
                sr = requests.get(self.url + '/status', timeout=(2, 2))
                if sr.status_code == 200 and sr.json().get('alive', False):
                    if self._fails < 30:
                        return False
            except (requests.RequestException, ValueError):
                pass
            self._set_welle_up(False)
            if self.proc and time.time() - self.proc_started > 60:
                logger.warning("welle-cli ne répond plus : relance")
                self._kill_welle()
        return False

    # ── Publication ─────────────────────────────────────────────────────
    def snapshot(self):
        c = self.cfg
        with self.lock:
            svcs = []
            for s in self.services.values():
                if not is_real_service(s.label):
                    continue
                d = {k: v for k, v in s.info.items() if k != 'raw_audio'}
                d.update({
                    'sid': s.sid, 'sid_dec': sid_decimal(s.sid), 'label': s.label, 'present': s.present,
                    'watched': self._watched(s.sid), 'silence': bool(s.silence_start),
                    'silence_alert': s.silence_alerted, 'lost_alert': s.lost_alerted,
                    'level_age': round(time.time() - s.last_fresh, 1) if s.last_fresh else None,
                })
                svcs.append(d)
        svcs.sort(key=lambda d: d['label'].lower())
        rf = dict(self.rf) if self.welle_up else {}
        ens_ok = bool(rf) and rf.get('snr', 0) >= float(c['snr_threshold']) \
            and rf.get('fic_quality', 0) >= float(c['fic_quality_threshold'])
        return {
            'updated': time.time(),
            'channel': c['channel'], 'frequency_mhz': c['frequency_mhz'],
            'ensemble_name': c.get('ensemble_name', ''),
            'receiver_up': self.welle_up,
            'receiver_since': self.welle_since,
            'receiver_error': self.last_error,
            'mode': 'carrousel' if int(c.get('carousel_size', 0) or 0) > 0 else 'complet',
            'rf': rf,
            'ensemble_ok': ens_ok,
            'ensemble_alert': self.ens_alerted,
            'thresholds': {'snr': c['snr_threshold'], 'fic': c['fic_quality_threshold'],
                           'silence': c['silence_threshold']},
            'watched_sids': c.get('watched_sids') or [],
            'services': svcs,
        }

    def publish(self):
        data = json.dumps(self.snapshot(), ensure_ascii=False)
        d = os.path.dirname(self.state_file) or '.'
        fd, tmp = tempfile.mkstemp(dir=d, prefix='.dab_state.')
        with os.fdopen(fd, 'w') as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.state_file)

    # ── Boucle principale ───────────────────────────────────────────────
    def run(self):
        self.running = True
        logger.info(f"Surveillance DAB+ : canal {self.cfg['channel']} ({self.cfg['frequency_mhz']} MHz)")
        while self.running:
            t0 = time.time()
            try:
                self.reload_if_changed()
                if not self.cfg.get('enabled'):
                    self._kill_welle()
                    self.publish()
                    time.sleep(5)
                    continue
                self._supervise()
                if self.proc:
                    self.poll_once()
                    self._check_sync()
                else:
                    self._set_welle_up(False)
                self.check_alerts()
                self.publish()
            except Exception as e:
                logger.exception(f"Boucle DAB+ : {e}")
            time.sleep(max(0.2, float(self.cfg['poll_interval']) - (time.time() - t0)))
        self.flush_slot()
        self._kill_welle()

    def stop(self, *_):
        self.running = False


# ════════════════════════════════════════════════════════════════════
# Lecture (utilisée par Flask)
# ════════════════════════════════════════════════════════════════════

RTL_USB_IDS = {('0bda', '2838'), ('0bda', '2832')}


def prerequisites(cfg):
    """Ce qui manque pour recevoir le DAB+ : welle-cli, clé RTL-SDR, pilote TNT écarté."""
    import shutil
    exe = cfg.get('welle_cli') or 'welle-cli'
    welle = shutil.which(exe) or (exe if os.path.isabs(exe) and os.access(exe, os.X_OK) else None)
    dongle = None
    base = '/sys/bus/usb/devices'
    try:
        for d in os.listdir(base):
            try:
                with open(os.path.join(base, d, 'idVendor')) as f:
                    v = f.read().strip()
                with open(os.path.join(base, d, 'idProduct')) as f:
                    pr = f.read().strip()
            except OSError:
                continue
            if (v, pr) in RTL_USB_IDS:
                try:
                    with open(os.path.join(base, d, 'product')) as f:
                        dongle = f.read().strip() or f'{v}:{pr}'
                except OSError:
                    dongle = f'{v}:{pr}'
                break
    except OSError:
        pass
    return {
        'welle_cli': welle,
        'dongle': dongle,
        'dvb_driver_loaded': os.path.isdir('/sys/module/dvb_usb_rtl28xxu'),
    }


class DabReader:
    def __init__(self, config_file=CONFIG_FILE, db_path=DB_FILE, state_file=STATE_FILE):
        self.config_file, self.db_path, self.state_file = config_file, db_path, state_file
        try:
            init_db(db_path)
        except sqlite3.Error:
            pass

    def config(self):
        return load_dab_config(self.config_file)

    def status(self):
        cfg = self.config()
        try:
            with open(self.state_file) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        age = time.time() - st.get('updated', 0) if st else None
        st['enabled'] = bool(cfg.get('enabled'))
        st['prereq'] = prerequisites(cfg)
        st['service_running'] = age is not None and age < STATE_STALE_S
        st.setdefault('channel', cfg['channel'])
        st.setdefault('frequency_mhz', cfg['frequency_mhz'])
        st.setdefault('services', [])
        st.setdefault('rf', {})
        if not st['service_running']:
            st['receiver_up'] = False
            st['ensemble_ok'] = False
            st['rf'] = {}
            for s in st['services']:
                s['present'] = None
        return st

    def events(self, limit=50):
        """Événements regroupés : un défaut et son rétablissement sur une ligne."""
        with _connect(self.db_path) as c:
            rows = [dict(r) for r in c.execute(
                'SELECT * FROM dab_events ORDER BY timestamp, id').fetchall()[-limit * 4:]]
        open_, out = {}, []
        for r in rows:
            key = (r['kind'], r['sid'])
            if r['state'] == 'lost':
                item = {'kind': r['kind'], 'label': KIND_LABELS.get(r['kind'], r['kind']),
                        'service': r['label'], 'sid': r['sid'], 'channel': r['channel'],
                        'start': r['timestamp'], 'end': None, 'duration': None,
                        'message': r['message'], 'emails': r['email_sent'], 'status': 'ongoing'}
                open_[key] = item
                out.append(item)
            elif key in open_:
                item = open_.pop(key)
                item['end'] = r['timestamp']
                item['status'] = 'complete'
                item['emails'] += r['email_sent']
                try:
                    item['duration'] = int((datetime.fromisoformat(r['timestamp'])
                                            - datetime.fromisoformat(item['start'])).total_seconds())
                except ValueError:
                    pass
        out.reverse()
        return out[:limit]

    def uptime(self, hours=24):
        cfg = self.config()
        since = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M')
        with _connect(self.db_path) as c:
            labels = {r['sid']: r['label'] for r in c.execute(
                'SELECT sid, label FROM dab_services WHERE channel=?', (str(cfg['channel']),))}
            rows = c.execute('SELECT sid, slot, checks, present FROM dab_uptime WHERE channel=? AND slot>=? '
                             'ORDER BY slot', (str(cfg['channel']), since)).fetchall()
        res = {}
        for r in rows:
            d = res.setdefault(r['sid'], {'sid': r['sid'], 'label': labels.get(r['sid'], r['sid']),
                                          'checks': 0, 'present': 0, 'blocks': []})
            d['checks'] += r['checks']
            d['present'] += r['present']
            d['blocks'].append({'slot': r['slot'], 'pct': round(100 * r['present'] / r['checks'], 1) if r['checks'] else None})
        for d in res.values():
            d['uptime_pct'] = round(100 * d['present'] / d['checks'], 2) if d['checks'] else None
        return sorted(res.values(), key=lambda d: (d['label'] or '').lower())


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                        stream=sys.stdout)
    os.chdir(BASE_DIR)
    mon = DabMonitor()
    signal.signal(signal.SIGTERM, mon.stop)
    signal.signal(signal.SIGINT, mon.stop)
    mon.run()


if __name__ == '__main__':
    main()
