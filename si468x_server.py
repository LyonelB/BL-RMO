#!/usr/bin/env python3
"""Dongle DAB+ Si468X Headless (mc_popa) vu comme welle-cli.

Pilote le dongle par son port série et sert la même API HTTP que welle-cli
(fork welle-monitor) : /mux.json, /mp3/<sid>, /slide/<sid>, /status. La
surveillance DAB+ (dab_monitor.py), l'écoute, la pige et le diaporama de
BL-RMO fonctionnent ainsi sans changement avec l'un ou l'autre récepteur.
En plus, /dongle.json donne les détails propres au dongle (firmware 2.0.0 :
sous-canaux, PTY et langue de chaque service, heure du multiplex) pour
l'onglet Récepteur de la page DAB+.

Le Si4684 ne décode qu'un service à la fois : celui passé par --sid (le service
surveillé). Les autres services du multiplex sont listés (présence suivie) sans
son. Le son arrive par la carte son USB du dongle (hw:DAB), en PCM 48 kHz.

Le protocole ne donne que l'identifiant local (0, 1, 2...) des services ; leur
SID est appris au démarrage en sélectionnant chaque service une fois (environ
1 s par service), puis gardé en cache par ensemble.

Usage (lancé par dab_monitor.py) :
    python3 si468x_server.py -c 9A -w 7979 --sid 0xfa41 [--port /dev/serial/by-id/...] [--card hw:DAB]
"""
import argparse
import base64
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dab_monitor import BAND_III                       # noqa: E402
from si468x import BAUD, AUDIO_TYPES, parse_kv, parse_list, labels_complete   # noqa: E402

logger = logging.getLogger('si468x')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SID_CACHE = os.path.join(BASE_DIR, 'si468x_sids.json')
CHANNELS = list(BAND_III)
RATE = 48000

PTY = ['', 'Infos', 'Actualité', 'Info', 'Sport', 'Éducation', 'Fiction', 'Culture', 'Science',
       'Divers', 'Musique pop', 'Rock', 'Variétés', 'Classique léger', 'Classique', 'Autre musique',
       'Météo', 'Économie', 'Enfants', 'Société', 'Religion', 'Libre antenne', 'Voyage', 'Loisirs',
       'Jazz', 'Country', 'Musique nationale', 'Rétro', 'Folk', 'Documentaire', 'Test alarme', 'Alarme']
TOTAL_CU = 864                                  # capacité d'un multiplex (unités de capacité)
SUB_MODES = {0: 'audio', 1: 'données', 2: 'FIDC', 3: 'paquets', 4: 'DAB+', 5: 'DAB', 7: 'X-PAD'}


def protection_label(p):
    """Protection du Si468x : 1-5 UEP 1 à 5, 6-9 EEP 1-A à 4-A, 10-13 EEP 1-B à 4-B."""
    try:
        p = int(p)
    except (TypeError, ValueError):
        return ''
    if 1 <= p <= 5:
        return f'UEP {p}'
    if 6 <= p <= 9:
        return f'EEP {p - 5}-A'
    if 10 <= p <= 13:
        return f'EEP {p - 9}-B'
    return ''


def sub_type(mode, comp):
    """Type d'un sous-canal ($SUB) : pour l'audio, l'ASCTy du compID distingue DAB+ et DAB."""
    try:
        c = int(comp or '0', 16)
    except ValueError:
        c = 0
    if (c >> 14) & 3 == 0 and mode in (0, 4, 5):
        ascty = ((c >> 16) & 0xFF) >> 2
        if ascty == 63:
            return 'DAB+'
        if ascty == 0:
            return 'DAB'
    return SUB_MODES.get(mode, str(mode))


def norm_sid(s):
    """'0xfa41', 'fa41' ou '64065' -> 'fa41'."""
    s = str(s or '').strip().lower()
    try:
        if s.startswith('0x'):
            return f'{int(s, 16):04x}'
        if s.isdigit() and len(s) >= 5:
            return f'{int(s):04x}'
        return f'{int(s, 16):04x}'
    except ValueError:
        return s


