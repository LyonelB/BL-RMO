"""Surveillance du réseau Dante, à partir des relevés de la sonde Dante.

La sonde (install/windows/sonde-dante.py) tourne sur un PC qui voit le réseau Dante,
lit l'état des appareils avec netaudio en lecture seule (« status » et « clock ») et
l'envoie toutes les 60 s à POST /api/dante/report, protégé par un jeton.

Ce module garde le dernier relevé, en tire un état lisible (appareils, abonnements,
horloge) et le compare à une référence : un appareil ou un abonnement vu une fois et
qui disparaît est signalé. La référence s'apprend seule (tout ce qui apparaît y entre)
et se recale depuis la page Dante (« Prendre l'état actuel comme référence »).

Stockage : table dante_kv de fm_monitor.db (jeton, dernier relevé, référence).
"""
import json
import logging
import secrets
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

STALE_AFTER = 180          # s sans relevé : sonde muette
PRODUCTS = {'DvsWin': 'Dante Virtual Soundcard', 'DvsMac': 'Dante Virtual Soundcard', 'DVS': 'Dante Virtual Soundcard',
            'DanteVia': 'Dante Via'}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _product(d):
    """Nom lisible du matériel : Dante Virtual Soundcard, Audinate DCM, ULTIMO4..."""
    model = d.get('model_id') or ''
    if model in PRODUCTS:
        return PRODUCTS[model]
    for svc in (d.get('services') or {}).values():
        info = ((svc or {}).get('properties') or {}).get('router_info')
        if info:
            return info.replace(' for Windows', '').replace(' for macOS', '')
    return model.strip('_') or '-'


def parse_report(rep):
    """Relevé brut de la sonde -> {devices, subscriptions, leaders, errors}."""
    status = (rep.get('status') or {}).get('dante') or {}
    clock = rep.get('clock') or {}
    clock_by_name = {}
    for c in clock.values():
        if isinstance(c, dict) and c.get('name'):
            clock_by_name[c['name']] = c
    devices, subs = [], []
    for d in status.values():
        if not isinstance(d, dict) or not d.get('name'):
            continue
        name = d['name']
        ck = clock_by_name.get(name) or {}
        cs = ck.get('clock_status') or {}
        audio = _num(d.get('rx_count')) is not None or _num(d.get('tx_count')) is not None
        devices.append({
            'name': name, 'ip': d.get('ipv4') or '', 'manufacturer': d.get('manufacturer') or '',
            'product': _product(d), 'online': bool(d.get('online')) and d.get('availability_state', 'online') == 'online',
            'audio': audio,                      # faux : appareil annoncé qui ne répond pas aux lectures
            'latency_ms': _num(d.get('active_latency_ms')), 'sample_rate': _num(d.get('sample_rate_hz')),
            'rx': _num(d.get('rx_count')), 'tx': _num(d.get('tx_count')),
            'clock_role': ck.get('role') or '', 'clock_source': cs.get('clock_source') or '',
            'clock_state': cs.get('clock_state') or '', 'word_clock': cs.get('word_clock_state') or '',
            'clock_muted': cs.get('mute_state') not in (None, '', 'unmuted'),
            'since': (d.get('presence') or {}).get('since'),
        })
        for s in d.get('subscriptions') or []:
            st = s.get('status') or {}
            if (st.get('severity') or 'none') == 'none' and not s.get('tx_device'):
                continue                         # canal sans abonnement
            subs.append({
                'rx_device': s.get('rx_device') or name, 'rx_channel': s.get('rx_channel') or '',
                'rx_number': s.get('rx_channel_number'), 'tx_device': s.get('tx_device') or '',
                'tx_channel': s.get('tx_channel') or '', 'severity': st.get('severity') or 'none',
                'state': st.get('state') or '', 'label': st.get('label') or '', 'detail': st.get('detail') or '',
                'transport': st.get('transport') or '',
            })
    devices.sort(key=lambda x: x['name'].lower())
    subs.sort(key=lambda x: (x['rx_device'].lower(), x['rx_number'] or 0))
    leaders = sorted(d['name'] for d in devices if d['clock_role'].lower() == 'leader')
    return {'devices': devices, 'subscriptions': subs, 'leaders': leaders, 'errors': rep.get('errors') or []}


