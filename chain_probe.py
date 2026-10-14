#!/usr/bin/env python3
"""
Supervision de la chaîne de diffusion (BL-FMO)

Sonde périodiquement chaque appareil de la chaîne (codecs, faisceau,
processeur, émetteur, switchs, serveur de streaming...) et garde un état
par appareil, un historique de latence et un journal des changements d'état.

Types de sondes :
  ping      ICMP via la commande `ping` (latence)
  tcp       ouverture d'un port TCP (latence)
  http      requête GET, code attendu, mot-clé optionnel, auth Basic optionnelle
  snmp      lecture d'OIDs via `snmpget` (paquet Debian `snmp`), seuils min/max
  icecast   lecture de status-json.xsl, présence d'un point de montage, auditeurs
  fm_local  état de la réception FM de ce BL-FMO (signal, modulation, RDS)

Les appareils sont stockés dans la base SQLite de l'application
(tables chain_devices et chain_events). Aucune dépendance Python nouvelle :
requests est déjà dans requirements.txt, ping et snmpget sont des binaires.
"""
import json
import logging
import re
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta

import requests

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

PROBE_TYPES = ('ping', 'tcp', 'http', 'snmp', 'icecast', 'fm_local')
STABLE_STATES = ('unknown', 'up', 'warn', 'down')
SECRET_MASK = '********'
HISTORY_LEN = 120          # points de latence gardés en mémoire par appareil
EVENTS_RETENTION_DAYS = 90
DOWN_EMAIL_MIN_INTERVAL = 300  # secondes entre deux emails "injoignable" pour un même appareil

_HOST_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,251}[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$')
_OID_RE = re.compile(r'^\.?\d+(\.\d+)+$')
_COMMUNITY_RE = re.compile(r'^[\x21-\x7e]{1,64}$')


# ══════════════════════════════════════════════════════════════════════════
#  Validation
# ══════════════════════════════════════════════════════════════════════════

def _num(value, name, default=None, lo=None, hi=None, integer=False, allow_none=False):
    if value in (None, ''):
        if allow_none:
            return None
        if default is not None:
            return default
        raise ValueError(f"{name} : valeur requise")
    try:
        v = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} : nombre invalide")
    if lo is not None and v < lo:
        raise ValueError(f"{name} : minimum {lo}")
    if hi is not None and v > hi:
        raise ValueError(f"{name} : maximum {hi}")
    return v


def _host(value):
    h = str(value or '').strip()
    if not h or not _HOST_RE.match(h):
        raise ValueError("Adresse : IP ou nom d'hôte invalide")
    return h.strip('[]')


def _url(value):
    u = str(value or '').strip()
    if not re.match(r'^https?://[^\s]+$', u):
        raise ValueError("URL : doit commencer par http:// ou https://")
    return u


