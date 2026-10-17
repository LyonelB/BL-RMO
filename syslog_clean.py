#!/usr/bin/env python3
"""
syslog_clean.py : lecture « propre » des journaux reçus (syslog.db).

Rien n'est modifié ni supprimé en base : le nettoyage se fait à l'affichage.
  - Préfixe du BW retiré (« : #31573 - 2026-10-17 12:11:06 »), l'heure de
    l'appareil est gardée à part (device_ts).
  - Messages sur plusieurs lignes conservés ; lignes vides du relevé DTLG retirées.
  - Relevé horaire DTLG du BW résumé en une ligne (summary) + valeurs (metrics).
  - Règles de masquage : messages connus sans importance (bruit de l'interface
    du Sound4, fragments du relevé du BW...). Règles intégrées désactivables,
    règles ajoutées par l'administrateur depuis la page Journaux.
    « Afficher les messages masqués » les remet, grisés, avec la règle en cause.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime

COUNT_CAP = 20000      # au-delà, le compteur affiche « 20 000+ » (évite de tout parcourir)

BW_PREFIX = re.compile(r'^\s*:?\s*#\d+\s*-\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})[ \t]?')
DTLG_EMPTY = re.compile(r'^\s*M:\s*=\s*\d*\s*$')

BUILTIN_RULES = [
    # id, libellé, programme, motif (sur le message nettoyé)
    ('b-frontpanel', "Sound4 : messages de l'interface de contrôle à distance (frontpanel)", 'frontpanel', ''),
    ('b-bw-saved', "BW : sauvegarde de l'état du système", None, r'^System state saved$'),
    ('b-bw-clock', "BW : resynchronisation horaire de l'horloge", None, r'^Synchronising SysClock\.'),
    ('b-bw-session', 'BW : fin de session web', None, r'^Remote session \d+ (about to )?expired\.?$'),
    ('b-bw-fragments', 'BW : fragments du relevé horaire', None, r'^(@ \d+:\d+|,\d+|Mute source: OFF)?$'),
    ('b-bw-alarms-off', 'BW : état des alarmes, aucune alarme active',
     None, r'^Alarm status \[A-C\]: Off Off Off\s+Trigger status \[1-4\]: Off Off Off Off$'),
]


# ── Nettoyage d'un message ──────────────────────────────────────────────
def clean(message):
    """Renvoie (texte nettoyé, heure de l'appareil ou None)."""
    msg = message or ''
    dev_ts = None
    m = BW_PREFIX.match(msg)
    if m:
        dev_ts = m.group(1).replace(' ', 'T')
        msg = msg[m.end():]
    if 'DTLG >' in msg:
        lines = []
        for line in msg.splitlines():
            line = line.strip()
            if line.startswith('DTLG >'):
                line = line[6:].strip()
            if not line or DTLG_EMPTY.match(line):
                continue
            lines.append(line)
        msg = '\n'.join(lines)
    return msg.strip('\r\n\t '), dev_ts


def _num(v):
    m = re.match(r'^\s*(-?[\d.]+)\s*([a-zA-Z]*)', v or '')
    if not m:
        return None, ''
    try:
        return float(m.group(1)), m.group(2)
    except ValueError:
        return None, ''


def dtlg(text):
    """Relevé DTLG du BW : (résumé, valeurs) ou (None, None)."""
    if 'psu1present' not in text and 'pafwd' not in text:
        return None, None
    kv = {}
    for line in text.splitlines():
        m = re.match(r'^M:\s*(\w+)\s*=\s*(.+)$', line.strip())
        if m:
            kv[m.group(1)] = m.group(2).strip()
    met = {}
    for k, v in kv.items():
        n, unit = _num(v)
        if n is None:
            continue
        if k.endswith('current'):
            n = n / 1000 if unit.lower() == 'ma' else n          # le BW passe de mA à A au-delà de 10 A
        elif k.endswith('mains'):
            n = n * 1000 if unit.lower() == 'kv' else n          # kV -> V
        met[k] = round(n, 3)
    fr = lambda x, d=1: f'{x:.{d}f}'.replace('.', ',')
    parts = []
    if 'pafwd' in met:
        parts.append(f"{met['pafwd']:.0f} W directe")
    if 'parev' in met:
        parts.append(f"{met['parev']:.0f} W réfléchie")
    if 'pavswr' in met:
        parts.append(f"ROS {fr(met['pavswr'])}")
    if 'patemp' in met:
        parts.append(f"PA {met['patemp']:.0f} °C")
    for i in (1, 2):
        c, u = met.get(f'psu{i}current'), met.get(f'psu{i}voltage')
        if c is not None:
            parts.append(f"PSU{i} {fr(c, 2)} A" + (f" / {fr(u)} V" if u is not None else ''))
    if 'psu1mains' in met:
        parts.append(f"secteur {met['psu1mains']:.0f} V")
    return ('Relevé émetteur : ' + ' · '.join(parts)) if parts else None, met


def similar_pattern(text):
    """Motif « messages semblables » : chiffres variables, le reste à l'identique."""
    out, last = [], 0
    for m in re.finditer(r'\d+', text):
        out.append(re.escape(text[last:m.start()]))
        out.append(r'\d+')
        last = m.end()
    out.append(re.escape(text[last:]))
    return '^' + ''.join(out) + '$'


# ── Règles ──────────────────────────────────────────────────────────────
class Cleaner:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

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
            c.execute('''CREATE TABLE IF NOT EXISTS hide_rules (
                id TEXT PRIMARY KEY,
                label TEXT,
                src TEXT,               -- adresse de l'appareil (vide : tous)
                program TEXT,           -- programme (vide : tous)
                pattern TEXT,           -- expression régulière sur le message nettoyé
                builtin INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                created TEXT,
                created_by TEXT
            )''')
            for rid, label, prog, pat in BUILTIN_RULES:
                c.execute('INSERT OR IGNORE INTO hide_rules (id, label, program, pattern, builtin, enabled, created) '
                          'VALUES (?,?,?,?,1,1,?)', (rid, label, prog, pat, _now()))
                c.execute('UPDATE hide_rules SET label=?, program=?, pattern=? WHERE id=? AND builtin=1',
                          (label, prog, pat, rid))

    def rules(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM hide_rules ORDER BY builtin DESC, created')]

    def add_rule(self, src, program, message, label, user=''):
        text, _ = clean(message)
        pat = similar_pattern(text)
        rid = 'u-' + datetime.now().strftime('%Y%m%d%H%M%S%f')
        with self._conn() as c:
            c.execute('INSERT INTO hide_rules (id, label, src, program, pattern, builtin, enabled, created, created_by) '
                      'VALUES (?,?,?,?,?,0,1,?,?)', (rid, (label or text)[:200], src or None, program or None, pat, _now(), user))
        return rid

    def set_enabled(self, rid, enabled):
        with self._conn() as c:
            return c.execute('UPDATE hide_rules SET enabled=? WHERE id=?', (1 if enabled else 0, rid)).rowcount

    def delete_rule(self, rid):
        with self._conn() as c:
            return c.execute('DELETE FROM hide_rules WHERE id=? AND builtin=0', (rid,)).rowcount

    def _matcher(self):
        compiled = []
        for r in self.rules():
            if not r['enabled']:
                continue
            try:
                compiled.append((r['id'], r['src'], r['program'], re.compile(r['pattern'] or '', re.S)))
            except re.error:
                continue

        def hidden_by(src, program, message):
            text = None
            for rid, rsrc, rprog, rx in compiled:
                if rsrc and rsrc != src:
                    continue
                if rprog and rprog != (program or ''):
                    continue
                if text is None:
                    text = clean(message)[0]
                if rx.search(text):
                    return rid
            return None
        return hidden_by

    # ── Lecture ─────────────────────────────────────────────────────────
    def _query(self, store, hide, filters):
        where, args = store._where(**filters)
        if hide == 'only':
            cond = 'HIDDEN_BY(src, program, message) IS NOT NULL'
        elif hide:
            cond = 'HIDDEN_BY(src, program, message) IS NULL'
        else:
            return where, args
        return (where + ' AND ' + cond) if where else (' WHERE ' + cond), args

    @contextmanager
    def _reader(self):
        with self._conn() as c:
            c.create_function('HIDDEN_BY', 3, self._matcher(), deterministic=True)
            yield c

    def search(self, store, hide=True, limit=200, offset=0, **filters):
        """Comme SyslogStore.search, avec nettoyage et masquage. Renvoie (lignes, total, masqués, plafonné)."""
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        labels = {r['id']: r['label'] for r in self.rules()}
        with self._reader() as c:
            where, args = self._query(store, hide, filters)
            rows = [dict(r) for r in c.execute(
                f'SELECT *, HIDDEN_BY(src, program, message) AS hidden_by FROM logs{where} '
                f'ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?', args + [limit, offset])]
            total = c.execute(f'SELECT COUNT(*) FROM (SELECT 1 FROM logs{where} LIMIT ?)', args + [COUNT_CAP]).fetchone()[0]
            hw, ha = self._query(store, 'only', filters)
            hidden = c.execute(f'SELECT COUNT(*) FROM (SELECT 1 FROM logs{hw} LIMIT ?)', ha + [COUNT_CAP]).fetchone()[0]
        for r in rows:
            self.decorate(r, labels)
        return rows, total, hidden, total >= COUNT_CAP

    def iter_rows(self, store, hide=True, **filters):
        labels = {r['id']: r['label'] for r in self.rules()}
        with self._reader() as c:
            where, args = self._query(store, hide, filters)
            for r in c.execute(f'SELECT *, HIDDEN_BY(src, program, message) AS hidden_by FROM logs{where} '
                               f'ORDER BY ts, id', args):
                yield self.decorate(dict(r), labels)

    @staticmethod
    def decorate(r, labels=None):
        r['raw'] = r.get('message') or ''
        r['message'], r['device_ts'] = clean(r['raw'])
        r['summary'], r['metrics'] = dtlg(r['message'])
        if r.get('hidden_by') and labels is not None:
            r['hidden_label'] = labels.get(r['hidden_by'], '')
        return r


def _now():
    return datetime.now().isoformat(timespec='seconds')
