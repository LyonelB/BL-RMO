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
import csv
import hmac
import io
import json
import os
import secrets
import logging
import re
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

PROBE_TYPES = ('ping', 'burst', 'tcp', 'http', 'snmp', 'icecast', 'fm_local')
STABLE_STATES = ('unknown', 'up', 'warn', 'down')
KINDS = ('device', 'probe')   # appareil de la chaîne / sonde réseau


def infer_kind(dev):
    """Catégorie d'une entrée créée avant l'existence du champ 'kind'."""
    if dev.get('kind') in KINDS:
        return dev['kind']
    if dev.get('probe') == 'burst' or dev.get('site') == 'Sondes réseau':
        return 'probe'
    return 'device'
SECRET_MASK = '********'
HISTORY_LEN = 120          # points de latence gardés en mémoire par appareil
EVENTS_RETENTION_DAYS = 90
SAMPLES_RETENTION_DAYS = 7      # mesures brutes (une ligne par sondage)
EXPORT_MAX_DAYS = 7
DOWN_EMAIL_MIN_INTERVAL = 300  # secondes entre deux emails "injoignable" pour un même appareil

_HOST_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,251}[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$')
_OID_RE = re.compile(r'^\.?\d+(\.\d+)+$')
_COMMUNITY_RE = re.compile(r'^[\x21-\x7e]{1,64}$')
SNMP3_LEVELS = ('noAuthNoPriv', 'authNoPriv', 'authPriv')
SNMP3_AUTH = ('MD5', 'SHA', 'SHA-224', 'SHA-256', 'SHA-384', 'SHA-512')
SNMP3_PRIV = ('DES', 'AES', 'AES192', 'AES256')


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
        'kind': data.get('kind') if data.get('kind') in KINDS else infer_kind(data),
        'name': name[:60],
        'site': str(data.get('site', '')).strip()[:40] or 'Studio',
        'role': str(data.get('role', '')).strip()[:40],
        'probe': probe,
        'interval': _num(data.get('interval'), 'Intervalle', 30, 1, 3600, integer=True),
        'timeout': _num(data.get('timeout'), 'Délai', 3, 1, 30),
        'fail_threshold': _num(data.get('fail_threshold'), "Seuil d'échecs", 3, 1, 20, integer=True),
        'alert_email': bool(data.get('alert_email', False)),
        'alert_on_warn': bool(data.get('alert_on_warn', False)),
        'enabled': bool(data.get('enabled', True)),
        'notes': str(data.get('notes', '')).strip()[:300],
        # Synoptique : libellé de la liaison vers l'appareil suivant (AES, MPX, Faisceau 5 GHz...)
        'out_label': str(data.get('out_label', '')).strip()[:40],
        # Adresse de l'interface web de l'appareil (vide : pas de lien)
        'web': _url(data['web']) if str(data.get('web') or '').strip() else '',
    }
    # Une sonde réseau surveille le lien entre deux appareils de la chaîne
    if dev['kind'] == 'probe':
        lf = _num(data.get('link_from'), 'Lien (départ)', allow_none=True, integer=True)
        lt = _num(data.get('link_to'), 'Lien (arrivée)', allow_none=True, integer=True)
        if (lf is None) != (lt is None):
            raise ValueError("Lien surveillé : choisir les deux appareils, ou aucun")
        if lf is not None and lf == lt:
            raise ValueError("Lien surveillé : deux appareils différents")
        dev['link_from'], dev['link_to'] = lf, lt

    raw = data.get('config') or {}
    old = (existing or {}).get('config') or {}
    cfg = {}

    if probe == 'ping':
        cfg['host'] = _host(raw.get('host'))
        cfg['warn_ms'] = _num(raw.get('warn_ms'), 'Latence max', None, 1, 10000, allow_none=True)

    elif probe == 'burst':
        cfg['host'] = _host(raw.get('host'))
        cfg['count'] = _num(raw.get('count'), 'Nombre de pings', 20, 2, 100, integer=True)
        cfg['spacing_ms'] = _num(raw.get('spacing_ms'), 'Espacement', 50, 10, 1000, integer=True)
        cfg['loss_pct'] = _num(raw.get('loss_pct'), 'Seuil de pertes', 5, 0, 100)
        cfg['max_ms'] = _num(raw.get('max_ms'), 'Latence max', 150, 1, 10000)
        cfg['jitter_ms'] = _num(raw.get('jitter_ms'), 'Gigue max', None, 0.1, 10000, allow_none=True)

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
        cfg['version'] = raw.get('version') if raw.get('version') in ('1', '2c', '3') else '2c'
        if cfg['version'] == '3':
            # SNMPv3 : utilisateur, authentification et chiffrement (USM)
            user = str(raw.get('user') or '').strip()
            if not _COMMUNITY_RE.match(user):
                raise ValueError("SNMPv3 : nom d'utilisateur requis")
            level = raw.get('level') if raw.get('level') in SNMP3_LEVELS else 'authPriv'
            cfg.update({'user': user, 'level': level, 'community': ''})
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
            fmt = m.get('format') if m.get('format') in ('number', 'uptime', 'text', 'enum') else 'number'
            metrics.append({
                'label': (str(m.get('label', '')).strip() or oid)[:40],
                'oid': oid if oid.startswith('.') else '.' + oid,
                'format': fmt,
                'scale': _num(m.get('scale'), f'Échelle ({oid})', 1, allow_none=False) if fmt == 'number' else 1,
                'unit': str(m.get('unit', '')).strip()[:12],
                'min': _num(m.get('min'), f'Min ({oid})', allow_none=True) if fmt == 'number' else None,
                'max': _num(m.get('max'), f'Max ({oid})', allow_none=True) if fmt == 'number' else None,
                'expect': str(m.get('expect') or '').strip()[:40] if fmt in ('text', 'enum') else '',
                'labels': str(m.get('labels') or '').strip()[:200] if fmt == 'enum' else '',
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
    for k in ('community', 'auth_pass', 'priv_pass'):
        if cfg.get(k):
            cfg[k] = SECRET_MASK
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


_PING_STATS = re.compile(r'(\d+) packets transmitted, (\d+) received')
_PING_RTT = re.compile(r'= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms')


def probe_burst(cfg, timeout):
    """Rafale de pings, équivalent d'une sonde netwatch MikroTik « icmp ».

    Envoie `count` pings espacés de `spacing_ms` et mesure pertes, latence
    moyenne/maximale et gigue (mdev). Dégradé si pertes > loss_pct, latence
    max > max_ms ou gigue > jitter_ms. Hors service si aucune réponse.
    """
    if not shutil.which('ping'):
        return _result(False, "Commande ping absente (sudo apt install iputils-ping)")
    count, spacing = cfg['count'], cfg['spacing_ms'] / 1000.0
    wait = max(1, int(round(timeout)))
    cmd = ['ping', '-n', '-q', '-c', str(count), '-i', f'{spacing:.3f}', '-W', str(wait), cfg['host']]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=count * spacing + wait + 5)
    except subprocess.TimeoutExpired:
        return _result(False, 'Rafale de pings bloquée (délai dépassé)')
    st = _PING_STATS.search(p.stdout)
    if not st:
        err = (p.stderr or '').strip().splitlines()
        msg = err[-1] if err else 'Ping impossible'
        if 'interval' in msg.lower() or 'flood' in msg.lower():
            msg = 'Espacement trop court pour ce système (essayer 200 ms)'
        return _result(False, msg[:160])
    sent, recv = int(st.group(1)), int(st.group(2))
    loss = 100.0 * (sent - recv) / sent if sent else 100.0
    metrics = [{'label': 'Pertes', 'value': loss, 'display': f'{loss:.0f}', 'unit': '%',
                'state': 'warn' if loss > cfg['loss_pct'] else 'ok'}]
    if recv == 0:
        return _result(False, f'Aucune réponse sur {sent} pings', None, metrics)

    rtt = _PING_RTT.search(p.stdout)
    avg = mx = mdev = None
    if rtt:
        avg, mx, mdev = float(rtt.group(2)), float(rtt.group(3)), float(rtt.group(4))
        metrics.append({'label': 'Latence moy.', 'value': avg, 'display': _fmt_number(avg), 'unit': 'ms', 'state': None})
        metrics.append({'label': 'Latence max', 'value': mx, 'display': _fmt_number(mx), 'unit': 'ms',
                        'state': 'warn' if mx > cfg['max_ms'] else None})
        jit = cfg.get('jitter_ms')
        metrics.append({'label': 'Gigue', 'value': mdev, 'display': _fmt_number(mdev), 'unit': 'ms',
                        'state': 'warn' if jit and mdev > jit else None})

    reasons = []
    if loss > cfg['loss_pct']:
        reasons.append(f'{loss:.0f} % de pertes')
    if mx is not None and mx > cfg['max_ms']:
        reasons.append(f'pic à {_fmt_number(mx)} ms')
    if cfg.get('jitter_ms') and mdev is not None and mdev > cfg['jitter_ms']:
        reasons.append(f'gigue {_fmt_number(mdev)} ms')
    if reasons:
        return _result(True, 'Dégradé : ' + ', '.join(reasons), avg, metrics, warn=True)
    return _result(True, f'{recv}/{sent} réponses', avg, metrics)


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
    if cfg.get('version') == '3':
        base = ['snmpget', '-v', '3', '-u', cfg['user'], '-l', cfg.get('level', 'authPriv')]
        if cfg.get('level') != 'noAuthNoPriv':
            base += ['-a', cfg.get('auth_proto', 'SHA'), '-A', cfg.get('auth_pass', '')]
        if cfg.get('level') == 'authPriv':
            base += ['-x', cfg.get('priv_proto', 'AES'), '-X', cfg.get('priv_pass', '')]
    else:
        base = ['snmpget', '-v', cfg.get('version', '2c'), '-c', cfg['community']]
    base += ['-t', str(max(1, int(round(timeout)))), '-r', '0', '-OqvteU', '-m', '', '-Cf', target]

    def _get(oids):
        return subprocess.run(base + oids, capture_output=True, text=True, timeout=timeout + 3)

    t0 = time.perf_counter()
    try:
        p = _get([m['oid'] for m in metrics_cfg])
    except subprocess.TimeoutExpired:
        return _result(False, 'Pas de réponse SNMP')
    lat = (time.perf_counter() - t0) * 1000
    err = (p.stderr or '').strip()

    if p.returncode != 0 and 'Timeout' in err:
        hint = {'2c': ' (essayer la version v1)',
                '3': ' (vérifier le mot de passe de chiffrement et son protocole)'}.get(cfg.get('version', '2c'), '')
        return _result(False, f'Pas de réponse SNMP{hint}')
    if p.returncode != 0 and cfg.get('version') == '3':
        low = err.lower()
        if 'unknown user' in low:
            return _result(False, 'SNMPv3 : utilisateur inconnu de l\'appareil')
        if 'authentication failure' in low or 'wrong digest' in low:
            return _result(False, "SNMPv3 : échec d'authentification (mot de passe ou protocole)")
        if 'unsupported security level' in low:
            return _result(False, 'SNMPv3 : niveau de sécurité non accepté par l\'appareil')
        if 'decrypt' in low:
            return _result(False, 'SNMPv3 : échec du déchiffrement (mot de passe ou protocole de chiffrement)')

    if p.returncode == 0:
        lines = p.stdout.rstrip('\n').split('\n')
    if p.returncode == 0 and len(lines) >= len(metrics_cfg):
        pass
    elif p.returncode == 0 or 'noSuchName' in err or 'Error in packet' in err:
        # Réponse incomplète (certains agents, ex. BW Broadcast, ne renvoient qu'une
        # valeur par requête) ou SNMP v1 avec un OID inconnu : on interroge chaque OID.
        lines = []
        for m in metrics_cfg:
            try:
                q = _get([m['oid']])
                lines.append(q.stdout.strip() if q.returncode == 0 else 'No Such Object')
            except subprocess.TimeoutExpired:
                lines.append('No Such Object')
    else:
        msg = err.splitlines()[-1] if err else 'Erreur SNMP'
        return _result(False, msg[:160])

    metrics, warn, missing = [], False, 0
    for i, m in enumerate(metrics_cfg):
        raw = lines[i].strip() if i < len(lines) else ''
        item = {'label': m['label'], 'unit': m.get('unit', ''), 'value': None, 'display': '?', 'state': None}
        if not raw or raw.startswith('No Such') or raw.startswith('No more'):
            item['display'] = 'OID absent'
            item['state'] = 'warn'
            missing += 1
        elif m['format'] in ('text', 'enum'):
            item['value'] = raw.strip('"')
            item['display'] = item['value'][:60]
            if m['format'] == 'enum':
                # « 1=Non,2=Oui » : traduit le code numérique en libellé
                code = re.search(r'-?\d+', item['value'])
                code = code.group(0) if code else item['value']
                labels = dict(p.split('=', 1) for p in re.split(r'[,;]', m.get('labels') or '') if '=' in p)
                labels = {k.strip(): v.strip() for k, v in labels.items()}
                item['value'] = code
                item['display'] = labels.get(code, code)[:60]
            exp = m.get('expect')
            if exp:
                # plusieurs valeurs acceptées possibles : « Locked|1 »
                okv = [e.strip().lower() for e in exp.split('|') if e.strip()]
                if item['value'].strip().lower() in okv or item['display'].strip().lower() in okv:
                    item['state'] = 'ok'
                else:
                    item['state'] = 'warn'
                    warn = True
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
        msg = f"{missing} OID absent{'s' if missing > 1 else ''} (vérifier le modèle)"
        warn = True
    else:
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
        if probe == 'burst':
            return probe_burst(cfg, timeout)
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
    def __init__(self, db_path='fm_monitor.db', fm_getter=None, alert_getter=None, max_workers=10):
        self.db_path = db_path
        self.fm_getter = fm_getter
        self.alert_getter = alert_getter
        self._lock = threading.RLock()
        self._devices = {}     # id -> dict
        self._runtime = {}     # id -> état courant
        self._inflight = set()
        self._external = {}    # (source, probe) -> dernier état reçu
        self._samples = []     # mesures en attente d'écriture (par lots)
        self._trap_last_id = None   # dernier trap SNMP traité (journaux syslog.db)
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
            cols = [r[1] for r in c.execute('PRAGMA table_info(chain_events)').fetchall()]
            if 'source' not in cols:
                c.execute('ALTER TABLE chain_events ADD COLUMN source TEXT')
            c.execute('''CREATE TABLE IF NOT EXISTS chain_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                device_id INTEGER,
                ok INTEGER,
                warn INTEGER,
                latency REAL,
                message TEXT,
                metrics TEXT
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_chain_samples_ts ON chain_samples(ts)')
            c.execute('''CREATE TABLE IF NOT EXISTS chain_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )''')
        self._load_external()

    # ── Événements externes (netwatch MikroTik, etc.) ────────────────────
    def _load_external(self):
        """Recharge le dernier état connu de chaque sonde externe."""
        q = '''SELECT e.* FROM chain_events e
               JOIN (SELECT source, message, MAX(id) AS mid FROM chain_events
                     WHERE source IS NOT NULL GROUP BY source, json_extract(message, '$.probe')) m
               ON e.id = m.mid'''
        try:
            with self._conn() as c:
                rows = c.execute(q).fetchall()
        except sqlite3.Error:
            rows = []
        for r in rows:
            try:
                info = json.loads(r['message'])
            except (TypeError, ValueError):
                continue
            key = (r['source'], info.get('probe', ''))
            self._external[key] = {
                'source': r['source'], 'probe': info.get('probe', ''), 'host': info.get('host', ''),
                'state': r['to_state'], 'text': info.get('text', ''), 'ts': r['timestamp'],
            }

    def get_token(self):
        with self._conn() as c:
            row = c.execute("SELECT value FROM chain_settings WHERE key='hook_token'").fetchone()
            if row and row['value']:
                return row['value']
            token = secrets.token_urlsafe(18)
            c.execute("INSERT OR REPLACE INTO chain_settings (key, value) VALUES ('hook_token', ?)", (token,))
            return token

    def regenerate_token(self):
        token = secrets.token_urlsafe(18)
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO chain_settings (key, value) VALUES ('hook_token', ?)", (token,))
        return token

    def check_token(self, token):
        return bool(token) and hmac.compare_digest(str(token), self.get_token())

    @staticmethod
    def _device_host(dev):
        cfg = dev.get('config') or {}
        if cfg.get('host'):
            return cfg['host']
        if cfg.get('url'):
            return urlparse(cfg['url']).hostname or ''
        return ''

    def record_external(self, source, probe, host, state, text=''):
        """Enregistre un événement reçu d'un équipement tiers (ex. netwatch du CRS).

        state : 'up' ou 'down' (accepte aussi normal/degrade/perdu/retabli).
        """
        source = (str(source or '').strip() or 'externe')[:40]
        probe = str(probe or '').strip()[:40]
        host = str(host or '').strip()[:64]
        text = str(text or '').strip()[:200]
        st = str(state or '').strip().lower()
        if st in ('up', 'normal', 'ok', 'retabli', 'rétabli', '1', 'true'):
            st = 'up'
        elif st in ('down', 'degrade', 'dégradé', 'perdu', 'ko', '0', 'false'):
            st = 'down'
        else:
            raise ValueError("state : 'up' ou 'down' attendu")
        if not probe:
            raise ValueError('probe : nom de la sonde requis')

        now = datetime.now().isoformat(timespec='seconds')
        key = (source, probe)
        with self._lock:
            prev = (self._external.get(key) or {}).get('state', 'unknown')
            self._external[key] = {'source': source, 'probe': probe, 'host': host,
                                   'state': st, 'text': text, 'ts': now}
            dev = None
            if host:
                for d in sorted(self._devices.values(),
                                key=lambda d: (d.get('kind') == 'probe', d['position'], d['id'])):
                    if self._device_host(d) == host:
                        dev = d
                        break
        payload = json.dumps({'probe': probe, 'host': host, 'text': text}, ensure_ascii=False)
        with self._conn() as c:
            c.execute('INSERT INTO chain_events (timestamp, device_id, device_name, from_state, to_state, message, source) '
                      'VALUES (?,?,?,?,?,?,?)',
                      (now, dev['id'] if dev else None, dev['name'] if dev else (host or probe),
                       prev, st, payload, source))
        level = logging.WARNING if st == 'down' else logging.INFO
        logger.log(level, f"Chaîne : {source}/{probe} ({host}) {prev} → {st} {text}")
        return {'device': dev['name'] if dev else None, 'state': st, 'previous': prev}

    def _external_for(self, host):
        if not host:
            return []
        items = [dict(v) for v in self._external.values() if v.get('host') == host]
        return sorted(items, key=lambda v: (v['source'], v['probe']))

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
                dev['kind'] = infer_kind(dev)
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
            'alert_sent': False,
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
        last_flush = time.time()
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
            if now - last_flush >= 5:
                last_flush = now
                self._flush_samples()
            self._check_traps()
            if now - last_cleanup > 3600:
                last_cleanup = now
                self._cleanup_events()
            time.sleep(1)
        self._flush_samples()

    def _check_traps(self):
        """Un trap SNMP reçu (service syslog) déclenche un relevé immédiat de l'appareil émetteur."""
        sys_db = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), 'syslog.db')
        if not os.path.exists(sys_db):
            return
        try:
            con = sqlite3.connect(sys_db, timeout=2)
            try:
                if self._trap_last_id is None:
                    row = con.execute("SELECT MAX(id) FROM logs").fetchone()
                    self._trap_last_id = row[0] or 0
                    return
                rows = con.execute("SELECT id, src FROM logs WHERE id > ? AND program = 'trap' ORDER BY id",
                                   (self._trap_last_id,)).fetchall()
                last = con.execute("SELECT MAX(id) FROM logs").fetchone()[0] or self._trap_last_id
            finally:
                con.close()
        except sqlite3.Error:
            return
        self._trap_last_id = last
        if not rows:
            return
        srcs = {r[1] for r in rows}
        with self._lock:
            targets = [d['id'] for d in self._devices.values() if self._device_host(d) in srcs and d.get('enabled', True)]
        for did in targets:
            self.trigger(did)
        if targets:
            logger.info(f"Chaîne : trap SNMP reçu, relevé immédiat de {len(targets)} appareil(s)")

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
        first_seen = False
        with self._lock:
            rt = self._runtime.get(did)
            if rt is None or did not in self._devices:
                return
            rt['last_check'] = now.isoformat(timespec='seconds')
            rt['result'] = res
            rt['history'].append([int(time.time() * 1000), res['latency_ms'] if res['ok'] else None, res['ok']])
            mets = [[m.get('label'), m.get('display'), m.get('unit', ''), m.get('state'),
                     m.get('value') if isinstance(m.get('value'), (int, float)) and not isinstance(m.get('value'), bool) else None]
                    for m in res.get('metrics') or []]
            self._samples.append((now.isoformat(timespec='milliseconds'), did, int(bool(res['ok'])), int(bool(res['warn'])),
                                  res['latency_ms'], res.get('message', ''),
                                  json.dumps(mets, ensure_ascii=False) if mets else None))
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
                bad = [f"{m.get('label')} = {m.get('display')}{(' ' + m['unit']) if m.get('unit') else ''}"
                       for m in res.get('metrics') or [] if m.get('state') == 'warn']
                event = (prev, new, res['message'] + (' : ' + ', '.join(bad) if bad else ''))
            elif rt['last_change'] is None:
                # Jamais de changement d'état connu : le premier relevé sert de point de départ
                rt['last_change'] = rt['last_check']
                first_seen = True

        if first_seen:
            try:
                with self._conn() as c:
                    c.execute('UPDATE chain_devices SET last_change=? WHERE id=? AND last_change IS NULL',
                              (now.isoformat(timespec='seconds'), did))
            except sqlite3.Error:
                pass
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
        # Même règle que les alertes FM : seul l'interrupteur « alertes email »
        # (email.alerts_enabled, page Configuration) décide ; send_alert le vérifie aussi.
        if alert is None or not getattr(alert, 'alerts_enabled', True):
            logger.info(f"Chaîne : email non envoyé pour {dev['name']} (alertes email coupées)")
            return
        where = f"{dev.get('site', '')} / {dev.get('role', '')}".strip(' /')
        target = (dev.get('config') or {}).get('host') or (dev.get('config') or {}).get('url') or ''
        details = f"Appareil : {dev['name']}\nEmplacement : {where}\nAdresse : {target}\nDétail : {msg}"
        with self._lock:
            rt = self._runtime.get(dev['id'])
        if rt is None:
            return
        bad = new == 'down' or (new == 'warn' and dev.get('alert_on_warn'))
        try:
            if bad and not rt['alert_sent']:
                if time.time() - rt['last_email'] < DOWN_EMAIL_MIN_INTERVAL:
                    return
                rt['last_email'] = time.time()
                rt['alert_sent'] = True
                what = 'injoignable' if new == 'down' else 'dégradé'
                threading.Thread(target=alert.send_alert,
                                 args=(f"Chaîne : {dev['name']} {what}", details),
                                 kwargs={'skip_cooldown': True}, daemon=True).start()
            elif not bad and rt['alert_sent'] and new in ('up', 'warn'):
                rt['alert_sent'] = False
                threading.Thread(target=alert.send_alert,
                                 args=(f"Chaîne : {dev['name']} rétabli", details),
                                 kwargs={'skip_cooldown': True}, daemon=True).start()
        except Exception as e:
            logger.error(f"Chaîne : envoi email impossible : {e}")

    def _flush_samples(self):
        with self._lock:
            batch, self._samples = self._samples, []
        if not batch:
            return
        try:
            with self._conn() as c:
                c.executemany('INSERT INTO chain_samples (ts, device_id, ok, warn, latency, message, metrics) '
                              'VALUES (?,?,?,?,?,?,?)', batch)
        except Exception as e:
            logger.error(f"Chaîne : écriture des mesures impossible ({len(batch)} perdues) : {e}")

    def _cleanup_events(self):
        limit = (datetime.now() - timedelta(days=EVENTS_RETENTION_DAYS)).isoformat(timespec='seconds')
        slimit = (datetime.now() - timedelta(days=SAMPLES_RETENTION_DAYS)).isoformat(timespec='seconds')
        try:
            with self._conn() as c:
                c.execute('DELETE FROM chain_events WHERE timestamp < ?', (limit,))
                c.execute('DELETE FROM chain_samples WHERE ts < ?', (slimit,))
        except Exception as e:
            logger.error(f"Chaîne : nettoyage du journal impossible : {e}")

    # ── Export pour analyse ──────────────────────────────────────────────
    def export_zip(self, start, end):
        """Archive ZIP (CSV) de tout ce qui s'est passé entre start et end (datetime locaux)."""
        if end <= start:
            raise ValueError('Période invalide')
        if end - start > timedelta(days=EXPORT_MAX_DAYS):
            raise ValueError(f'Période limitée à {EXPORT_MAX_DAYS} jours')
        self._flush_samples()
        s_iso, e_iso = start.isoformat(timespec='seconds'), end.isoformat(timespec='seconds')
        with self._lock:
            devs = {d['id']: d for d in self._devices.values()}

        def name(did):
            return devs[did]['name'] if did in devs else f'#{did}'

        def to_csv(header, rows):
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=';', lineterminator='\n')
            w.writerow(header)
            w.writerows(rows)
            return buf.getvalue().encode('utf-8-sig')   # BOM : s'ouvre correctement dans Excel/LibreOffice

        files = {}
        with self._conn() as c:
            # 1. changements d'état (appareils, sondes, événements externes)
            rows = c.execute('SELECT * FROM chain_events WHERE timestamp >= ? AND timestamp < ? ORDER BY id',
                             (s_iso, e_iso)).fetchall()
            ev = []
            for r in rows:
                msg, probe = r['message'], ''
                if r['source']:
                    try:
                        info = json.loads(msg)
                        msg, probe = info.get('text', ''), info.get('probe', '')
                    except (TypeError, ValueError):
                        pass
                ev.append([r['timestamp'], r['device_name'], r['source'] or 'BL-FMO', probe,
                           r['from_state'], r['to_state'], msg])
            files['evenements.csv'] = to_csv(['horodatage', 'appareil', 'source', 'sonde_externe',
                                              'etat_avant', 'etat_apres', 'detail'], ev)

            # 2. mesures brutes, une ligne par sondage
            rows = c.execute('SELECT * FROM chain_samples WHERE ts >= ? AND ts < ? ORDER BY ts',
                             (s_iso, e_iso)).fetchall()
            sm = []
            for r in rows:
                d = devs.get(r['device_id'], {})
                vals = ''
                if r['metrics']:
                    try:
                        vals = ' | '.join(f"{m[0]}={m[1]}{(' ' + m[2]) if m[2] else ''}{' (!)' if m[3] == 'warn' else ''}"
                                          for m in json.loads(r['metrics']))
                    except (TypeError, ValueError, IndexError):
                        vals = r['metrics']
                state = 'ok' if r['ok'] and not r['warn'] else ('alerte' if r['ok'] else 'echec')
                sm.append([r['ts'], name(r['device_id']), 'sonde' if d.get('kind') == 'probe' else 'appareil',
                           d.get('probe', ''), state,
                           '' if r['latency'] is None else str(r['latency']).replace('.', ','),
                           r['message'] or '', vals])
            files['mesures.csv'] = to_csv(['horodatage', 'nom', 'categorie', 'type_sonde', 'etat',
                                           'latence_ms', 'message', 'valeurs'], sm)

            # 3. alertes FM et niveaux audio de BL-FMO (tables existantes, si présentes)
            try:
                rows = c.execute("SELECT timestamp, alert_type, level_db, duration_seconds, message FROM alerts "
                                 "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                                 (s_iso.replace('T', ' '), e_iso.replace('T', ' '))).fetchall()
                files['alertes_fm.csv'] = to_csv(['horodatage', 'type', 'niveau_db', 'duree_s', 'message'],
                                                 [list(r) for r in rows])
            except sqlite3.Error:
                pass
            try:
                rows = c.execute("SELECT datetime(timestamp, 'localtime') AS t, level_db, signal_ok FROM audio_levels "
                                 "WHERE datetime(timestamp, 'localtime') >= ? AND datetime(timestamp, 'localtime') < ? "
                                 "ORDER BY timestamp",
                                 (s_iso.replace('T', ' '), e_iso.replace('T', ' '))).fetchall()
                files['niveaux_fm.csv'] = to_csv(['horodatage', 'niveau_dbfs', 'signal_ok'],
                                                 [[r[0], str(r[1]).replace('.', ','), r[2]] for r in rows])
            except sqlite3.Error:
                pass

        # 4. journaux reçus des appareils (syslog), si le service de réception est installé
        n_sys = 0
        sys_db = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), 'syslog.db')
        if os.path.exists(sys_db):
            try:
                from syslog_server import SyslogStore, SEVERITIES
                hosts = {}
                for d in sorted(devs.values(), key=lambda d: d.get('kind') == 'probe'):
                    h = self._device_host(d)
                    if h and h not in hosts:
                        hosts[h] = d['name']
                sl = []
                for r in SyslogStore(sys_db).iter_rows(start=s_iso, end=e_iso):
                    sev = r['severity'] if isinstance(r['severity'], int) and 0 <= r['severity'] < 8 else 6
                    sl.append([r['ts'], SEVERITIES[sev], hosts.get(r['src'], r['host'] or r['src']),
                               r['src'], r['program'], r['message']])
                    if len(sl) >= 500000:
                        break
                n_sys = len(sl)
                files['journaux.csv'] = to_csv(['horodatage', 'niveau', 'appareil', 'adresse', 'programme', 'message'], sl)
            except Exception as e:
                logger.warning(f"Export : journaux syslog non inclus ({e})")

        # 5. configuration des appareils, sans secrets
        conf = []
        for d in sorted(devs.values(), key=lambda d: (d.get('kind') == 'probe', d['position'], d['id'])):
            d = public_device(d)
            cfg = d.get('config') or {}
            if cfg.get('community'):
                cfg['community'] = SECRET_MASK
            conf.append(d)
        files['appareils.json'] = json.dumps(conf, ensure_ascii=False, indent=2).encode('utf-8')

        files['LISEZMOI.txt'] = (
            f"Export BL-FMO, chaîne de diffusion\n"
            f"Période : {start:%d/%m/%Y %H:%M:%S} au {end:%d/%m/%Y %H:%M:%S} (heure locale)\n"
            f"Généré le {datetime.now():%d/%m/%Y %H:%M:%S}\n\n"
            "evenements.csv  changements d'état (appareils, sondes, événements externes)\n"
            "mesures.csv     une ligne par sondage : état, latence, valeurs lues (SNMP, pertes, gigue...)\n"
            "                (!) signale une valeur hors seuil\n"
            "alertes_fm.csv  alertes de la réception FM (signal, modulation, RDS...)\n"
            "niveaux_fm.csv  niveau audio FM reçu, toutes les 5 s\n"
            "journaux.csv    journaux envoyés par les appareils (syslog : switchs, faisceaux...)\n"
            "appareils.json  configuration des appareils et sondes (communautés et mots de passe masqués)\n\n"
            "Fichiers CSV séparés par des points-virgules, encodés en UTF-8.\n"
        ).encode('utf-8')

        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for fname, data in files.items():
                z.writestr(fname, data)
        return out.getvalue(), {k: v for k, v in (('evenements', len(ev)), ('mesures', len(sm)), ('journaux', n_sys))}

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
                    'kind': d.get('kind', 'device'),
                    'name': d['name'],
                    'site': d.get('site', ''),
                    'role': d.get('role', ''),
                    'probe': d['probe'],
                    'target': cfg.get('host') or cfg.get('url') or '',
                    'host': self._device_host(d),
                    'out_label': d.get('out_label', ''),
                    'web': d.get('web', ''),
                    'link_from': d.get('link_from'),
                    'link_to': d.get('link_to'),
                    'settings': self._settings_summary(d),
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
                    'external': self._external_for(self._device_host(d)) if d.get('kind') != 'probe' else [],
                })
        return out

    @staticmethod
    def _settings_summary(d):
        """Résumé lisible des réglages (onglet Réglages du synoptique), sans secrets."""
        cfg = d.get('config') or {}
        p = d['probe']
        out = []
        if p == 'snmp':
            if cfg.get('version') == '3':
                sec = {'noAuthNoPriv': 'sans authentification', 'authNoPriv': f"authentification {cfg.get('auth_proto', '')}",
                       'authPriv': f"authentification {cfg.get('auth_proto', '')}, chiffrement {cfg.get('priv_proto', '')}"}.get(cfg.get('level'), '')
                out.append(['Sonde', f"SNMP v3, port {cfg.get('port', 161)}, {sec}"])
            else:
                out.append(['Sonde', f"SNMP v{cfg.get('version', '2c')}, port {cfg.get('port', 161)}"])
            seuils = []
            for m in cfg.get('metrics') or []:
                if m.get('min') is not None:
                    seuils.append(f"{m['label']} min {_fmt_number(m['min'])} {m.get('unit', '')}".strip())
                if m.get('max') is not None:
                    seuils.append(f"{m['label']} max {_fmt_number(m['max'])} {m.get('unit', '')}".strip())
                if m.get('expect'):
                    seuils.append(f"{m['label']} = {m['expect']}")
            out.append(['Seuils', ', '.join(seuils) or 'aucun'])
        elif p == 'burst':
            out.append(['Sonde', f"Ping qualité vers {cfg.get('host')}"])
            out.append(['Rafale', f"{cfg.get('count')} pings espacés de {cfg.get('spacing_ms')} ms"])
            s = [f"pertes max {_fmt_number(cfg.get('loss_pct', 5))} %", f"latence max {_fmt_number(cfg.get('max_ms', 150))} ms"]
            if cfg.get('jitter_ms'):
                s.append(f"gigue max {_fmt_number(cfg['jitter_ms'])} ms")
            out.append(['Seuils', ', '.join(s)])
        elif p == 'ping':
            out.append(['Sonde', f"Ping vers {cfg.get('host')}"])
        elif p == 'tcp':
            out.append(['Sonde', f"Port TCP {cfg.get('port')} sur {cfg.get('host')}"])
        elif p in ('http', 'icecast'):
            out.append(['Sonde', f"{'Page web' if p == 'http' else 'Icecast'} : {cfg.get('url')}"])
        elif p == 'fm_local':
            out.append(['Sonde', 'Récepteur FM local'])
        out.append(['Intervalle', f"{d.get('interval', 30)} s"])
        out.append(['Échecs avant hors service', str(d.get('fail_threshold', 3))])
        mail = 'Non'
        if d.get('alert_email'):
            mail = 'Panne et alerte' if d.get('alert_on_warn') else 'Panne seulement'
        out.append(['Alerte email', mail])
        if d.get('notes'):
            out.append(['Notes', d['notes']])
        return out

    def numeric_labels(self, device_id):
        """Valeurs qui peuvent faire une courbe (nombres), dans l'ordre de l'appareil."""
        with self._lock:
            d = self._devices.get(device_id)
            rt = self._runtime.get(device_id)
            if not d:
                return []
            res = (rt or {}).get('result') or {}
        labels = []
        if d['probe'] == 'snmp':
            labels = [m['label'] for m in (d.get('config') or {}).get('metrics') or [] if m.get('format') == 'number']
        else:
            labels = [m.get('label') for m in res.get('metrics') or []
                      if isinstance(m.get('value'), (int, float)) and not isinstance(m.get('value'), bool)]
            if d['probe'] in ('ping', 'tcp', 'http'):
                labels.insert(0, 'Latence')
        return labels

    def metric_history(self, device_id, label, hours=24, buckets=240):
        """Courbe d'une valeur : liste [horodatage ms, moyenne, min, max] par tranche de temps."""
        hours = max(1, min(float(hours), SAMPLES_RETENTION_DAYS * 24))
        start = datetime.now() - timedelta(hours=hours)
        self._flush_samples()
        with self._conn() as c:
            rows = c.execute('SELECT ts, latency, metrics FROM chain_samples WHERE device_id=? AND ts >= ? ORDER BY ts',
                             (device_id, start.isoformat(timespec='seconds'))).fetchall()
        num = re.compile(r'-?\d+(?:\.\d+)?')
        pts = []
        for r in rows:
            v = None
            if label == 'Latence':
                v = r['latency']
            elif r['metrics']:
                try:
                    for m in json.loads(r['metrics']):
                        if m[0] == label:
                            if len(m) > 4 and m[4] is not None:
                                v = float(m[4])
                            else:
                                g = num.search(str(m[1]).replace('\u2212', '-'))
                                v = float(g.group(0)) if g else None
                            break
                except (TypeError, ValueError, IndexError):
                    v = None
            if v is not None:
                pts.append((datetime.fromisoformat(r['ts']).timestamp() * 1000, v))
        if not pts:
            return []
        t0 = start.timestamp() * 1000
        width = hours * 3600 * 1000 / buckets
        acc = {}
        for t, v in pts:
            b = int((t - t0) // width)
            a = acc.setdefault(b, [0.0, 0, v, v])
            a[0] += v
            a[1] += 1
            a[2] = min(a[2], v)
            a[3] = max(a[3], v)
        return [[int(t0 + (b + 0.5) * width), round(a[0] / a[1], 3), a[2], a[3]] for b, a in sorted(acc.items())]

    def device_logs(self, device_id, limit=50):
        """Derniers journaux syslog reçus de l'appareil (si le service de réception est installé)."""
        d = self.get_device(device_id)
        sys_db = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), 'syslog.db')
        if not d or not os.path.exists(sys_db):
            return {'installed': os.path.exists(sys_db), 'host': d and self._device_host(d), 'rows': []}
        from syslog_server import SyslogStore
        store = SyslogStore(sys_db)
        server_ip = store.meta().get('ip', '')
        host = self._device_host(d)
        if not host:
            return {'installed': True, 'host': '', 'rows': [], 'server_ip': server_ip}
        rows, _total = store.search(limit=limit, src=host)
        return {'installed': True, 'host': host, 'rows': rows, 'server_ip': server_ip}

    def get_events(self, limit=100, device_id=None):
        q = 'SELECT * FROM chain_events'
        args = []
        if device_id:
            q += ' WHERE device_id=?'
            args.append(device_id)
        q += ' ORDER BY id DESC LIMIT ?'
        args.append(max(1, min(int(limit), 1000)))
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, args).fetchall()]
        for r in rows:
            if r.get('source'):
                try:
                    info = json.loads(r['message'])
                    r['probe'] = info.get('probe', '')
                    r['message'] = info.get('text') or ''
                except (TypeError, ValueError):
                    pass
        return rows
