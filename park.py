#!/usr/bin/env python3
"""
BL-RMO : surveillance du parc (PC de production, NAS, sites web).

Appareils hors de la chaîne de diffusion, relevés toutes les minutes par le
processus Flask (comme la chaîne) :

  - PC Windows en SNMP (HOST-RESOURCES-MIB) : allumé ou éteint, depuis quand,
    processeur (moyenne des cœurs, maximum depuis l'allumage), disques fixes.
    « 24 h/24 » : éteint ou injoignable = alerte, redémarrage signalé.
    Sinon, éteint = simple information.
  - NAS Synology en SNMP (SYNOLOGY-MIB) : système, alimentation, ventilateurs,
    température, disques, volumes, mise à jour DSM, processeur et mémoire.
  - Sites web : réponse HTTP(S), texte attendu, temps de réponse,
    disponibilité sur 24 h.

« Allumé depuis » est noté par BL-RMO lui-même (heure à laquelle le PC recommence
à répondre) : avec le démarrage rapide de Windows, la durée de fonctionnement
annoncée par le PC ne repart pas de zéro après un arrêt.
"""
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta

import requests

from chain_probe import (SECRET_MASK, SNMP3_AUTH, SNMP3_LEVELS, SNMP3_PRIV, _COMMUNITY_RE,
                         _host, _num, _url)

logger = logging.getLogger(__name__)

KINDS = ('pc', 'nas', 'site')
INTERVAL = 60                  # s entre deux relevés
FAILS_DOWN = 2                 # relevés manqués de suite avant « éteint » ou « injoignable »
EVENTS_RETENTION_DAYS = 90
SAMPLES_RETENTION_DAYS = 7     # disponibilité des sites

# HOST-RESOURCES-MIB
HR_UPTIME = '.1.3.6.1.2.1.25.1.1.0'
HR_STORAGE = '.1.3.6.1.2.1.25.2.3.1'          # .2 type, .3 libellé, .4 unité, .5 taille, .6 utilisé
HR_FIXED_DISK = '.1.3.6.1.2.1.25.2.1.4'
HR_RAM = '.1.3.6.1.2.1.25.2.1.2'
HR_CPU_LOAD = '.1.3.6.1.2.1.25.3.3.1.2'
SYS_NAME = '.1.3.6.1.2.1.1.5.0'
# SYNOLOGY-MIB
SYNO = '.1.3.6.1.4.1.6574'
SYNO_DISK = SYNO + '.2.1.1'                   # .2 nom, .3 modèle, .5 état, .6 température, .13 santé
SYNO_RAID = SYNO + '.3.1.1'                   # .2 nom, .3 état, .4 libre, .5 total
UCD_MEM = '.1.3.6.1.4.1.2021.4'               # .5 total, .6 disponible, .14 tampons, .15 cache (ko)

SYNO_DISK_STATUS = {1: 'Normal', 2: 'Initialisé', 3: 'Non initialisé', 4: 'Partition système en panne', 5: 'En panne'}
SYNO_DISK_HEALTH = {1: 'Normal', 2: 'Avertissement', 3: 'Critique', 4: 'Défaillant'}
SYNO_RAID_STATUS = {1: 'Normal', 2: 'Réparation', 3: 'Migration', 4: 'Extension', 5: 'Suppression',
                    6: 'Création', 7: 'Synchronisation', 8: 'Vérification', 9: 'Assemblage',
                    10: 'Annulation', 11: 'Dégradé', 12: 'En panne', 13: 'Vérification', 14: 'Synchronisation',
                    15: 'Synchronisation', 16: 'Réparation', 17: 'Vérification', 18: 'Réparation',
                    19: 'Réparation', 20: 'Réparation', 21: 'Réparation'}
SYNO_UPGRADE = {1: 'disponible', 2: 'à jour', 3: 'vérification…', 4: 'serveur injoignable', 5: '-'}


# ══════════════════════════════════════════════════════════════════════════
#  Validation
# ══════════════════════════════════════════════════════════════════════════

