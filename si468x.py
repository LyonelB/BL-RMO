"""Dongle Si468X Headless DAB (mc_popa) : balayage de la bande III.

Protocole série texte, 1 000 000 bauds, une ligne par message terminée par \\n :
commandes KEY=valeur ; réponses *KEY=... (accusé), $KEY=... (données), #n (code
d'erreur). Le dongle ne sert ici qu'en essai, à côté de la clé RTL-SDR et de
welle-cli : seul le balayage l'utilise, le port est fermé entre deux balayages.
"""
import logging
import threading
import time

from dab_monitor import BAND_III

logger = logging.getLogger(__name__)

BAUD = 1_000_000
CHANNELS = list(BAND_III)                     # index 0 à 37 : 5A ... 13F, dans l'ordre du firmware
AUDIO_TYPES = {'0': 'audio', '4': 'DAB+', '5': 'DAB'}
TYPES = {'0': 'audio', '1': 'données', '2': 'FIDC', '3': 'paquets', '4': 'DAB+', '5': 'DAB',
         '6': 'FIC', '7': 'XPAD', '8': 'aucun'}


def parse_kv(text, sep=','):
    out = {}
    for part in text.split(sep):
        if '=' in part:
            k, v = part.split('=', 1)
            out[k.strip()] = v.strip()
    return out


def parse_list(line):
    """$L=COUNT=13,ENSEMBLE=F030,LA ROCHE SUR YON,ECC=E1;SERVICES=0,4,Graffiti Radio|Graffiti;1,4,..."""
    body = line[3:]
    head, _, rest = body.partition(';SERVICES=')
    ens = {'eid': '', 'label': '', 'ecc': ''}
    if 'ENSEMBLE=' in head:
        e = head.split('ENSEMBLE=', 1)[1]
        ecc = ''
        if ',ECC=' in e:
            e, ecc = e.rsplit(',ECC=', 1)
        eid, _, label = e.partition(',')
        ens = {'eid': eid.strip().upper(), 'label': label.strip(), 'ecc': ecc.strip().upper()}
    services = []
    for item in rest.split(';') if rest else []:
        f = item.split(',', 2)
        if len(f) < 3:
            continue
        label, _, short = f[2].partition('|')
        services.append({'id': f[0].strip(), 'type': f[1].strip(), 'type_label': TYPES.get(f[1].strip(), f[1].strip()),
                         'label': label.strip(), 'short': short.strip()})
    return ens, services


def labels_complete(ens, services):
    """La liste arrive parfois avant le décodage des noms (ensemble vide, ECC 00)."""
    return bool(ens['label']) and all(s['label'] for s in services)


class Si468xLink:
    """Port série du dongle, lu en continu par un thread."""

    def __init__(self, port):
        import serial
        self.ser = serial.Serial(port, BAUD, timeout=0.2)
        self.lines = []
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        buf = b''
        while not self._stop.is_set():
            try:
                buf += self.ser.read(4096)
            except Exception:
                break
            while b'\n' in buf:
                raw, buf = buf.split(b'\n', 1)
                line = raw.decode('utf-8', 'replace').strip()
                if line and not line.startswith('$M=') and not line.startswith('$EPGRAW='):
                    with self.lock:
                        self.lines.append((time.time(), line))
                        if len(self.lines) > 5000:
                            del self.lines[:1000]

    def since(self, t, prefix=None):
        with self.lock:
            return [l for ts, l in self.lines if ts >= t and (prefix is None or l.startswith(prefix))]

    def send(self, cmd, pause=0.2):
        self.ser.write((cmd + '\n').encode())
        time.sleep(pause)

    def close(self):
        self._stop.set()
        try:
            self.ser.close()
        except Exception:
            pass


class BandScan:
    """Balayage des 38 canaux en arrière-plan, une seule fois à la fois."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {'running': False, 'progress': 0, 'total': len(CHANNELS), 'channel': None,
                      'results': [], 'error': None, 'started': None, 'finished': None, 'port': None, 'chip': None}

    def snapshot(self):
        with self._lock:
            return {**self.state, 'results': [dict(r) for r in self.state['results']]}

    def start(self, port, link_factory=Si468xLink):
        with self._lock:
            if self.state['running']:
                return False
            self.state.update(running=True, progress=0, channel=None, results=[], error=None,
                              started=time.time(), finished=None, port=port, chip=None)
        threading.Thread(target=self._run, args=(port, link_factory), daemon=True).start()
        return True

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def _run(self, port, link_factory):
        link = None
        try:
            link = link_factory(port)
            t0 = time.time()
            link.send('ENABLE=0', 0.3)
            link.send('ENABLE=1', 0.3)
            link.send('MODE=dab', 1.2)
            chip = next((l[6:] for l in link.since(t0, '$CHIP=')), None)
            if not link.since(t0, '*MODE='):
                raise RuntimeError('Le dongle ne répond pas (pas d\'accusé MODE=dab)')
            self._set(chip=chip)
            for i, ch in enumerate(CHANNELS):
                self._set(channel=ch, progress=i)
                res = self._scan_channel(link, i, ch)
                with self._lock:
                    self.state['results'].append(res)
            self._set(progress=len(CHANNELS), channel=None)
            found = sum(1 for r in self.state['results'] if r['lock'])
            logger.info(f"Balayage DAB+ (dongle Si468x) : {found} multiplex sur {len(CHANNELS)} canaux")
        except Exception as e:
            logger.warning(f"Balayage DAB+ (dongle Si468x) interrompu : {e}")
            self._set(error=str(e) or e.__class__.__name__)
        finally:
            if link:
                link.close()
            self._set(running=False, finished=time.time())

    @staticmethod
    def _scan_channel(link, idx, ch):
        """Même méthode que l'interface de mc_popa : 400 ms pour perdre l'ancienne
        synchronisation, LOCK=1 attendu jusqu'à 2 s, puis la liste des services ;
        un canal n'est retenu qu'avec LOCK=1 et un ensemble (verrouillages fantômes
        à côté d'émetteurs puissants)."""
        t0 = time.time()
        link.send(f'TUNE={idx}', 0.4)
        t_lock = t0 + 0.4
        deadline, lock = time.time() + (2.0 if idx == 0 else 1.8), False
        while time.time() < deadline and not lock:
            time.sleep(0.1)
            lock = any('LOCK=1' in l for l in link.since(t_lock, '$S='))
        ens, services = {'eid': '', 'label': '', 'ecc': ''}, []
        if lock:
            # Liste complète (noms décodés) attendue jusqu'à 4 s
            deadline = time.time() + 4.0
            while time.time() < deadline:
                time.sleep(0.15)
                lists = link.since(t0, '$L=')
                if lists:
                    ens, services = parse_list(lists[-1])
                    if labels_complete(ens, services):
                        break
        stats = [parse_kv(l[3:]) for l in link.since(t_lock, '$S=')]
        locked = [s for s in stats if s.get('LOCK') == '1'] or stats

        def best(key):
            vals = []
            for s in locked:
                try:
                    vals.append(float(s.get(key, '')))
                except ValueError:
                    pass
            return max(vals) if vals else None

        audio = [s for s in services if s['type'] in AUDIO_TYPES and s['label'] not in ('', '.', '..')]
        return {
            'channel': ch, 'index': idx, 'freq_mhz': BAND_III[ch] / 1000,
            'lock': bool(lock and (ens['eid'] or services)),
            'cnr': best('CNR'), 'fic': best('FIC'),
            'ensemble': ens['label'], 'eid': ens['eid'], 'ecc': ens['ecc'],
            'services': services, 'audio_count': len(audio),
        }