def find_port():
    import glob
    ports = sorted(glob.glob('/dev/serial/by-id/*Si468X*'))
    return ports[0] if ports else None


class Dongle:
    """État du récepteur, alimenté par le port série."""

    def __init__(self, port, channel, want_sid, card):
        import serial
        self.ser = serial.Serial(port, BAUD, timeout=0.2)
        self.channel, self.idx = channel, CHANNELS.index(channel)
        self.want_sid = norm_sid(want_sid) if want_sid else ''
        self.card = card
        self.lock = threading.Lock()
        self.wlock = threading.Lock()
        self.chip = ''
        self.rf = {'lock': False, 'cnr': 0.0, 'fic': 0.0, 'signal': 0.0, 'snr': 0.0, 'fiberr': 0,
                   'cu': None, 'alvl': None, 'at': 0.0}
        self.lock_since = None
        self.unlock_since = time.time()
        self.ens = {'eid': '', 'label': '', 'ecc': ''}
        self.list_raw = ''
        self.services = []                  # [{id, type, label, short}]
        self.sid_of = {}                    # id local -> sid
        self.current_id = None
        self.info = {}                      # dernier $I (service décodé)
        self.dls = ''
        self.slide = None                   # (octets, type MIME, heure)
        self.audio = {'left': None, 'right': None, 'time': 0}
        self.last_line = time.time()
        self.events = queue.Queue()
        self.tuning = False                 # entre TUNE= et son accusé *TUNE= : $L et $S périmés ignorés
        # Service demandé (numéro dans $L). Le ID de $I n'est pas ce numéro (SERVICE=2 répond
        # ID=3, SERVICE=12 répond ID=18) : le $I qui suit SERVICE= décrit le service demandé.
        self.s_fiberr = False               # FIBERR présent dans $S (firmware récent)
        self.sids_line = False              # SID reçus par $SIDS= (firmware récent)
        self.selected_id = None
        self.selected_ack = True            # $I d'avant l'accusé *SERVICE= : service précédent, ignoré
        self.recent = []                    # dernières lignes de contrôle (diagnostic)
        # Firmware 2.0.0 : détails du multiplex pour l'onglet Récepteur (/dongle.json)
        self.port = port
        self.version = ''                   # *VER=
        self.subs = {}                      # id local -> sous-canal ($SUB)
        self.svx = {}                       # id local -> PTY, ECC, langue ($SVX)
        self.mux_time = None                # ($TIME en secondes UTC, heure de réception)
        self.fib_hist = []                  # [(heure, FIBERR cumulé)] pour un taux par seconde
        self._load_cache()
        threading.Thread(target=self._read, daemon=True, name='si468x-serial').start()

    # ── Série ──────────────────────────────────────────────────────────
    def send(self, cmd, pause=0.2):
        with self.wlock:
            self.ser.write((cmd + '\n').encode())
        time.sleep(pause)

    def _read(self):
        buf = b''
        while True:
            try:
                chunk = self.ser.read(65536)
            except Exception as e:
                logger.error(f"Port série du dongle : {e}")
                os._exit(2)
            buf += chunk
            while b'\n' in buf:
                raw, buf = buf.split(b'\n', 1)
                try:
                    self._line(raw.decode('utf-8', 'replace').strip())
                except Exception as e:
                    logger.debug(f"Ligne ignorée ({e})")

    def _line(self, line):
        if not line:
            return
        now = time.time()
        self.last_line = now
        if line[:1] in '*#' or line.startswith('$I='):
            self.recent = (self.recent + [line[:120]])[-12:]
        if line.startswith('*TUNE='):
            self.tuning = False
            return
        if line.startswith('*SERVICE='):
            if line[9:] == self.selected_id:
                self.selected_ack = True
            return
        if self.tuning and line.startswith(('$S=', '$L=', '$SIDS=', '$SUB=', '$SVX=')):
            return
        if line.startswith('$S='):
            kv = parse_kv(line[3:])
            locked = kv.get('LOCK') == '1'
            with self.lock:
                self.rf.update(lock=locked, at=now)
                for k, key in (('cnr', 'CNR'), ('fic', 'FIC'), ('signal', 'SIGNAL'), ('snr', 'SNR')):
                    try:
                        self.rf[k] = float(kv.get(key, 0))
                    except ValueError:
                        pass
                # Firmware de novembre 2026 : FIBERR (cumulé depuis le réglage), CU, ALVL
                if 'FIBERR' in kv:
                    try:
                        self.rf['fiberr'] = int(kv['FIBERR'])
                        self.s_fiberr = True
                        h = self.fib_hist
                        if h and self.rf['fiberr'] < h[-1][1]:
                            h.clear()                       # compteur remis à zéro (nouveau réglage)
                        h.append((now, self.rf['fiberr']))
                        while len(h) > 2 and now - h[0][0] > 10:
                            h.pop(0)
                    except ValueError:
                        pass
                for k in ('CU', 'ALVL'):
                    try:
                        self.rf[k.lower()] = int(kv[k]) if k in kv else None
                    except ValueError:
                        pass
            if locked:
                self.lock_since = self.lock_since or now
                self.unlock_since = None
            else:
                self.unlock_since = self.unlock_since or now
                self.lock_since = None
        elif line.startswith('$DBG'):
            kv = parse_kv(line.replace(' ', ','))
            if not self.s_fiberr and 'FIBERR' in kv:        # firmware sans FIBERR dans $S
                try:
                    with self.lock:
                        self.rf['fiberr'] = int(kv['FIBERR'])     # cumulé depuis le réglage
                except ValueError:
                    pass
        elif line.startswith('$SIDS='):
            # Firmware de novembre 2026 : SID de chaque service, après $L (index:SID)
            got = {}
            for part in line[6:].split(';'):
                i, _, sid = part.partition(':')
                if i.strip().isdigit() and sid.strip():
                    got[i.strip()] = norm_sid(sid)
            if got:
                with self.lock:
                    self.sid_of.update(got)
                    self.sids_line = True
                self.events.put(('sids', len(got)))
        elif line.startswith('$L='):
            ens, services = parse_list(line)
            with self.lock:
                complete = labels_complete(ens, services)
                if complete or not self.services:
                    self.ens, self.services, self.list_raw = ens, services, line
            self.events.put(('list', complete))
        elif line.startswith('$I='):
            if not self.selected_ack:
                return
            kv = parse_kv(line[3:], ';')
            sid = norm_sid(kv.get('SID', ''))
            lid = self.selected_id if self.selected_id is not None else kv.get('ID')
            with self.lock:
                if lid is not None and sid:
                    self.sid_of[lid] = sid
                    self.current_id = lid
                self.info = kv
            self.events.put(('info', lid))
        elif line.startswith('$D='):
            text = line[3:]
            if text.startswith('RT='):
                text = text[3:]
            with self.lock:
                self.dls = ' '.join(text.split())
        elif line.startswith('$M=SLIDESHOW='):
            body = line[len('$M=SLIDESHOW='):]
            kind, _, data = body.partition(',')
            if kind in ('1', '2') and data.startswith('BASE64='):
                try:
                    img = base64.b64decode(data[7:])
                    with self.lock:
                        self.slide = (img, 'image/jpeg' if kind == '1' else 'image/png', int(now))
                except ValueError:
                    pass
        elif line.startswith('$CHIP='):
            self.chip = line[6:]
        elif line.startswith('*VER='):
            self.version = line[5:].strip()
        elif line.startswith('$SUB='):
            # $SUB=<id>:<CU début>,<nombre de CU>,<kbit/s>,<protection>,<type>,<compID hex>;...
            got = {}
            for part in line[5:].split(';'):
                i, _, rest = part.partition(':')
                f = rest.split(',')
                if not i.strip().isdigit() or len(f) < 5:
                    continue
                try:
                    start, cu, kbps, prot, mode = (int(x) for x in f[:5])
                except ValueError:
                    continue
                if cu <= 0 or start + cu > TOTAL_CU:
                    continue
                comp = f[5].strip().upper() if len(f) > 5 else ''
                got[i.strip()] = {'start': start, 'cu': cu, 'kbps': kbps, 'protection': protection_label(prot),
                                  'type': sub_type(mode, comp)}
            with self.lock:
                self.subs.update(got)
        elif line.startswith('$SVX='):
            # $SVX=<id>:<PTY>,<ECC hex>,<langue>,<UA>;...
            got = {}
            for part in line[5:].split(';'):
                i, _, rest = part.partition(':')
                f = rest.split(',')
                if not i.strip().isdigit() or len(f) < 3:
                    continue
                try:
                    pty, lang = int(f[0]), int(f[2])
                except ValueError:
                    continue
                got[i.strip()] = {'pty': PTY[pty] if 0 <= pty < len(PTY) else '', 'ecc': f[1].strip().upper(),
                                  'lang': lang}
            with self.lock:
                self.svx.update(got)
        elif line.startswith('$TIME='):
            try:
                t = datetime.strptime(line[6:].strip(), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                self.mux_time = (t.timestamp(), now)
            except ValueError:
                pass

    # ── Démarrage et sélection du service ─────────────────────────────
    def start(self):
        self.send('ENABLE=0', 0.3)
        self.send('ENABLE=1', 0.3)
        self.send('VER=1', 0.2)
        self.send('MODE=dab', 1.2)
        self.tune()

    def tune(self):
        with self.lock:
            self.services, self.ens, self.current_id, self.info, self.dls = [], {'eid': '', 'label': '', 'ecc': ''}, None, {}, ''
        with self.lock:
            self.rf.update(lock=False, cnr=0.0, fic=0.0)
            self.subs, self.svx, self.fib_hist = {}, {}, []
        self.unlock_since, self.lock_since = time.time(), None
        self.tuning = True
        self.sids_line = False
        self.selected_id = None
        self._drain()
        self.send(f'TUNE={self.idx}', 0.4)

    def _drain(self):
        while not self.events.empty():
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

    def wait_list(self, timeout=20):
        """Liste des services avec les noms décodés (ou la dernière reçue)."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                kind, complete = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == 'list' and complete:
                return True
        return bool(self.services)

    def select(self, sid_id, timeout=8):
        self._drain()
        self.recent = []
        self.selected_id, self.selected_ack = str(sid_id), False
        with self.lock:
            self.dls, self.slide = '', None          # textes et image du service précédent
        self.send(f'SERVICE={sid_id}', 0.1)
        end = time.time() + timeout
        while time.time() < end:
            try:
                kind, val = self.events.get(timeout=0.3)
            except queue.Empty:
                continue
            if kind == 'info' and val == str(sid_id):
                time.sleep(1.5)        # second $I du même service (le premier a un débit faux)
                return self.sid_of.get(str(sid_id))
        logger.info(f"    pas de $I pour le service {sid_id} en {timeout} s ; reçu : {' | '.join(self.recent) or 'rien'}")
        return None

    def _cache_key(self):
        # v2 : SID associé au service demandé (et non au ID de $I) ; les caches v1 sont faux
        return f"v2:{self.channel}:{self.ens.get('eid', '')}"

    def _load_cache(self):
        try:
            with open(SID_CACHE) as f:
                self._cache = json.load(f)
        except (OSError, ValueError):
            self._cache = {}

    def _save_cache(self):
        try:
            self._cache[self._cache_key()] = {'list': self.list_raw, 'sids': self.sid_of}
            tmp = SID_CACHE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self._cache, f, indent=1)
            os.replace(tmp, SID_CACHE)
        except OSError:
            pass

    def learn_sids(self):
        """SID de chaque service audio : repris du cache si la liste n'a pas changé,
        sinon appris en sélectionnant chaque service (environ 1 s par service)."""
        audio = [s for s in self.services if s['type'] in AUDIO_TYPES]
        # Firmware récent : $SIDS= suit $L, inutile de sélectionner chaque service
        end = time.time() + 2
        while time.time() < end and not all(s['id'] in self.sid_of for s in audio):
            time.sleep(0.2)
        if self.sids_line and all(s['id'] in self.sid_of for s in audio):
            logger.info(f"SID des {len(audio)} services fournis par le dongle ($SIDS), sans changer de service")
            self._save_cache()
            return
        cached = self._cache.get(self._cache_key()) or {}
        if cached.get('list') == self.list_raw and cached.get('sids'):
            with self.lock:
                self.sid_of.update(cached['sids'])
            if not self.want_sid or self.want_sid in cached['sids'].values():
                logger.info(f"SID de {len(cached['sids'])} services sur {len(audio)} repris du cache")
                return
        logger.info(f"Apprentissage des SID : {len(audio)} services")
        for s in audio:
            if s['id'] in self.sid_of:
                continue
            sid = self.select(s['id'])
            logger.info(f"  service {s['id']} {s['label'] or '(sans nom)'} : SID {sid or 'inconnu'}")
            if sid:
                self._save_cache()
        self._save_cache()

    def select_wanted(self):
        want = self.want_sid
        target = next((i for i, sid in self.sid_of.items() if sid == want), None) if want else None
        if target is None:
            audio = [s for s in self.services if s['type'] in AUDIO_TYPES and s['label'] not in ('', '.', '..')]
            if want:
                logger.warning(f"Service {want} absent du multiplex")
                return False
            if not audio:
                return False
            target = audio[0]['id']
        sid = self.select(target)
        logger.info(f"Service décodé : {target} (SID {sid})")
        return sid is not None

    # ── Vue welle-cli ─────────────────────────────────────────────────
    def mux(self):
        now = time.time()
        with self.lock:
            rf, info = dict(self.rf), dict(self.info)
            services, ens = list(self.services), dict(self.ens)
            cur = self.current_id
            audio, slide, dls = dict(self.audio), self.slide, self.dls
            sid_of = dict(self.sid_of)
        # Synchronisation perdue depuis plus de 5 s : plus d'ensemble ni de services
        lost = not rf['lock'] and self.unlock_since and now - self.unlock_since > 5
        out = {
            'receiver': {'hardware': {'name': f"Si{self.chip or '468x'} (dongle USB)", 'gain': 0}},
            'demodulator': {'snr': 0 if lost else rf['cnr'], 'frequencycorrection': 0,
                            'fic': {'numcrcerrors': rf['fiberr']},
                            'ficquality': 0 if lost else rf['fic'], 'signal': rf['signal'],
                            # firmware récent : SNR signé de la puce, unités de capacité décodées
                            'chip_snr': rf['snr'], 'cu': rf['cu']},
            'ensemble': {'id': '' if lost or not ens['eid'] else f"0x{ens['eid'].lower()}",
                         'label': {'label': '' if lost else ens['label']}, 'ecc': ens['ecc']},
            'current_carousel_sid': None,
            'services': [],
        }
        if lost:
            return out
        for s in services:
            sid = sid_of.get(s['id'])
            if not sid or s['type'] not in AUDIO_TYPES:
                continue
            d = {'sid': f'0x{sid}', 'label': {'label': s['label'], 'shortlabel': s['short']},
                 'components': [{'primary': True, 'ascty': AUDIO_TYPES[s['type']],
                                 'subchannel': {'bitrate': 0, 'protection': ''}}],
                 'errorcounters': {'aacerrors': 0, 'frameerrors': 0, 'rserrors': 0}}
            if s['id'] == cur:
                try:
                    br = int(info.get('BITRATE', 0))
                except ValueError:
                    br = 0
                prot_s = protection_label(info.get('PROTECTION'))
                try:
                    pty = PTY[int(info.get('PTY', 0))]
                except (ValueError, IndexError):
                    pty = ''
                d['components'][0]['subchannel'] = {'bitrate': br if 0 < br < 1000 else 0, 'protection': prot_s}
                d['samplerate'] = int(info.get('SAMPLERATE', 0) or 0)
                d['channels'] = 1 if info.get('AUDIO') == '1' else 2
                d['ptystring'] = pty
                d['dls'] = {'label': dls}
                if audio['time']:
                    d['audiolevel'] = audio
                d['mot'] = {'time': slide[2] if slide else 0}
                if rf['alvl'] is not None:
                    d['alvl'] = rf['alvl']        # niveau audio après soft mute, 0-100 (expérimental)
            out['services'].append(d)
        return out

    # ── Onglet Récepteur de la page DAB+ ──────────────────────────────
    def details(self):
        """Tout ce que le dongle donne en plus de /mux.json (firmware 2.0.0)."""
        now = time.time()
        with self.lock:
            rf, ens = dict(self.rf), dict(self.ens)
            services, subs, svx = list(self.services), dict(self.subs), dict(self.svx)
            sid_of, cur, hist = dict(self.sid_of), self.current_id, list(self.fib_hist)
        lost = not rf['lock'] and self.unlock_since and now - self.unlock_since > 5
        rate = None
        if len(hist) >= 2 and hist[-1][0] - hist[0][0] >= 1:
            rate = round((hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0]), 1)
        mt = None
        if self.mux_time and now - self.mux_time[1] < 300:
            t, at = self.mux_time
            mt = {'utc': t + (now - at), 'offset': round(t - at, 1), 'age': round(now - at)}
        rows = []
        if not lost:
            for s in services:
                sub, x = subs.get(s['id']), svx.get(s['id'], {})
                rows.append({
                    'id': s['id'], 'sid': sid_of.get(s['id'], ''), 'label': s['label'], 'short': s['short'],
                    'type': (sub or {}).get('type') or s['type_label'], 'sub': sub,
                    'pty': x.get('pty', ''), 'lang': x.get('lang', 0), 'current': s['id'] == cur,
                })
            rows.sort(key=lambda r: (r['sub'] is None, (r['sub'] or {}).get('start', 0), int(r['id'])))
        seen, used = set(), 0
        for r in rows:
            sub = r['sub']
            if sub and (sub['start'], sub['cu']) not in seen:     # sous-canal partagé : compté une fois
                seen.add((sub['start'], sub['cu']))
                used += sub['cu']
        return {
            'chip': self.chip, 'version': self.version, 'port': self.port, 'card': self.card,
            'channel': self.channel, 'frequency_mhz': BAND_III[self.channel] / 1000,
            'lock': rf['lock'], 'lock_since': self.lock_since, 'unlock_since': self.unlock_since,
            'signal': rf['signal'], 'cnr': rf['cnr'], 'snr': rf['snr'], 'fic': rf['fic'],
            'fiberr': rf['fiberr'], 'fiberr_rate': rate,
            'ensemble': {} if lost else ens, 'time': mt,
            'capacity': {'used': used, 'total': TOTAL_CU} if seen else None,
            'services': rows,
        }

    def current_sid(self):
        with self.lock:
            return self.sid_of.get(self.current_id) if self.current_id is not None else None


class Audio:
    """Carte son du dongle -> niveaux (pour /mux.json) et MP3 (pour /mp3/<sid>)."""

    def __init__(self, dongle, card):
        self.dongle, self.card = dongle, card
        self.clients = set()
        self.clock = threading.Lock()
        threading.Thread(target=self._run, daemon=True, name='si468x-audio').start()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.clock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.clock:
            self.clients.discard(q)

    def _run(self):
        import array
        while True:
            rec = subprocess.Popen(['arecord', '-q', '-D', self.card, '-f', 'S16_LE', '-r', str(RATE), '-c', '2', '-t', 'raw'],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            enc = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 's16le', '-ar', str(RATE), '-ac', '2',
                                    '-i', 'pipe:0', '-c:a', 'libmp3lame', '-b:a', '128k', '-f', 'mp3', 'pipe:1'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            threading.Thread(target=self._fanout, args=(enc,), daemon=True).start()
            block = RATE // 10 * 4                            # 100 ms de stéréo 16 bits
            peak_l = peak_r = 0
            last = time.time()
            try:
                while True:
                    data = rec.stdout.read(block)
                    if not data:
                        break
                    enc.stdin.write(data)
                    a = array.array('h', data[:len(data) - len(data) % 4])
                    if a:
                        peak_l = max(peak_l, max(abs(x) for x in a[0::2]))
                        peak_r = max(peak_r, max(abs(x) for x in a[1::2]))
                    now = time.time()
                    if now - last >= 0.5:
                        with self.dongle.lock:
                            self.dongle.audio = {'left': peak_l, 'right': peak_r, 'time': int(now * 1000)}
                        peak_l = peak_r = 0
                        last = now
            except (BrokenPipeError, OSError):
                pass
            logger.warning(f"Carte son {self.card} : lecture interrompue, reprise dans 3 s")
            for p in (rec, enc):
                try:
                    p.kill()
                except Exception:
                    pass
            time.sleep(3)

    def _fanout(self, enc):
        while True:
            chunk = enc.stdout.read(4096)
            if not chunk:
                return
            with self.clock:
                clients = list(self.clients)
            for q in clients:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    pass                                       # auditeur trop lent : morceau perdu


def make_handler(dongle, audio):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self._json({'status': 'non pris en charge'}, 404)

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/mux.json':
                return self._json(dongle.mux())
            if path == '/status':
                return self._json({'alive': time.time() - dongle.last_line < 10})
            if path == '/dongle.json':
                return self._json(dongle.details())
            if path.startswith('/slide/'):
                with dongle.lock:
                    slide = dongle.slide
                if not slide or norm_sid(path[7:]) != dongle.current_sid():
                    return self._json({'error': 'pas d\'image'}, 404)
                self.send_response(200)
                self.send_header('Content-Type', slide[1])
                self.send_header('Content-Length', str(len(slide[0])))
                self.end_headers()
                self.wfile.write(slide[0])
                return
            if path.startswith('/mp3/'):
                if norm_sid(path[5:]) != dongle.current_sid():
                    return self._json({'error': 'le dongle ne décode que le service surveillé'}, 404)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                q = audio.subscribe()
                try:
                    while True:
                        self.wfile.write(q.get(timeout=30))
                except Exception:
                    pass
                finally:
                    audio.unsubscribe(q)
                return
            self._json({'receiver': 'Si468x', 'channel': dongle.channel})
    return H


def main():
    ap = argparse.ArgumentParser(description='Dongle Si468x servi comme welle-cli')
    ap.add_argument('-c', '--channel', default='9A')
    ap.add_argument('-w', '--web', type=int, default=7979)
    ap.add_argument('--sid', default='')
    ap.add_argument('--port', default='')
    ap.add_argument('--card', default='hw:DAB')
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    port = a.port or find_port()
    if not port or not os.path.exists(port):
        logger.error('No valid device found : dongle Si468x absent (aucun /dev/serial/by-id/*Si468X*)')
        sys.exit(3)
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    dongle = Dongle(port, a.channel.upper(), a.sid, a.card)
    audio = Audio(dongle, a.card)
    srv = ThreadingHTTPServer(('0.0.0.0', a.web), make_handler(dongle, audio))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logger.info(f"Dongle {port}, canal {dongle.channel}, API welle-cli sur le port {a.web}")

    dongle.start()
    selected = False
    last_tune = next_try = time.time()
    while True:
        now = time.time()
        if now - dongle.last_line > 15:
            logger.error('Le dongle ne répond plus')
            os._exit(4)
        if not selected and dongle.rf['lock'] and now >= next_try:
            if dongle.wait_list(10):
                dongle.learn_sids()
                selected = dongle.select_wanted()
                last_tune = time.time()
            if not selected:
                next_try = time.time() + 60       # service absent : nouvel essai dans 1 min
        # Synchronisation perdue longtemps : nouveau réglage du canal
        if dongle.unlock_since and now - dongle.unlock_since > 60 and now - last_tune > 60:
            logger.warning('Pas de synchronisation depuis 60 s : nouveau réglage du canal')
            dongle.tune()
            selected, last_tune = False, now
        # Synchronisation retrouvée sans service décodé
        if selected and dongle.current_sid() is None and dongle.rf['lock'] and now >= next_try:
            selected = dongle.select_wanted()
            if not selected:
                next_try = now + 60
        time.sleep(1)


if __name__ == '__main__':
    main()
