#!/usr/bin/env python3
"""
syslog_server.py : réception des journaux (syslog) des appareils de la radio.

Remplace le Centre des journaux du NAS : les switchs, faisceaux, codecs,
émetteur... envoient leurs journaux à BL-FMO, qui les range dans syslog.db
et les affiche sur la page /logs.

Deux usages :
  1. Service autonome (fm-monitor-syslog), lancé par systemd :
         python3 syslog_server.py
     Écoute en UDP (514 par défaut). Indépendant de fm-monitor : les journaux
     continuent d'arriver pendant un redémarrage de l'application.
  2. Module importé par app.py (SyslogStore) : recherche, export, état.

Formats acceptés : RFC 3164 (BSD, MikroTik, Ubiquiti, la plupart des
appareils) et RFC 5424. L'horodatage retenu est celui de la réception par
BL-FMO : les horloges des appareils ne sont pas toutes à l'heure.

Réglages (config.json, section « syslog », tous facultatifs) :
    "syslog": {"port": 514, "bind": "0.0.0.0", "retention_days": 90}
"""
import json
import logging
import os
import re
import signal
import socket
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
DB_FILE = os.path.join(BASE_DIR, 'syslog.db')

DEFAULTS = {
    'port': 514,
    'bind': '0.0.0.0',
    'retention_days': 90,
    'max_rows': 3_000_000,      # garde-fou disque (≈ 600 Mo) en cas d'appareil trop bavard
    'trap_port': 162,           # traps SNMP (0 : désactivé)
    'trap_community': '',       # vide : traps acceptés quelle que soit la communauté
}

SEVERITIES = ['Urgence', 'Alerte', 'Critique', 'Erreur', 'Avertissement', 'Notice', 'Info', 'Debug']
FACILITIES = ['kern', 'user', 'mail', 'daemon', 'auth', 'syslog', 'lpr', 'news', 'uucp', 'cron',
              'authpriv', 'ftp', 'ntp', 'audit', 'alert', 'clock',
              'local0', 'local1', 'local2', 'local3', 'local4', 'local5', 'local6', 'local7']

logger = logging.getLogger('syslog')


def load_config(config_file=CONFIG_FILE):
    cfg = dict(DEFAULTS)
    try:
        with open(config_file) as f:
            cfg.update(json.load(f).get('syslog', {}) or {})
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Lecture config syslog impossible ({e}), valeurs par défaut")
    return cfg


# ════════════════════════════════════════════════════════════════════
#  Décodage
# ════════════════════════════════════════════════════════════════════

_PRI = re.compile(r'^<(\d{1,3})>')
_TS_3164 = re.compile(r'^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})\s+')
_TS_ISO = re.compile(r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+')
_TAG = re.compile(r'^([^\s:\[\]]{1,48})(?:\[(\d+)\])?:?\s+')
_HOSTNAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$')


def parse(data, src_ip):
    """Décode un message syslog. Ne lève jamais : un message illisible est gardé brut."""
    text = data.decode('utf-8', errors='replace').replace('\x00', '').strip()
    if text.startswith('\ufeff'):
        text = text[1:]
    rec = {'src': src_ip, 'host': '', 'facility': 1, 'severity': 5, 'program': '', 'message': text}

    m = _PRI.match(text)
    if m:
        pri = int(m.group(1))
        if pri <= 191:
            rec['facility'], rec['severity'] = pri >> 3, pri & 7
        text = text[m.end():]

    # RFC 5424 : <PRI>1 HORODATAGE HÔTE APPLI PROCID MSGID [SD] MESSAGE
    if text.startswith('1 '):
        parts = text[2:].split(' ', 5)
        if len(parts) >= 5:
            _, host, app, _procid, _msgid = parts[:5]
            rest = parts[5] if len(parts) > 5 else ''
            if rest.startswith('['):                    # données structurées : on les saute
                depth, i = 0, 0
                for i, ch in enumerate(rest):
                    if ch == '[':
                        depth += 1
                    elif ch == ']':
                        depth -= 1
                        if depth == 0 and (i + 1 == len(rest) or rest[i + 1] == ' '):
                            break
                rest = rest[i + 1:]
            elif rest.startswith('- '):
                rest = rest[2:]
            elif rest == '-':
                rest = ''
            rec['host'] = '' if host == '-' else host
            rec['program'] = '' if app == '-' else app
            rec['message'] = rest.strip()
            return rec

    # RFC 3164 : [HORODATAGE] [HÔTE] ÉTIQUETTE[pid]: MESSAGE
    had_ts = False
    m = _TS_3164.match(text) or _TS_ISO.match(text)
    if m:
        had_ts = True
        text = text[m.end():]

    if had_ts:
        # Après l'horodatage vient le nom d'hôte, sauf s'il se termine par « : » (c'est alors l'étiquette)
        first, _, after = text.partition(' ')
        if first and not first.endswith(':') and '[' not in first and _HOSTNAME.match(first) and after:
            rec['host'] = first
            text = after

    m = _TAG.match(text) if (had_ts or rec['message'] != text) else None   # pas d'étiquette dans un texte brut
    if m and len(text) > m.end():
        rec['program'] = m.group(1)
        text = text[m.end():]
    rec['message'] = text.strip()
    return rec


def local_ip(bind='0.0.0.0'):
    """Adresse IP de BL-FMO sur le réseau local (à indiquer aux appareils).
    Les appareils embarqués ne résolvent pas les noms en .local : il leur faut l'IP."""
    if bind and bind not in ('0.0.0.0', '::'):
        return bind
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 9))      # aucun paquet n'est envoyé : choix de la route seulement
            return s.getsockname()[0]
    except OSError:
        return ''