def _snmp_creds(raw, old):
    """Accès SNMP (v1/v2c communauté, v3 utilisateur) : mêmes règles que la chaîne."""
    cfg = {'version': raw.get('version') if raw.get('version') in ('1', '2c', '3') else '2c'}
    if cfg['version'] == '3':
        user = str(raw.get('user') or '').strip()
        if not _COMMUNITY_RE.match(user):
            raise ValueError("SNMPv3 : nom d'utilisateur requis")
        level = raw.get('level') if raw.get('level') in SNMP3_LEVELS else 'authPriv'
        cfg.update({'user': user, 'level': level})
        if level != 'noAuthNoPriv':
            cfg['auth_proto'] = raw.get('auth_proto') if raw.get('auth_proto') in SNMP3_AUTH else 'SHA'
            ap = str(raw.get('auth_pass') or '')
            cfg['auth_pass'] = old.get('auth_pass', '') if ap == SECRET_MASK else ap
            if not 8 <= len(cfg['auth_pass']) <= 64:
                raise ValueError("SNMPv3 : mot de passe d'authentification de 8 à 64 caractères")
        if level == 'authPriv':
            cfg['priv_proto'] = raw.get('priv_proto') if raw.get('priv_proto') in SNMP3_PRIV else 'AES'
            pp = str(raw.get('priv_pass') or '')
            cfg['priv_pass'] = old.get('priv_pass', '') if pp == SECRET_MASK else pp
            if not 8 <= len(cfg['priv_pass']) <= 64:
                raise ValueError("SNMPv3 : mot de passe de chiffrement de 8 à 64 caractères")
    else:
        community = str(raw.get('community') or '').strip()
        if community == SECRET_MASK:
            community = old.get('community', '')
        if not _COMMUNITY_RE.match(community or ''):
            raise ValueError("Communauté SNMP requise")
        cfg['community'] = community
    return cfg


def validate_device(data, existing=None):
    if not isinstance(data, dict):
        raise ValueError('Données invalides')
    kind = data.get('kind')
    if kind not in KINDS:
        raise ValueError("Type d'appareil inconnu")
    name = str(data.get('name', '')).strip()
    if not name:
        raise ValueError('Nom : requis')
    old = (existing or {}).get('config') or {}
    raw = data.get('config') or {}
    dev = {
        'kind': kind, 'name': name[:40],
        'alert_email': bool(data.get('alert_email', True)),
        'enabled': bool(data.get('enabled', True)),
        'notes': str(data.get('notes', '')).strip()[:200],
    }
    cfg = {}
    if kind in ('pc', 'nas'):
        cfg['host'] = _host(raw.get('host'))
        cfg['port'] = _num(raw.get('port'), 'Port SNMP', 161, 1, 65535, integer=True)
        cfg.update(_snmp_creds(raw, old))
        cfg['disk_pct'] = _num(raw.get('disk_pct'), 'Seuil disque', 90, 50, 100, integer=True)
    if kind == 'pc':
        dev['always_on'] = bool(data.get('always_on', False))
        cfg['os'] = str(raw.get('os') or '').strip()[:30]
        cfg['cpu_pct'] = _num(raw.get('cpu_pct'), 'Seuil processeur', 90, 10, 100, integer=True)
        cfg['cpu_minutes'] = _num(raw.get('cpu_minutes'), 'Durée processeur', 10, 1, 240, integer=True)
    elif kind == 'nas':
        cfg['temp_max'] = _num(raw.get('temp_max'), 'Température max', 60, 30, 90, integer=True)
    elif kind == 'site':
        cfg['url'] = _url(raw.get('url'))
        cfg['keyword'] = str(raw.get('keyword') or '').strip()[:100]
        cfg['slow_ms'] = _num(raw.get('slow_ms'), 'Réponse lente', 5000, 200, 60000, integer=True)
    dev['config'] = cfg
    return dev


def public_device(dev):
    d = json.loads(json.dumps(dev))
    for k in ('community', 'auth_pass', 'priv_pass'):
        if (d.get('config') or {}).get(k):
            d['config'][k] = SECRET_MASK
    return d


# ══════════════════════════════════════════════════════════════════════════
#  SNMP
# ══════════════════════════════════════════════════════════════════════════

class SnmpError(Exception):
    pass


def _auth_args(cfg):
    if cfg.get('version') == '3':
        a = ['-v', '3', '-u', cfg['user'], '-l', cfg.get('level', 'authPriv')]
        if cfg.get('level') != 'noAuthNoPriv':
            a += ['-a', cfg.get('auth_proto', 'SHA'), '-A', cfg.get('auth_pass', '')]
        if cfg.get('level') == 'authPriv':
            a += ['-x', cfg.get('priv_proto', 'AES'), '-X', cfg.get('priv_pass', '')]
        return a
    return ['-v', cfg.get('version', '2c'), '-c', cfg['community']]