def validate_device(data, existing=None):
    """Nettoie et valide une définition d'appareil. Lève ValueError (message en français)."""
    if not isinstance(data, dict):
        raise ValueError("Données invalides")

    name = str(data.get('name', '')).strip()
    if not name:
        raise ValueError("Nom : requis")
    probe = data.get('probe')
    if probe not in PROBE_TYPES:
        raise ValueError("Type de sonde inconnu")

    dev = {
        'name': name[:60],
        'site': str(data.get('site', '')).strip()[:40] or 'Studio',
        'role': str(data.get('role', '')).strip()[:40],
        'probe': probe,
        'interval': _num(data.get('interval'), 'Intervalle', 30, 5, 3600, integer=True),
        'timeout': _num(data.get('timeout'), 'Délai', 3, 1, 30),
        'fail_threshold': _num(data.get('fail_threshold'), "Seuil d'échecs", 3, 1, 20, integer=True),
        'alert_email': bool(data.get('alert_email', False)),
        'enabled': bool(data.get('enabled', True)),
        'notes': str(data.get('notes', '')).strip()[:300],
    }

    raw = data.get('config') or {}
    old = (existing or {}).get('config') or {}
    cfg = {}

    if probe == 'ping':
        cfg['host'] = _host(raw.get('host'))
        cfg['warn_ms'] = _num(raw.get('warn_ms'), 'Latence max', None, 1, 10000, allow_none=True)

    elif probe == 'tcp':
        cfg['host'] = _host(raw.get('host'))
        cfg['port'] = _num(raw.get('port'), 'Port', None, 1, 65535, integer=True)
        cfg['warn_ms'] = _num(raw.get('warn_ms'), 'Latence max', None, 1, 10000, allow_none=True)

    elif probe == 'http':
        cfg['url'] = _url(raw.get('url'))
        cfg['expect_status'] = str(raw.get('expect_status') or '<400').strip()[:20]
        if not re.match(r'^(<\s*\d{3}|\d{3}(\s*,\s*\d{3})*)$', cfg['expect_status']):
            raise ValueError("Code attendu : ex. 200, 200,401 ou <400")
        cfg['keyword'] = str(raw.get('keyword') or '').strip()[:100]
        cfg['verify_tls'] = bool(raw.get('verify_tls', False))
        cfg['username'] = str(raw.get('username') or '').strip()[:64]
        pw = raw.get('password') or ''
        cfg['password'] = old.get('password', '') if pw == SECRET_MASK else str(pw)[:128]
        cfg['warn_ms'] = _num(raw.get('warn_ms'), 'Latence max', None, 1, 30000, allow_none=True)

    elif probe == 'snmp':
        cfg['host'] = _host(raw.get('host'))
        cfg['port'] = _num(raw.get('port'), 'Port SNMP', 161, 1, 65535, integer=True)
        cfg['version'] = raw.get('version') if raw.get('version') in ('1', '2c') else '2c'
        community = str(raw.get('community') or 'public').strip()
        if community == SECRET_MASK:
            community = old.get('community', 'public')
        if not _COMMUNITY_RE.match(community):
            raise ValueError("Communauté SNMP invalide")
        cfg['community'] = community
        metrics = []
        for i, m in enumerate(raw.get('metrics') or []):
            oid = str(m.get('oid', '')).strip()
            if not oid:
                continue
            if not _OID_RE.match(oid):
                raise ValueError(f"OID invalide : {oid}")
            fmt = m.get('format') if m.get('format') in ('number', 'uptime', 'text') else 'number'
            metrics.append({
                'label': (str(m.get('label', '')).strip() or oid)[:40],
                'oid': oid if oid.startswith('.') else '.' + oid,
                'format': fmt,
                'scale': _num(m.get('scale'), f'Échelle ({oid})', 1, allow_none=False) if fmt == 'number' else 1,
                'unit': str(m.get('unit', '')).strip()[:12],
                'min': _num(m.get('min'), f'Min ({oid})', allow_none=True) if fmt == 'number' else None,
                'max': _num(m.get('max'), f'Max ({oid})', allow_none=True) if fmt == 'number' else None,
            })
            if len(metrics) >= 12:
                break
        if not metrics:
            metrics = [{'label': 'Uptime', 'oid': '.1.3.6.1.2.1.1.3.0', 'format': 'uptime',
                        'scale': 1, 'unit': '', 'min': None, 'max': None}]
        cfg['metrics'] = metrics

    elif probe == 'icecast':
        cfg['url'] = _url(raw.get('url'))
        cfg['mount'] = str(raw.get('mount') or '').strip()[:100]
        if cfg['mount'] and not cfg['mount'].startswith('/'):
            cfg['mount'] = '/' + cfg['mount']
        cfg['min_listeners'] = _num(raw.get('min_listeners'), 'Auditeurs min', None, 0, 100000,
                                    integer=True, allow_none=True)
        cfg['verify_tls'] = bool(raw.get('verify_tls', False))

    elif probe == 'fm_local':
        pass

    dev['config'] = cfg
    return dev


def public_device(dev):
    """Copie d'un appareil sans secrets (mot de passe HTTP masqué)."""
    d = json.loads(json.dumps(dev))
    cfg = d.get('config') or {}
    if cfg.get('password'):
        cfg['password'] = SECRET_MASK
    return d


# ══════════════════════════════════════════════════════════════════════════
#  Sondes
# ══════════════════════════════════════════════════════════════════════════

def _result(ok, message='', latency=None, metrics=None, warn=False):
    return {
        'ok': bool(ok),
        'warn': bool(warn),
        'latency_ms': None if latency is None else round(latency, 1),
        'message': message,
        'metrics': metrics or [],
    }


def _latency_warn(cfg, latency):
    w = cfg.get('warn_ms')
    return bool(w and latency is not None and latency > w)