# ════════════════════════════════════════════════════════════════════
#  Stockage
# ════════════════════════════════════════════════════════════════════

class SyslogStore:
    def __init__(self, db_path=DB_FILE):
        self.db_path = db_path
        self._init_db()

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
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('''CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                src TEXT,
                host TEXT,
                facility INTEGER,
                severity INTEGER,
                program TEXT,
                message TEXT
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_logs_src ON logs(src, ts)')
            c.execute('''CREATE TABLE IF NOT EXISTS sources (
                src TEXT PRIMARY KEY,
                host TEXT,
                first_seen TEXT,
                last_seen TEXT,
                count INTEGER DEFAULT 0
            )''')
            c.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')

    # ── Écriture (service de réception) ─────────────────────────────────
    def insert_many(self, recs):
        if not recs:
            return
        with self._conn() as c:
            c.executemany('INSERT INTO logs (ts, src, host, facility, severity, program, message) '
                          'VALUES (:ts, :src, :host, :facility, :severity, :program, :message)', recs)
            per_src = {}
            for r in recs:
                p = per_src.setdefault(r['src'], {'host': '', 'first': r['ts'], 'ts': r['ts'], 'n': 0})
                p['n'] += 1
                p['first'] = min(p['first'], r['ts'])
                p['ts'] = max(p['ts'], r['ts'])
                if r['host']:
                    p['host'] = r['host']
            for src, p in per_src.items():
                c.execute('INSERT INTO sources (src, host, first_seen, last_seen, count) VALUES (?,?,?,?,?) '
                          'ON CONFLICT(src) DO UPDATE SET last_seen=MAX(last_seen, excluded.last_seen), '
                          'first_seen=MIN(first_seen, excluded.first_seen), count=count+excluded.count, '
                          "host=CASE WHEN excluded.host != '' THEN excluded.host ELSE sources.host END",
                          (src, p['host'], p['first'], p['ts'], p['n']))

    def set_meta(self, **kv):
        with self._conn() as c:
            for k, v in kv.items():
                c.execute('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                          (k, json.dumps(v)))

    def purge(self, retention_days, max_rows):
        limit = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec='seconds')
        with self._conn() as c:
            n = c.execute('DELETE FROM logs WHERE ts < ?', (limit,)).rowcount
            total = c.execute('SELECT COUNT(*) FROM logs').fetchone()[0]
            if total > max_rows:
                n += c.execute('DELETE FROM logs WHERE id <= (SELECT id FROM logs ORDER BY id LIMIT 1 OFFSET ?)',
                               (total - max_rows,)).rowcount
        return n

    # ── Lecture (application web) ───────────────────────────────────────
    def meta(self):
        with self._conn() as c:
            return {r['key']: json.loads(r['value']) for r in c.execute('SELECT key, value FROM meta')}

    def sources(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM sources ORDER BY last_seen DESC')]

    @staticmethod
    def _where(src=None, max_severity=None, q=None, start=None, end=None, program=None):
        cond, args = [], []
        if src:
            srcs = [s for s in (src if isinstance(src, (list, tuple)) else [src]) if s]
            if srcs:
                cond.append(f"src IN ({','.join('?' * len(srcs))})")
                args += srcs
        if max_severity is not None:
            cond.append('severity <= ?')
            args.append(int(max_severity))
        if program:
            cond.append('program = ?')
            args.append(program)
        if q:
            for word in q.split()[:5]:
                cond.append("(message LIKE ? ESCAPE '\\' OR program LIKE ? ESCAPE '\\' OR host LIKE ? ESCAPE '\\')")
                w = '%' + word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                args += [w, w, w]
        if start:
            cond.append('ts >= ?')
            args.append(start)
        if end:
            cond.append('ts < ?')
            args.append(end)
        return (' WHERE ' + ' AND '.join(cond)) if cond else '', args

    def search(self, limit=200, offset=0, **filters):
        where, args = self._where(**filters)
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        with self._conn() as c:
            total = c.execute(f'SELECT COUNT(*) FROM logs{where}', args).fetchone()[0]
            rows = [dict(r) for r in c.execute(
                f'SELECT * FROM logs{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?', args + [limit, offset])]
        return rows, total

    def iter_rows(self, **filters):
        """Pour l'export : du plus ancien au plus récent."""
        where, args = self._where(**filters)
        with self._conn() as c:
            for r in c.execute(f'SELECT * FROM logs{where} ORDER BY ts, id', args):
                yield dict(r)


# ════════════════════════════════════════════════════════════════════
#  Service de réception
# ════════════════════════════════════════════════════════════════════

class Receiver:
    FLUSH_S = 1.0
    MAX_QUEUE = 50_000

    def __init__(self, cfg):
        self.cfg = cfg
        self.store = SyslogStore()
        self.queue = []
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.received = 0
        self.dropped = 0
        self.traps = 0
        self.started = datetime.now().isoformat(timespec='seconds')

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        try:
            sock.bind((self.cfg['bind'], int(self.cfg['port'])))
        except PermissionError:
            logger.error(f"Port {self.cfg['port']} refusé : il faut la capacité CAP_NET_BIND_SERVICE "
                         "(voir install/build/66-syslog.sh)")
            sys.exit(1)
        sock.settimeout(1.0)
        logger.info(f"Réception syslog sur UDP {self.cfg['bind']}:{self.cfg['port']}, "
                    f"conservation {self.cfg['retention_days']} jours")
        threading.Thread(target=self._writer, daemon=True).start()
        if int(self.cfg.get('trap_port') or 0):
            threading.Thread(target=self._traps, daemon=True).start()
        while not self.stop_evt.is_set():
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError as e:
                logger.warning(f"Réception : {e}")
                continue
            rec = parse(data, addr[0])
            rec['ts'] = datetime.now().isoformat(timespec='milliseconds')
            with self.lock:
                if len(self.queue) < self.MAX_QUEUE:
                    self.queue.append(rec)
                    self.received += 1
                else:
                    self.dropped += 1
        sock.close()
        self._flush()

    def _queue(self, rec):
        with self.lock:
            if len(self.queue) < self.MAX_QUEUE:
                self.queue.append(rec)
                return True
            self.dropped += 1
            return False

    def _traps(self):
        """Réception des traps SNMP (v1, v2c) : une ligne de journal par trap, programme « trap »."""
        from snmp_traps import decode, translate, inform_response, TrapError
        port = int(self.cfg['trap_port'])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.cfg['bind'], port))
        except OSError as e:
            logger.error(f"Traps SNMP : port UDP {port} indisponible ({e}), réception des traps désactivée")
            return
        sock.settimeout(1.0)
        want = str(self.cfg.get('trap_community') or '')
        logger.info(f"Réception des traps SNMP sur UDP {self.cfg['bind']}:{port}")
        while not self.stop_evt.is_set():
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                continue
            ts = datetime.now().isoformat(timespec='milliseconds')
            try:
                trap = decode(data)
            except (TrapError, IndexError, ValueError) as e:
                self._queue({'ts': ts, 'src': addr[0], 'host': '', 'facility': 1, 'severity': 4,
                             'program': 'trap', 'message': f'Trap illisible : {e}'})
                continue
            if want and trap['community'] != want:
                logger.warning(f"Trap refusé de {addr[0]} : communauté inattendue")
                continue
            if trap['inform']:
                try:
                    resp = inform_response(data)
                    if resp:
                        sock.sendto(resp, addr)
                except (OSError, TrapError, IndexError):
                    pass
            sev, alias, msg = translate(trap)
            if self._queue({'ts': ts, 'src': addr[0], 'host': alias, 'facility': 1, 'severity': sev,
                            'program': 'trap', 'message': msg}):
                self.traps += 1
        sock.close()

    def _flush(self):
        with self.lock:
            batch, self.queue = self.queue, []
        if batch:
            try:
                self.store.insert_many(batch)
            except sqlite3.Error as e:
                logger.error(f"Écriture impossible ({len(batch)} messages perdus) : {e}")

    def _writer(self):
        last_meta = last_purge = 0
        while not self.stop_evt.is_set():
            time.sleep(self.FLUSH_S)
            self._flush()
            now = time.time()
            if now - last_meta >= 15:
                last_meta = now
                try:
                    self.store.set_meta(heartbeat=datetime.now().isoformat(timespec='seconds'),
                                        started=self.started, port=int(self.cfg['port']),
                                        ip=local_ip(self.cfg.get('bind')),
                                        received=self.received, dropped=self.dropped,
                                        trap_port=int(self.cfg.get('trap_port') or 0), traps=self.traps,
                                        retention_days=self.cfg['retention_days'])
                except sqlite3.Error as e:
                    logger.warning(f"État non enregistré : {e}")
            if now - last_purge >= 3600:
                last_purge = now
                try:
                    n = self.store.purge(int(self.cfg['retention_days']), int(self.cfg['max_rows']))
                    if n:
                        logger.info(f"Purge : {n} messages anciens supprimés")
                except sqlite3.Error as e:
                    logger.warning(f"Purge impossible : {e}")

    def stop(self, *_):
        self.stop_evt.set()


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    r = Receiver(load_config())
    signal.signal(signal.SIGTERM, r.stop)
    signal.signal(signal.SIGINT, r.stop)
    r.run()


if __name__ == '__main__':
    main()