def _snmp(tool, cfg, oids, timeout=2):
    """Lance snmpget/snmpwalk, renvoie {oid numérique: valeur brute}."""
    if not shutil.which(tool):
        raise SnmpError(f'Commande {tool} absente (sudo apt install snmp)')
    cmd = [tool] + _auth_args(cfg) + ['-t', str(timeout), '-r', '1', '-On', '-OQ', '-Oe', '-Ot', '-m', '', f"{cfg['host']}:{cfg.get('port', 161)}"] + oids
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 2 * max(1, len(oids)) + 10)
    except subprocess.TimeoutExpired:
        raise SnmpError('Pas de réponse SNMP')
    err = (p.stderr or '').strip()
    if p.returncode != 0 and ('Timeout' in err or not p.stdout.strip()):
        low = err.lower()
        if 'authentication failure' in low or 'wrong digest' in low:
            raise SnmpError("SNMPv3 : échec d'authentification")
        if 'unknown user' in low:
            raise SnmpError('SNMPv3 : utilisateur inconnu')
        raise SnmpError('Pas de réponse SNMP')
    out = {}
    for line in p.stdout.splitlines():
        if ' = ' not in line:
            continue
        oid, val = line.split(' = ', 1)
        val = val.strip()
        if val.startswith('No Such') or val.startswith('No more'):
            continue
        out[oid.strip()] = val.strip('"')
    return out


def _int(v, default=None):
    try:
        return int(re.search(r'-?\d+', str(v)).group(0))
    except (AttributeError, TypeError, ValueError):
        return default


def _column(table, base, col):
    """Colonne d'une table SNMP parcourue : {index: valeur}."""
    pre = f'{base}.{col}.'
    return {k[len(pre):]: v for k, v in table.items() if k.startswith(pre)}


def _disks_from_hr(storage):
    types, descr = _column(storage, HR_STORAGE, 2), _column(storage, HR_STORAGE, 3)
    unit, size, used = _column(storage, HR_STORAGE, 4), _column(storage, HR_STORAGE, 5), _column(storage, HR_STORAGE, 6)
    disks, ram = [], None
    for idx, t in types.items():
        t = t if t.startswith('.') else '.' + t
        u, s, us = _int(unit.get(idx), 1), _int(size.get(idx), 0), _int(used.get(idx), 0)
        if s and s < 0:                         # entier 32 bits signé dépassé (gros volumes)
            s += 2 ** 32
        if us and us < 0:
            us += 2 ** 32
        if t == HR_FIXED_DISK and s:
            label = descr.get(idx, idx)
            m = re.match(r'^([A-Za-z]:)', label)
            disks.append({'label': m.group(1) if m else label.split(' ')[0][:20],
                          'size': s * u, 'used': us * u, 'pct': round(100.0 * us / s, 1)})
        elif t == HR_RAM and s:
            ram = round(100.0 * us / s, 1)
    disks.sort(key=lambda d: d['label'])
    return disks, ram


def probe_pc(cfg):
    base = _snmp('snmpget', cfg, [SYS_NAME, HR_UPTIME])
    if not base:
        raise SnmpError('Répond, mais sans les informations du PC')
    storage = _snmp('snmpwalk', cfg, [HR_STORAGE])
    cpu = [v for v in (_int(x) for x in _snmp('snmpwalk', cfg, [HR_CPU_LOAD]).values()) if v is not None]
    disks, ram = _disks_from_hr(storage)
    up = _int(base.get(HR_UPTIME))
    return {'sysname': base.get(SYS_NAME, ''), 'uptime': up / 100.0 if up is not None else None,
            'cpu': round(sum(cpu) / len(cpu), 1) if cpu else None, 'cores': len(cpu),
            'ram_pct': ram, 'disks': disks}