def probe_ping(cfg, timeout):
    if not shutil.which('ping'):
        return _result(False, "Commande ping absente (sudo apt install iputils-ping)")
    host = cfg['host']
    cmd = ['ping', '-n', '-c', '1', '-W', str(max(1, int(round(timeout)))), host]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return _result(False, 'Pas de réponse')
    m = re.search(r'time[=<]([\d.]+)\s*ms', p.stdout)
    if p.returncode == 0 and m:
        lat = float(m.group(1))
        warn = _latency_warn(cfg, lat)
        return _result(True, 'Latence élevée' if warn else 'Répond au ping', lat, warn=warn)
    err = (p.stderr or '').strip().splitlines()
    if err and ('permitted' in err[-1].lower() or 'permission' in err[-1].lower()):
        return _result(False, "ping non autorisé pour ce service (utiliser une sonde TCP)")
    return _result(False, 'Pas de réponse au ping')


def probe_tcp(cfg, timeout):
    t0 = time.perf_counter()
    try:
        with socket.create_connection((cfg['host'], cfg['port']), timeout=timeout):
            lat = (time.perf_counter() - t0) * 1000
    except socket.timeout:
        return _result(False, f"Port {cfg['port']} : délai dépassé")
    except ConnectionRefusedError:
        return _result(False, f"Port {cfg['port']} : connexion refusée")
    except OSError as e:
        return _result(False, f"Port {cfg['port']} : {e.strerror or e}")
    warn = _latency_warn(cfg, lat)
    return _result(True, f"Port {cfg['port']} ouvert", lat, warn=warn)


def _status_matches(expect, code):
    expect = expect.replace(' ', '')
    if expect.startswith('<'):
        return code < int(expect[1:])
    return str(code) in expect.split(',')


def probe_http(cfg, timeout):
    auth = (cfg['username'], cfg.get('password', '')) if cfg.get('username') else None
    t0 = time.perf_counter()
    try:
        r = requests.get(cfg['url'], timeout=timeout, auth=auth, verify=cfg.get('verify_tls', False),
                         allow_redirects=True, headers={'User-Agent': 'BL-FMO chain probe'})
    except requests.exceptions.SSLError:
        return _result(False, 'Erreur certificat TLS (décocher la vérification ?)')
    except requests.exceptions.Timeout:
        return _result(False, 'Délai dépassé')
    except requests.exceptions.ConnectionError:
        return _result(False, 'Connexion impossible')
    except requests.exceptions.RequestException as e:
        return _result(False, f'Erreur HTTP : {e.__class__.__name__}')
    lat = (time.perf_counter() - t0) * 1000
    metrics = [{'label': 'Code HTTP', 'display': str(r.status_code), 'value': r.status_code, 'state': None}]
    if not _status_matches(cfg.get('expect_status', '<400'), r.status_code):
        return _result(False, f'Code HTTP {r.status_code} inattendu', lat, metrics)
    kw = cfg.get('keyword')
    if kw and kw.lower() not in r.text.lower():
        return _result(True, f'Mot-clé « {kw} » absent de la page', lat, metrics, warn=True)
    warn = _latency_warn(cfg, lat)
    return _result(True, 'Latence élevée' if warn else f'HTTP {r.status_code}', lat, metrics, warn=warn)