def sub_key(s):
    return f"{s['rx_device']}|{s['rx_number']}"


class DanteMonitor:
    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        with self._db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS dante_kv (key TEXT PRIMARY KEY, value TEXT)")
        self.report = self._get('report') or None          # {received, probe, ts, duration, parsed}
        self.baseline = self._get('baseline') or {'devices': {}, 'subscriptions': {}, 'leader': ''}

    # ── stockage ──────────────────────────────────────────────────────────
    def _db(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def _get(self, key):
        with self._db() as c:
            row = c.execute("SELECT value FROM dante_kv WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return row[0]

    def _set(self, key, value):
        with self._db() as c:
            c.execute("INSERT OR REPLACE INTO dante_kv (key, value) VALUES (?, ?)",
                      (key, value if isinstance(value, str) else json.dumps(value)))

    # ── jeton de la sonde ────────────────────────────────────────────────
    def get_token(self):
        token = self._get('token')
        if not token:
            token = secrets.token_urlsafe(24)
            self._set('token', token)
        return token

    def regenerate_token(self):
        token = secrets.token_urlsafe(24)
        self._set('token', token)
        return token

    def check_token(self, token):
        return bool(token) and secrets.compare_digest(str(token), self.get_token())

    # ── relevés ──────────────────────────────────────────────────────────
    def record(self, rep, remote):
        parsed = parse_report(rep)
        now = time.time()
        with self.lock:
            # Référence : tout ce qui apparaît y entre ; rien n'en sort tout seul
            b = self.baseline
            changed = False
            for d in parsed['devices']:
                if d['name'] not in b['devices']:
                    b['devices'][d['name']] = {'first_seen': now}
                    changed = True
                b['devices'][d['name']]['last_seen'] = now
            for s in parsed['subscriptions']:
                if s['severity'] == 'none':
                    continue
                k = sub_key(s)
                ref = b['subscriptions'].get(k)
                if not ref or ref.get('tx') != f"{s['tx_device']}|{s['tx_channel']}":
                    b['subscriptions'][k] = {'tx': f"{s['tx_device']}|{s['tx_channel']}",
                                             'rx_channel': s['rx_channel'], 'first_seen': now}
                    changed = True
            if not b.get('leader') and len(parsed['leaders']) == 1:
                b['leader'] = parsed['leaders'][0]
                changed = True
            self.report = {'received': now, 'remote': remote, 'probe': rep.get('probe') or {},
                           'ts': rep.get('ts'), 'duration': rep.get('duration'), 'parsed': parsed}
            self._set('report', self.report)
            self._set('baseline', b)
        if changed:
            logger.info("Dante : référence complétée (%d appareils, %d abonnements)",
                        len(b['devices']), len(b['subscriptions']))
        return {'devices': len(parsed['devices']), 'subscriptions': len(parsed['subscriptions'])}

    def reset_baseline(self):
        """L'état actuel devient la référence (après un changement voulu de routage)."""
        with self.lock:
            parsed = (self.report or {}).get('parsed') or {'devices': [], 'subscriptions': [], 'leaders': []}
            now = time.time()
            self.baseline = {
                'devices': {d['name']: {'first_seen': now, 'last_seen': now} for d in parsed['devices'] if d['online']},
                'subscriptions': {sub_key(s): {'tx': f"{s['tx_device']}|{s['tx_channel']}", 'rx_channel': s['rx_channel'],
                                               'first_seen': now}
                                  for s in parsed['subscriptions'] if s['severity'] not in ('none',)},
                'leader': parsed['leaders'][0] if len(parsed['leaders']) == 1 else '',
            }
            self._set('baseline', self.baseline)

    @staticmethod
    def _baseline_info(b):
        return {'devices': len(b['devices']), 'subscriptions': len(b['subscriptions']), 'leader': b.get('leader') or ''}

    # ── état pour la page ────────────────────────────────────────────────
    def status(self):
        with self.lock:
            rep = self.report
            b = json.loads(json.dumps(self.baseline))
        now = time.time()
        if not rep:
            return {'state': 'waiting', 'problems': [], 'devices': [], 'subscriptions': [], 'leaders': [],
                    'baseline': self._baseline_info(b), 'report': None, 'now': now}
        p = rep['parsed']
        age = now - rep['received']
        problems = []                              # [{level, kind, text, values}]
        if age > STALE_AFTER:
            problems.append({'level': 'down', 'kind': 'probe', 'text': 'Sonde muette depuis {t} min',
                             'values': {'t': int(age // 60)}})
        for e in p.get('errors') or []:
            problems.append({'level': 'warn', 'kind': 'probe', 'text': 'Lecture incomplète : {e}', 'values': {'e': e}})

        devices = [dict(d) for d in p['devices']]
        present = {d['name'] for d in devices if d['online']}
        for d in devices:
            d['state'] = 'ok' if d['online'] else 'down'
            if d['online'] and not d['audio']:
                d['state'] = 'muted'               # annoncé, ne répond pas aux lectures
        for name, ref in b['devices'].items():
            if name not in present:
                if not any(d['name'] == name for d in devices):
                    devices.append({'name': name, 'state': 'down', 'online': False, 'missing': True,
                                    'last_seen': ref.get('last_seen'), 'product': '', 'ip': ''})
                problems.append({'level': 'down', 'kind': 'device', 'text': '{n} absent du réseau Dante',
                                 'values': {'n': name}})
        devices.sort(key=lambda x: x['name'].lower())

        subs = [dict(s) for s in p['subscriptions'] if s['severity'] != 'none']
        seen = {sub_key(s): s for s in subs}
        for s in subs:
            s['state_ui'] = 'ok' if s['severity'] == 'ok' else ('warn' if s['severity'] in ('warning', 'info') else 'down')
            if s['state_ui'] != 'ok':
                problems.append({'level': s['state_ui'], 'kind': 'subscription',
                                 'text': '{rx} · {ch} : {l}', 'values': {'rx': s['rx_device'], 'ch': s['rx_channel'],
                                                                       'l': s['label']}})
        for k, ref in b['subscriptions'].items():
            if k in seen:
                continue
            rx_dev, rx_num = k.split('|', 1)
            tx_dev, tx_ch = (ref.get('tx') or '|').split('|', 1)
            subs.append({'rx_device': rx_dev, 'rx_number': int(rx_num) if rx_num.isdigit() else None,
                         'rx_channel': ref.get('rx_channel') or rx_num, 'tx_device': tx_dev, 'tx_channel': tx_ch,
                         'state_ui': 'down', 'missing': True, 'label': '', 'transport': ''})
            problems.append({'level': 'down', 'kind': 'subscription', 'text': '{rx} · {ch} ne reçoit plus {tx}',
                             'values': {'rx': rx_dev, 'ch': ref.get('rx_channel') or rx_num, 'tx': f'{tx_dev} · {tx_ch}'}})
        subs.sort(key=lambda x: (x['rx_device'].lower(), x.get('rx_number') or 0))

        leaders = p['leaders']
        if not leaders and devices:
            problems.append({'level': 'down', 'kind': 'clock', 'text': "Aucun leader d'horloge", 'values': {}})
        elif len(leaders) > 1:
            problems.append({'level': 'warn', 'kind': 'clock', 'text': "Plusieurs leaders d'horloge : {l}",
                             'values': {'l': ', '.join(leaders)}})
        elif b.get('leader') and leaders[0] != b['leader']:
            problems.append({'level': 'warn', 'kind': 'clock', 'text': "Leader d'horloge changé : {n} au lieu de {r}",
                             'values': {'n': leaders[0], 'r': b['leader']}})
        for d in devices:
            if d.get('clock_muted'):
                problems.append({'level': 'warn', 'kind': 'clock', 'text': '{n} : horloge non synchronisée, audio coupé',
                                 'values': {'n': d['name']}})

        levels = {x['level'] for x in problems}
        state = 'down' if 'down' in levels else 'warn' if 'warn' in levels else 'ok'
        return {'state': state, 'problems': problems, 'devices': devices, 'subscriptions': subs,
                'leaders': leaders, 'baseline': self._baseline_info(b), 'now': now,
                'report': {'received': rep['received'], 'age': age, 'probe': rep.get('probe') or {},
                           'remote': rep.get('remote'), 'duration': rep.get('duration')}}