def probe_nas(cfg):
    oids = {'status': SYNO + '.1.1.0', 'temp': SYNO + '.1.2.0', 'power': SYNO + '.1.3.0',
            'fan': SYNO + '.1.4.1.0', 'cpu_fan': SYNO + '.1.4.2.0', 'model': SYNO + '.1.5.1.0',
            'version': SYNO + '.1.5.3.0', 'upgrade': SYNO + '.1.5.4.0', 'uptime': HR_UPTIME,
            'mem_total': UCD_MEM + '.5.0', 'mem_avail': UCD_MEM + '.6.0',
            'mem_buf': UCD_MEM + '.14.0', 'mem_cache': UCD_MEM + '.15.0'}
    got = _snmp('snmpget', cfg, list(oids.values()))
    v = {k: got.get(o) for k, o in oids.items()}
    if v['status'] is None and v['model'] is None:
        raise SnmpError("Répond, mais sans les informations Synology")
    disks_t = _snmp('snmpwalk', cfg, [SYNO_DISK])
    raid_t = _snmp('snmpwalk', cfg, [SYNO_RAID])
    cpu = [x for x in (_int(c) for c in _snmp('snmpwalk', cfg, [HR_CPU_LOAD]).values()) if x is not None]
    disks = []
    names, models = _column(disks_t, SYNO_DISK, 2), _column(disks_t, SYNO_DISK, 3)
    status, temps, health = _column(disks_t, SYNO_DISK, 5), _column(disks_t, SYNO_DISK, 6), _column(disks_t, SYNO_DISK, 13)
    for idx in sorted(names, key=lambda i: _int(i, 0)):
        st, he = _int(status.get(idx)), _int(health.get(idx))
        disks.append({'name': names[idx], 'model': (models.get(idx) or '').strip(), 'status': st,
                      'status_label': SYNO_DISK_STATUS.get(st, str(st)), 'health': he,
                      'health_label': SYNO_DISK_HEALTH.get(he, '') if he else '',
                      'temp': _int(temps.get(idx)),
                      'ok': st in (1, None) and (he in (None, 1))})
    vols = []
    rn, rs = _column(raid_t, SYNO_RAID, 2), _column(raid_t, SYNO_RAID, 3)
    rfree, rtot = _column(raid_t, SYNO_RAID, 4), _column(raid_t, SYNO_RAID, 5)
    for idx in sorted(rn, key=lambda i: _int(i, 0)):
        st = _int(rs.get(idx))
        tot, free = _int(rtot.get(idx), 0), _int(rfree.get(idx), 0)
        vols.append({'name': rn[idx], 'status': st, 'status_label': SYNO_RAID_STATUS.get(st, str(st)),
                     'total': tot, 'used': max(0, tot - free),
                     'pct': round(100.0 * (tot - free) / tot, 1) if tot else None,
                     'level': 'ok' if st == 1 else 'down' if st in (11, 12) else 'warn'})
    mem = None
    mt, ma = _int(v['mem_total']), _int(v['mem_avail'])
    if mt:
        free = (ma or 0) + (_int(v['mem_buf'], 0) or 0) + (_int(v['mem_cache'], 0) or 0)
        mem = round(max(0.0, 100.0 * (mt - free) / mt), 1)
    up = _int(v['uptime'])
    return {'model': v['model'] or '', 'version': v['version'] or '',
            'status': _int(v['status']), 'temp': _int(v['temp']), 'power': _int(v['power']),
            'fan': _int(v['fan']), 'cpu_fan': _int(v['cpu_fan']),
            'upgrade': SYNO_UPGRADE.get(_int(v['upgrade']), ''),
            'uptime': up / 100.0 if up is not None else None,
            'cpu': round(sum(cpu) / len(cpu), 1) if cpu else None, 'ram_pct': mem,
            'disks': disks, 'volumes': vols}


def probe_site(cfg, timeout=10):
    t0 = time.perf_counter()
    try:
        r = requests.get(cfg['url'], timeout=timeout, allow_redirects=True,
                         headers={'User-Agent': 'BL-RMO site check'})
    except requests.exceptions.SSLError:
        return {'ok': False, 'message': 'Erreur de certificat HTTPS', 'code': None, 'ms': None}
    except requests.exceptions.Timeout:
        return {'ok': False, 'message': f'Pas de réponse en {timeout} s', 'code': None, 'ms': None}
    except requests.exceptions.ConnectionError:
        return {'ok': False, 'message': 'Connexion impossible', 'code': None, 'ms': None}
    except requests.exceptions.RequestException as e:
        return {'ok': False, 'message': f'Erreur : {e.__class__.__name__}', 'code': None, 'ms': None}
    ms = round((time.perf_counter() - t0) * 1000)
    if r.status_code >= 400:
        return {'ok': False, 'message': f'Erreur HTTP {r.status_code}', 'code': r.status_code, 'ms': ms}
    kw = cfg.get('keyword')
    if kw and kw.lower() not in r.text.lower():
        return {'ok': False, 'message': f'Texte « {kw} » absent de la page', 'code': r.status_code, 'ms': ms}
    slow = ms > int(cfg.get('slow_ms') or 5000)
    return {'ok': True, 'slow': slow, 'message': 'Réponse lente' if slow else 'En ligne', 'code': r.status_code, 'ms': ms}


def run_probe(dev):
    """Relevé brut d'un appareil (bouton Tester)."""
    k, cfg = dev['kind'], dev['config']
    try:
        if k == 'pc':
            return {'ok': True, 'data': probe_pc(cfg)}
        if k == 'nas':
            return {'ok': True, 'data': probe_nas(cfg)}
        r = probe_site(cfg)
        return {'ok': r['ok'], 'data': r, 'message': r['message']}
    except SnmpError as e:
        return {'ok': False, 'message': str(e)}


def _fmt_size(b):
    if b is None:
        return '-'
    for unit, div in (('To', 1e12), ('Go', 1e9), ('Mo', 1e6)):
        if b >= div:
            v = b / div
            return f"{v:.1f} {unit}".replace('.0 ', ' ').replace('.', ',') if v < 10 else f"{v:.0f} {unit}"
    return f'{b} o'


# ══════════════════════════════════════════════════════════════════════════
#  Surveillance
# ══════════════════════════════════════════════════════════════════════════