def _fmt_uptime(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f'{d} j {h} h'
    if h:
        return f'{h} h {m:02d}'
    return f'{m} min'


def _fmt_number(v):
    if abs(v) >= 100 or float(v).is_integer():
        return f'{v:.0f}'
    if abs(v) >= 10:
        return f'{v:.1f}'
    return f'{v:.2f}'


def probe_snmp(cfg, timeout):
    if not shutil.which('snmpget'):
        return _result(False, "Commande snmpget absente (sudo apt install snmp)")
    metrics_cfg = cfg['metrics']
    target = f"{cfg['host']}:{cfg.get('port', 161)}"
    cmd = ['snmpget', '-v', cfg.get('version', '2c'), '-c', cfg['community'],
           '-t', str(max(1, int(round(timeout)))), '-r', '0', '-OqvteU', '-m', '',
           target] + [m['oid'] for m in metrics_cfg]
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
    except subprocess.TimeoutExpired:
        return _result(False, 'Pas de réponse SNMP')
    lat = (time.perf_counter() - t0) * 1000
    if p.returncode != 0:
        err = (p.stderr or '').strip().splitlines()
        msg = err[-1] if err else 'Erreur SNMP'
        if 'Timeout' in msg:
            msg = 'Pas de réponse SNMP (communauté ou SNMP désactivé ?)'
        return _result(False, msg[:160])

    lines = p.stdout.rstrip('\n').split('\n')
    metrics, warn, missing = [], False, 0
    for i, m in enumerate(metrics_cfg):
        raw = lines[i].strip() if i < len(lines) else ''
        item = {'label': m['label'], 'unit': m.get('unit', ''), 'value': None, 'display': '?', 'state': None}
        if not raw or raw.startswith('No Such') or raw.startswith('No more'):
            item['display'] = 'OID absent'
            item['state'] = 'warn'
            missing += 1
        elif m['format'] == 'text':
            item['value'] = raw.strip('"')
            item['display'] = item['value'][:60]
        else:
            num = re.search(r'-?\d+(\.\d+)?', raw)
            if not num:
                item['display'] = raw.strip('"')[:40]
            elif m['format'] == 'uptime':
                secs = float(num.group(0)) / 100.0  # timeticks
                item['value'] = secs
                item['display'] = _fmt_uptime(secs)
            else:
                v = float(num.group(0)) * float(m.get('scale') or 1)
                item['value'] = v
                item['display'] = _fmt_number(v)
                lo, hi = m.get('min'), m.get('max')
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    item['state'] = 'warn'
                    warn = True
                elif lo is not None or hi is not None:
                    item['state'] = 'ok'
        metrics.append(item)

    if missing == len(metrics_cfg):
        return _result(True, 'Répond en SNMP, mais aucun OID trouvé', lat, metrics, warn=True)
    if missing:
        warn = True
    msg = 'Valeur hors seuil' if warn else 'SNMP OK'
    return _result(True, msg, lat, metrics, warn=warn)


def probe_icecast(cfg, timeout):
    url = cfg['url']
    if not url.rstrip('/').endswith('.xsl'):
        url = url.rstrip('/') + '/status-json.xsl'
    t0 = time.perf_counter()
    try:
        r = requests.get(url, timeout=timeout, verify=cfg.get('verify_tls', False))
        lat = (time.perf_counter() - t0) * 1000
        data = r.json()
    except requests.exceptions.RequestException:
        return _result(False, 'Serveur Icecast injoignable')
    except ValueError:
        return _result(False, 'Réponse Icecast illisible (status-json.xsl ?)')

    stats = data.get('icestats', {}) if isinstance(data, dict) else {}
    sources = stats.get('source') or []
    if isinstance(sources, dict):
        sources = [sources]
    mount = cfg.get('mount')
    if mount:
        found = [s for s in sources if str(s.get('listenurl', '')).rstrip('/').endswith(mount)]
        if not found:
            return _result(False, f'Point de montage {mount} absent (source coupée ?)', lat)
        src = found[0]
    else:
        if not sources:
            return _result(False, 'Aucune source active', lat)
        src = sources[0]

    listeners = int(src.get('listeners') or 0)
    metrics = [{'label': 'Auditeurs', 'value': listeners, 'display': str(listeners), 'unit': '', 'state': None}]
    if src.get('bitrate') or src.get('audio_bitrate'):
        br = src.get('bitrate') or src.get('audio_bitrate')
        try:
            br = int(br)
            br = br // 1000 if br > 1000 else br
        except (TypeError, ValueError):
            pass
        metrics.append({'label': 'Débit', 'value': br, 'display': str(br), 'unit': 'kb/s', 'state': None})
    if not mount and len(sources) > 1:
        metrics.append({'label': 'Sources', 'value': len(sources), 'display': str(len(sources)), 'unit': '', 'state': None})
    warn = cfg.get('min_listeners') is not None and listeners < cfg['min_listeners']
    if warn:
        metrics[0]['state'] = 'warn'
    return _result(True, 'Moins d\'auditeurs que prévu' if warn else 'Flux en ligne', lat, metrics, warn=warn)


def probe_fm_local(fm):
    if fm is None:
        return _result(False, 'Moniteur FM arrêté')
    try:
        s = fm.get_stats()
    except Exception as e:
        return _result(False, f'Moniteur FM indisponible : {e}')
    metrics = []

    def _flag(label, val, good='OK', bad='Absent'):
        metrics.append({'label': label, 'value': bool(val), 'display': good if val else bad,
                        'unit': '', 'state': 'ok' if val else 'warn'})

    _flag('Signal', s.get('signal_ok'))
    _flag('Modulation', s.get('modulation_ok'))
    if s.get('rds_ever_received') or s.get('rds_ok'):
        ps = (s.get('ps') or '').strip()
        _flag('RDS', s.get('rds_ok'), good=ps if ps and ps != '-' else 'OK')
    if s.get('stereo_present') is not None:
        _flag('Stéréo', s.get('stereo_present'), bad='Mono')
    lvl = s.get('current_level')
    if isinstance(lvl, (int, float)) and lvl > -100:
        metrics.append({'label': 'Niveau', 'value': lvl, 'display': f'{lvl:.1f}', 'unit': 'dBFS', 'state': None})
    if isinstance(s.get('signal_dbf'), (int, float)):
        metrics.append({'label': 'Champ', 'value': s['signal_dbf'], 'display': f"{s['signal_dbf']:.1f}",
                        'unit': 'dBf', 'state': None})

    if not s.get('signal_ok'):
        return _result(False, 'Pas de signal FM', None, metrics)
    warn = not s.get('modulation_ok') or (s.get('rds_ever_received') and not s.get('rds_ok'))
    msg = 'Réception dégradée' if warn else 'Réception FM correcte'
    return _result(True, msg, None, metrics, warn=warn)


def run_probe(dev, fm_getter=None):
    """Exécute la sonde d'un appareil (dict validé). Ne lève jamais."""
    probe, cfg, timeout = dev['probe'], dev.get('config') or {}, float(dev.get('timeout') or 3)
    try:
        if probe == 'ping':
            return probe_ping(cfg, timeout)
        if probe == 'tcp':
            return probe_tcp(cfg, timeout)
        if probe == 'http':
            return probe_http(cfg, timeout)
        if probe == 'snmp':
            return probe_snmp(cfg, timeout)
        if probe == 'icecast':
            return probe_icecast(cfg, timeout)
        if probe == 'fm_local':
            return probe_fm_local(fm_getter() if fm_getter else None)
        return _result(False, 'Type de sonde inconnu')
    except Exception as e:  # garde-fou : une sonde ne doit jamais tuer le thread
        logger.exception(f"Sonde {dev.get('name')} : erreur inattendue")
        return _result(False, f'Erreur interne : {e.__class__.__name__}')


def tools_available():
    return {'ping': bool(shutil.which('ping')), 'snmpget': bool(shutil.which('snmpget'))}


# ══════════════════════════════════════════════════════════════════════════
#  Moniteur de chaîne
# ══════════════════════════════════════════════════════════════════════════

class ChainMonitor:
    def __init__(self, db_path='fm_monitor.db', fm_getter=None, alert_getter=None, max_workers=6):
        self.db_path = db_path
        self.fm_getter = fm_getter
        self.alert_getter = alert_getter
        self._lock = threading.RLock()
        self._devices = {}     # id -> dict
        self._runtime = {}     # id -> état courant
        self._inflight = set()
        self._running = False
        self._thread = None
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='chain-probe')
        self._init_db()
        self._load()

    # ── Base de données ──────────────────────────────────────────────────
    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=5000')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS chain_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position INTEGER NOT NULL DEFAULT 0,
                data TEXT NOT NULL,
                last_state TEXT NOT NULL DEFAULT 'unknown',
                last_change TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS chain_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device_id INTEGER,
                device_name TEXT,
                from_state TEXT,
                to_state TEXT,
                message TEXT
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_chain_events_ts ON chain_events(timestamp)')

    def _load(self):
        with self._conn() as c:
            rows = c.execute('SELECT * FROM chain_devices ORDER BY position, id').fetchall()
        with self._lock:
            self._devices.clear()
            for r in rows:
                try:
                    dev = json.loads(r['data'])
                except ValueError:
                    continue
                dev['id'] = r['id']
                dev['position'] = r['position']
                self._devices[r['id']] = dev
                rt = self._runtime.get(r['id'])
                if rt is None:
                    self._runtime[r['id']] = self._new_runtime(r['last_state'], r['last_change'])
            for did in list(self._runtime):
                if did not in self._devices:
                    del self._runtime[did]

    @staticmethod
    def _new_runtime(state='unknown', last_change=None):
        return {
            'state': state if state in STABLE_STATES else 'unknown',
            'fails': 0,
            'last_check': None,
            'last_ok': None,
            'last_change': last_change,
            'next_due': 0.0,
            'last_email': 0.0,
            'result': None,
            'history': deque(maxlen=HISTORY_LEN),
        }

    # ── CRUD ─────────────────────────────────────────────────────────────
    def list_devices(self):
        with self._lock:
            devs = sorted(self._devices.values(), key=lambda d: (d['position'], d['id']))
            return [public_device(d) for d in devs]

    def get_device(self, device_id):
        with self._lock:
            d = self._devices.get(device_id)
            return json.loads(json.dumps(d)) if d else None

    def create_device(self, data):
        dev = validate_device(data)
        with self._lock:
            pos = max([d['position'] for d in self._devices.values()] or [-1]) + 1
        with self._conn() as c:
            cur = c.execute('INSERT INTO chain_devices (position, data) VALUES (?, ?)',
                            (pos, json.dumps(dev, ensure_ascii=False)))
            new_id = cur.lastrowid
        self._load()
        self.trigger(new_id)
        return new_id

    def update_device(self, device_id, data):
        existing = self.get_device(device_id)
        if not existing:
            raise KeyError(device_id)
        dev = validate_device(data, existing)
        with self._conn() as c:
            c.execute('UPDATE chain_devices SET data=? WHERE id=?',
                      (json.dumps(dev, ensure_ascii=False), device_id))
        with self._lock:
            rt = self._runtime.get(device_id)
            if rt:
                rt['fails'] = 0
                if not dev['enabled']:
                    rt['result'] = None
        self._load()
        self.trigger(device_id)

    def delete_device(self, device_id):
        with self._conn() as c:
            c.execute('DELETE FROM chain_devices WHERE id=?', (device_id,))
        self._load()

    def reorder(self, ids):
        ids = [int(i) for i in ids]
        with self._conn() as c:
            for pos, did in enumerate(ids):
                c.execute('UPDATE chain_devices SET position=? WHERE id=?', (pos, did))
        self._load()

    # ── Sondage ──────────────────────────────────────────────────────────
    def test(self, data):
        """Teste une définition (non enregistrée) et renvoie le résultat brut."""
        existing = None
        if data.get('id'):
            existing = self.get_device(int(data['id']))
        dev = validate_device(data, existing)
        return run_probe(dev, self.fm_getter)

    def trigger(self, device_id):
        with self._lock:
            rt = self._runtime.get(device_id)
            if rt:
                rt['next_due'] = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name='chain-monitor', daemon=True)
        self._thread.start()
        logger.info(f"Supervision de la chaîne démarrée ({len(self._devices)} appareils)")

    def stop(self):
        self._running = False

    def _loop(self):
        last_cleanup = 0
        while self._running:
            now = time.time()
            due = []
            with self._lock:
                for did, dev in self._devices.items():
                    rt = self._runtime[did]
                    if not dev.get('enabled', True) or did in self._inflight:
                        continue
                    if now >= rt['next_due']:
                        rt['next_due'] = now + dev.get('interval', 30)
                        self._inflight.add(did)
                        due.append(json.loads(json.dumps(dev)))
            for dev in due:
                self._pool.submit(self._probe_and_record, dev)
            if now - last_cleanup > 3600:
                last_cleanup = now
                self._cleanup_events()
            time.sleep(1)

    def _probe_and_record(self, dev):
        try:
            res = run_probe(dev, self.fm_getter)
            self._record(dev, res)
        finally:
            with self._lock:
                self._inflight.discard(dev['id'])

    def _record(self, dev, res):
        did = dev['id']
        now = datetime.now()
        event = None
        with self._lock:
            rt = self._runtime.get(did)
            if rt is None or did not in self._devices:
                return
            rt['last_check'] = now.isoformat(timespec='seconds')
            rt['result'] = res
            rt['history'].append([int(time.time() * 1000), res['latency_ms'] if res['ok'] else None, res['ok']])
            prev = rt['state']
            if res['ok']:
                rt['fails'] = 0
                rt['last_ok'] = rt['last_check']
                new = 'warn' if res['warn'] else 'up'
            else:
                rt['fails'] += 1
                new = 'down' if rt['fails'] >= dev.get('fail_threshold', 3) else prev
            if new != prev:
                rt['state'] = new
                rt['last_change'] = rt['last_check']
                event = (prev, new, res['message'])

        if event:
            prev, new, msg = event
            self._log_event(dev, prev, new, msg, now)
            self._maybe_email(dev, prev, new, msg)

    def _log_event(self, dev, prev, new, msg, now):
        try:
            with self._conn() as c:
                c.execute('INSERT INTO chain_events (timestamp, device_id, device_name, from_state, to_state, message) '
                          'VALUES (?,?,?,?,?,?)',
                          (now.isoformat(timespec='seconds'), dev['id'], dev['name'], prev, new, msg))
                c.execute('UPDATE chain_devices SET last_state=?, last_change=? WHERE id=?',
                          (new, now.isoformat(timespec='seconds'), dev['id']))
        except Exception as e:
            logger.error(f"Chaîne : écriture événement impossible : {e}")
        level = logging.WARNING if new == 'down' else logging.INFO
        logger.log(level, f"Chaîne : {dev['name']} {prev} → {new} ({msg})")

    def _maybe_email(self, dev, prev, new, msg):
        if not dev.get('alert_email') or not self.alert_getter:
            return
        alert = None
        try:
            alert = self.alert_getter()
        except Exception:
            pass
        # send_alert(skip_cooldown=True) ne vérifie pas email.enabled : on le fait ici
        if alert is None or not (getattr(alert, 'config', None) or {}).get('enabled'):
            return
        where = f"{dev.get('site', '')} / {dev.get('role', '')}".strip(' /')
        target = (dev.get('config') or {}).get('host') or (dev.get('config') or {}).get('url') or ''
        details = f"Appareil : {dev['name']}\nEmplacement : {where}\nAdresse : {target}\nDétail : {msg}"
        with self._lock:
            rt = self._runtime.get(dev['id'])
        try:
            if new == 'down':
                if rt and time.time() - rt['last_email'] < DOWN_EMAIL_MIN_INTERVAL:
                    return
                if rt:
                    rt['last_email'] = time.time()
                threading.Thread(target=alert.send_alert,
                                 args=(f"Chaîne : {dev['name']} injoignable", details),
                                 kwargs={'skip_cooldown': True}, daemon=True).start()
            elif prev == 'down' and new in ('up', 'warn'):
                threading.Thread(target=alert.send_alert,
                                 args=(f"Chaîne : {dev['name']} rétabli", details),
                                 kwargs={'skip_cooldown': True}, daemon=True).start()
        except Exception as e:
            logger.error(f"Chaîne : envoi email impossible : {e}")

    def _cleanup_events(self):
        limit = (datetime.now() - timedelta(days=EVENTS_RETENTION_DAYS)).isoformat(timespec='seconds')
        try:
            with self._conn() as c:
                c.execute('DELETE FROM chain_events WHERE timestamp < ?', (limit,))
        except Exception as e:
            logger.error(f"Chaîne : nettoyage du journal impossible : {e}")

    # ── Lecture ──────────────────────────────────────────────────────────
    def get_status(self):
        out = []
        with self._lock:
            devs = sorted(self._devices.values(), key=lambda d: (d['position'], d['id']))
            for d in devs:
                rt = self._runtime.get(d['id']) or self._new_runtime()
                res = rt['result'] or {}
                cfg = d.get('config') or {}
                if not d.get('enabled', True):
                    display = 'disabled'
                elif rt['state'] != 'down' and rt['fails'] > 0:
                    display = 'unstable'
                else:
                    display = rt['state']
                out.append({
                    'id': d['id'],
                    'name': d['name'],
                    'site': d.get('site', ''),
                    'role': d.get('role', ''),
                    'probe': d['probe'],
                    'target': cfg.get('host') or cfg.get('url') or '',
                    'enabled': d.get('enabled', True),
                    'alert_email': d.get('alert_email', False),
                    'state': display,
                    'fails': rt['fails'],
                    'fail_threshold': d.get('fail_threshold', 3),
                    'interval': d.get('interval', 30),
                    'last_check': rt['last_check'],
                    'last_ok': rt['last_ok'],
                    'last_change': rt['last_change'],
                    'latency_ms': res.get('latency_ms'),
                    'message': res.get('message', ''),
                    'metrics': res.get('metrics', []),
                    'history': list(rt['history']),
                })
        return out

    def get_events(self, limit=100, device_id=None):
        q = 'SELECT * FROM chain_events'
        args = []
        if device_id:
            q += ' WHERE device_id=?'
            args.append(device_id)
        q += ' ORDER BY id DESC LIMIT ?'
        args.append(max(1, min(int(limit), 1000)))
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]