class ParkMonitor:
    def __init__(self, db_path='fm_monitor.db', alert_getter=None):
        self.db_path = db_path
        self.alert_getter = alert_getter
        self._lock = threading.RLock()
        self._devices = {}
        self._state = {}               # id -> état (enregistré en base, survit aux redémarrages)
        self._inflight = set()
        self._next = {}
        self._running = False
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix='park-probe')
        self._init_db()
        self._load()

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_db(self):
        with self._conn() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS park_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT, position INTEGER NOT NULL DEFAULT 0,
                data TEXT NOT NULL, state TEXT);
            CREATE TABLE IF NOT EXISTS park_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, device_id INTEGER,
                device_name TEXT, kind TEXT, level TEXT, message TEXT, email_sent INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_park_events_ts ON park_events(timestamp);
            CREATE TABLE IF NOT EXISTS park_samples (
                ts REAL NOT NULL, device_id INTEGER NOT NULL, ok INTEGER, ms INTEGER);
            CREATE INDEX IF NOT EXISTS idx_park_samples ON park_samples(device_id, ts);
            ''')

    def _load(self):
        with self._conn() as c:
            rows = c.execute('SELECT * FROM park_devices ORDER BY position, id').fetchall()
        with self._lock:
            self._devices.clear()
            for r in rows:
                try:
                    dev = json.loads(r['data'])
                except ValueError:
                    continue
                dev['id'], dev['position'] = r['id'], r['position']
                self._devices[r['id']] = dev
                if r['id'] not in self._state:
                    try:
                        self._state[r['id']] = json.loads(r['state']) if r['state'] else {}
                    except ValueError:
                        self._state[r['id']] = {}
                    self._state[r['id']].setdefault('alerts', {})
            for did in list(self._state):
                if did not in self._devices:
                    del self._state[did]

    # ── Appareils ────────────────────────────────────────────────────────
    def list_devices(self):
        with self._lock:
            return [public_device(d) for d in sorted(self._devices.values(), key=lambda d: (d['position'], d['id']))]

    def get_device(self, did):
        with self._lock:
            d = self._devices.get(did)
            return json.loads(json.dumps(d)) if d else None

    def create_device(self, data):
        dev = validate_device(data)
        with self._lock:
            pos = max([d['position'] for d in self._devices.values()] or [-1]) + 1
        with self._conn() as c:
            did = c.execute('INSERT INTO park_devices (position, data) VALUES (?, ?)',
                            (pos, json.dumps(dev, ensure_ascii=False))).lastrowid
        self._load()
        self._next[did] = 0
        return did

    def update_device(self, did, data):
        old = self.get_device(did)
        if not old:
            raise KeyError(did)
        dev = validate_device(data, old)
        with self._conn() as c:
            c.execute('UPDATE park_devices SET data=? WHERE id=?', (json.dumps(dev, ensure_ascii=False), did))
        self._load()
        self._next[did] = 0

    def delete_device(self, did):
        with self._conn() as c:
            c.execute('DELETE FROM park_devices WHERE id=?', (did,))
            c.execute('DELETE FROM park_samples WHERE device_id=?', (did,))
        self._load()

    def reorder(self, ids):
        with self._conn() as c:
            for pos, did in enumerate(int(i) for i in ids):
                c.execute('UPDATE park_devices SET position=? WHERE id=?', (pos, did))
        self._load()

    def test(self, data):
        old = self.get_device(int(data['id'])) if data.get('id') else None
        return run_probe(validate_device(data, old))

    # ── Boucle ───────────────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, name='park-monitor', daemon=True).start()
        logger.info(f"Surveillance du parc démarrée ({len(self._devices)} appareils)")

    def _loop(self):
        last_clean = 0
        while self._running:
            now = time.time()
            with self._lock:
                due = [json.loads(json.dumps(d)) for did, d in self._devices.items()
                       if d.get('enabled', True) and did not in self._inflight and now >= self._next.get(did, 0)]
                for d in due:
                    self._next[d['id']] = now + INTERVAL
                    self._inflight.add(d['id'])
            for d in due:
                self._pool.submit(self._poll, d)
            if now - last_clean > 3600:
                last_clean = now
                self._cleanup()
            time.sleep(1)

    def _poll(self, dev):
        try:
            if dev['kind'] == 'site':
                self._record_site(dev, probe_site(dev['config']))
            else:
                try:
                    data = probe_pc(dev['config']) if dev['kind'] == 'pc' else probe_nas(dev['config'])
                    self._record_snmp(dev, data, None)
                except SnmpError as e:
                    self._record_snmp(dev, None, str(e))
        except Exception as e:
            logger.exception(f"Parc : relevé de {dev.get('name')} : {e}")
        finally:
            with self._lock:
                self._inflight.discard(dev['id'])

    # ── Événements et alertes ────────────────────────────────────────────
    def _event(self, dev, kind, level, message, mail=None):
        """level : alert, restored, info. mail : (titre) ou None."""
        now = datetime.now().isoformat(timespec='seconds')
        eid = None
        try:
            with self._conn() as c:
                eid = c.execute('INSERT INTO park_events (timestamp, device_id, device_name, kind, level, message) '
                                'VALUES (?,?,?,?,?,?)', (now, dev['id'], dev['name'], kind, level, message)).lastrowid
        except sqlite3.Error as e:
            logger.error(f"Parc : événement non enregistré : {e}")
        (logger.warning if level == 'alert' else logger.info)(f"Parc : {dev['name']} : {message}")
        if mail and dev.get('alert_email') and self.alert_getter:
            alert = None
            try:
                alert = self.alert_getter()
            except Exception:
                pass
            if alert is not None and getattr(alert, 'alerts_enabled', True):
                cfg = dev.get('config') or {}
                where = cfg.get('host') or cfg.get('url') or ''

                def mark(eid=eid):
                    try:
                        with self._conn() as c:
                            c.execute('UPDATE park_events SET email_sent=1 WHERE id=?', (eid,))
                    except sqlite3.Error:
                        pass
                details = f"Appareil : {dev['name']}\nAdresse : {where}\nDétail : {message}"
                threading.Thread(target=lambda: alert.send_alert(f"Parc · {mail}", details, skip_cooldown=True,
                                                                 on_sent=mark, source=('Appareil', dev['name'])),
                                 daemon=True).start()

    def _set_alert(self, dev, st, key, active, kind, msg_on, msg_off, title_on, title_off):
        """Alerte persistante (disque plein, NAS dégradé...) : un mail à l'apparition, un au retour."""
        was = key in st['alerts']
        if active and not was:
            st['alerts'][key] = msg_on
            self._event(dev, kind, 'alert', msg_on, title_on)
        elif active:
            st['alerts'][key] = msg_on
        elif was and not active:
            del st['alerts'][key]
            self._event(dev, kind, 'restored', msg_off, title_off)

    def _save_state(self, did, st):
        try:
            with self._conn() as c:
                c.execute('UPDATE park_devices SET state=? WHERE id=?', (json.dumps(st, ensure_ascii=False), did))
        except sqlite3.Error:
            pass

    def _record_snmp(self, dev, data, error):
        did, now = dev['id'], time.time()
        with self._lock:
            st = self._state.setdefault(did, {'alerts': {}})
        st.setdefault('alerts', {})
        st['last_check'] = now
        name = dev['name']
        pc = dev['kind'] == 'pc'
        always = pc and dev.get('always_on') or dev['kind'] == 'nas'
        prev = st.get('state')
        if data is None:
            st['fails'] = st.get('fails', 0) + 1
            st['error'] = error
            if st['fails'] >= FAILS_DOWN and prev != 'off':
                st['state'], st['since'] = 'off', st.get('last_seen') or now
                if always:
                    st['alerts']['down'] = 'Injoignable'
                    self._event(dev, 'down', 'alert', f"Ne répond plus ({error})", f"{name} injoignable")
                elif prev == 'on':
                    self._event(dev, 'off', 'info', 'Éteint')
                st['cpu'] = None
            self._save_state(did, st)
            return
        # Répond
        st['fails'], st['error'] = 0, ''
        st['last_seen'] = now
        old_up, up = st.get('uptime'), data.get('uptime')
        rebooted = always and old_up is not None and up is not None and up + 120 < old_up
        if prev != 'on':
            off_since = st.get('since') or now
            st['state'], st['since'] = 'on', now
            st['cpu_max'], st['cpu_max_at'] = None, None
            if prev == 'off':
                if always:
                    st['alerts'].pop('down', None)
                    # Démarré pendant la coupure : uptime plus court que la coupure
                    boot = up is not None and up < now - off_since + 120
                    if boot:
                        st['since'] = now - up
                    self._event(dev, 'down', 'restored', 'Répond de nouveau' + (' (redémarré)' if boot else ''),
                                f"{name} rétabli")
                else:
                    self._event(dev, 'on', 'info', 'Allumé')
            elif prev is None and always and up:
                st['since'] = now - up                       # première observation : depuis son démarrage
        elif rebooted:
            st['since'], st['rebooted_at'] = now - up, now
            st['cpu_max'], st['cpu_max_at'] = None, None
            self._event(dev, 'reboot', 'alert', f"Redémarré il y a {max(1, int(up // 60))} min", f"{name} redémarré")
        st['uptime'] = up
        cfg = dev['config']
        cpu = data.get('cpu')
        st['cpu'] = cpu
        if cpu is not None and (st.get('cpu_max') is None or cpu >= st['cpu_max']):
            st['cpu_max'], st['cpu_max_at'] = cpu, now
        if pc and cpu is not None:
            if cpu >= cfg.get('cpu_pct', 90):
                st['cpu_high_since'] = st.get('cpu_high_since') or now
            else:
                st['cpu_high_since'] = None
            high = st['cpu_high_since'] and now - st['cpu_high_since'] >= 60 * cfg.get('cpu_minutes', 10)
            self._set_alert(dev, st, 'cpu', bool(high) or ('cpu' in st['alerts'] and cpu >= cfg.get('cpu_pct', 90) - 10),
                            'cpu', f"Processeur à {cpu:.0f} % depuis {cfg.get('cpu_minutes', 10)} min (seuil {cfg.get('cpu_pct', 90)} %)",
                            f"Processeur revenu à {cpu:.0f} %", f"{name} : processeur saturé", f"{name} : processeur normal")
        st['ram_pct'] = data.get('ram_pct')
        lim = cfg.get('disk_pct', 90)
        if pc:
            st['disks'] = data['disks']
            for d in data['disks']:
                key = 'disk:' + d['label']
                active = d['pct'] >= lim or (key in st['alerts'] and d['pct'] >= lim - 2)
                self._set_alert(dev, st, key, active, 'disk',
                                f"{d['label']} à {d['pct']:.0f} % (seuil {lim} %), {_fmt_size(d['size'] - d['used'])} libres",
                                f"{d['label']} revenu à {d['pct']:.0f} %",
                                f"{name} : disque {d['label']} presque plein", f"{name} : disque {d['label']} normal")
            st['sysname'] = data.get('sysname', '')
        else:
            st['nas'] = {k: data[k] for k in ('model', 'version', 'status', 'temp', 'power', 'fan', 'cpu_fan', 'upgrade', 'disks', 'volumes')}
            self._set_alert(dev, st, 'system', data['status'] not in (1, None), 'nas',
                            'Système en panne (état DSM)', 'Système normal', f"{name} : système en panne", f"{name} : système normal")
            self._set_alert(dev, st, 'power', data['power'] not in (1, None), 'nas',
                            'Alimentation en panne', 'Alimentation normale', f"{name} : alimentation en panne", f"{name} : alimentation normale")
            fan_bad = data['fan'] not in (1, None) or data['cpu_fan'] not in (1, None)
            self._set_alert(dev, st, 'fan', fan_bad, 'nas', 'Ventilateur en panne', 'Ventilateurs normaux',
                            f"{name} : ventilateur en panne", f"{name} : ventilateurs normaux")
            tmax = cfg.get('temp_max', 60)
            t = data.get('temp')
            self._set_alert(dev, st, 'temp', t is not None and (t >= tmax or ('temp' in st['alerts'] and t >= tmax - 3)), 'nas',
                            f"Température {t} °C (seuil {tmax} °C)", f"Température revenue à {t} °C",
                            f"{name} : température élevée", f"{name} : température normale")
            for d in data['disks']:
                bad = not d['ok']
                self._set_alert(dev, st, 'disk:' + d['name'], bad, 'nas',
                                f"{d['name']} : {d['status_label']}" + (f", santé {d['health_label']}" if d['health_label'] and d['health'] != 1 else ''),
                                f"{d['name']} : normal", f"{name} : {d['name']} en défaut", f"{name} : {d['name']} normal")
            for v in data['volumes']:
                self._set_alert(dev, st, 'raid:' + v['name'], v['level'] != 'ok', 'nas',
                                f"{v['name']} : {v['status_label']}", f"{v['name']} : normal",
                                f"{name} : {v['name']} {v['status_label'].lower()}", f"{name} : {v['name']} normal")
                if v['pct'] is not None:
                    key = 'full:' + v['name']
                    self._set_alert(dev, st, key, v['pct'] >= lim or (key in st['alerts'] and v['pct'] >= lim - 2), 'nas',
                                    f"{v['name']} rempli à {v['pct']:.0f} % (seuil {lim} %)", f"{v['name']} revenu à {v['pct']:.0f} %",
                                    f"{name} : {v['name']} presque plein", f"{name} : {v['name']} normal")
        self._save_state(did, st)

    def _record_site(self, dev, r):
        did, now = dev['id'], time.time()
        with self._lock:
            st = self._state.setdefault(did, {'alerts': {}})
        st.setdefault('alerts', {})
        st['last_check'], st['result'] = now, r
        try:
            with self._conn() as c:
                c.execute('INSERT INTO park_samples (ts, device_id, ok, ms) VALUES (?,?,?,?)',
                          (now, did, int(bool(r['ok'])), r.get('ms')))
        except sqlite3.Error:
            pass
        name = dev['name']
        if r['ok']:
            st['fails'] = 0
            if st.get('state') == 'down':
                st['alerts'].pop('down', None)
                self._event(dev, 'down', 'restored', f"En ligne (HTTP {r['code']}, {r['ms']} ms)", f"{name} de nouveau en ligne")
            if st.get('state') != 'up':
                st['state'], st['since'] = 'up', now
            self._set_alert(dev, st, 'slow', bool(r.get('slow')), 'slow',
                            f"Réponse lente : {r['ms']} ms (seuil {dev['config'].get('slow_ms', 5000)} ms)",
                            f"Temps de réponse revenu à {r['ms']} ms", f"{name} : réponse lente", f"{name} : temps de réponse normal")
        else:
            st['fails'] = st.get('fails', 0) + 1
            if st['fails'] >= FAILS_DOWN and st.get('state') != 'down':
                st['state'], st['since'] = 'down', now
                st['alerts']['down'] = r['message']
                st['alerts'].pop('slow', None)
                self._event(dev, 'down', 'alert', r['message'], f"{name} injoignable")
            elif st.get('state') == 'down':
                st['alerts']['down'] = r['message']
        self._save_state(did, st)

    def _cleanup(self):
        lim = (datetime.now() - timedelta(days=EVENTS_RETENTION_DAYS)).isoformat(timespec='seconds')
        try:
            with self._conn() as c:
                c.execute('DELETE FROM park_events WHERE timestamp < ?', (lim,))
                c.execute('DELETE FROM park_samples WHERE ts < ?', (time.time() - SAMPLES_RETENTION_DAYS * 86400,))
        except sqlite3.Error:
            pass

    # ── Lecture ──────────────────────────────────────────────────────────
    def _availability(self):
        since = time.time() - 86400
        try:
            with self._conn() as c:
                rows = c.execute('SELECT device_id, COUNT(*) n, SUM(ok) ok FROM park_samples WHERE ts >= ? '
                                 'GROUP BY device_id', (since,)).fetchall()
            return {r['device_id']: round(100.0 * (r['ok'] or 0) / r['n'], 1) for r in rows if r['n']}
        except sqlite3.Error:
            return {}

    def events(self, limit=30):
        try:
            with self._conn() as c:
                return [dict(r) for r in c.execute('SELECT * FROM park_events ORDER BY id DESC LIMIT ?', (limit,))]
        except sqlite3.Error:
            return []

    def status(self):
        avail = self._availability()
        out = []
        with self._lock:
            for d in sorted(self._devices.values(), key=lambda d: (d['position'], d['id'])):
                st = json.loads(json.dumps(self._state.get(d['id']) or {}))
                cfg = d.get('config') or {}
                item = {'id': d['id'], 'kind': d['kind'], 'name': d['name'], 'enabled': d.get('enabled', True),
                        'always_on': bool(d.get('always_on')), 'host': cfg.get('host', ''), 'url': cfg.get('url', ''),
                        'os': cfg.get('os', ''), 'disk_pct': cfg.get('disk_pct'), 'cpu_pct': cfg.get('cpu_pct'),
                        'temp_max': cfg.get('temp_max'), 'alert_email': d.get('alert_email', True),
                        'state': st.get('state'), 'since': st.get('since'), 'last_check': st.get('last_check'),
                        'error': st.get('error', ''), 'alerts': st.get('alerts', {}),
                        'rebooted_at': st.get('rebooted_at')}
                if d['kind'] == 'pc':
                    item.update({k: st.get(k) for k in ('cpu', 'cpu_max', 'cpu_max_at', 'ram_pct', 'disks', 'sysname')})
                elif d['kind'] == 'nas':
                    item.update({'cpu': st.get('cpu'), 'ram_pct': st.get('ram_pct'), 'nas': st.get('nas'),
                                 'uptime': st.get('uptime')})
                else:
                    item.update({'result': st.get('result'), 'availability': avail.get(d['id'])})
                out.append(item)
        pcs24 = [x for x in out if x['kind'] == 'pc' and x['always_on'] and x['enabled']]
        nas = [x for x in out if x['kind'] == 'nas' and x['enabled']]
        sites = [x for x in out if x['kind'] == 'site' and x['enabled']]
        alerts = [(x['name'], k, v) for x in out if x['enabled'] for k, v in (x['alerts'] or {}).items()]
        return {'devices': out, 'events': self.events(),
                'summary': {'pc24_total': len(pcs24), 'pc24_on': sum(1 for x in pcs24 if x['state'] == 'on'),
                            'nas_total': len(nas), 'sites_total': len(sites),
                            'sites_up': sum(1 for x in sites if x['state'] == 'up'),
                            'alerts': [{'device': a, 'key': k, 'message': m} for a, k, m in alerts]}}
