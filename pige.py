#!/usr/bin/env python3
"""
BL-RMO - Pige audio (enregistrement glissant, 30 jours par défaut)

Deux usages :
  1. Processus enregistreur autonome (service systemd bl-rmo-pige) :
         python3 pige.py
     Lit le flux Icecast local, le découpe en segments MP3 de 5 min
     (copie sans réencodage) et purge ce qui dépasse la rétention.
     Indépendant de Flask : un redémarrage de bl-rmo ne coupe la pige
     que le temps que Icecast retrouve sa source.

  2. Module importé par app.py (PigeStore) : liste des segments,
     couverture, extraction d'un créneau.

Piges multiples : la pige « fm » (section "pige" de config.json) enregistre
l'antenne ; chaque entrée de "pige"."extra" enregistre un service DAB+ servi
par welle-cli (http://127.0.0.1:<port>/mp3/<sid>), réencodé en MP3 CBR pour
que la durée des segments se déduise du débit, comme pour la FM.

Les noms de fichiers sont en UTC (AAAAMMJJ-HHMMSS.mp3 = début du segment),
ce qui évite toute ambiguïté au passage heure d'été / heure d'hiver.
"""
import glob
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger('pige')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')

DEFAULTS = {
    'enabled': True,
    'retention_hours': 720,       # 30 jours, environ 1,4 Go par jour à 128 kbit/s
    'segment_minutes': 5,
    'directory': os.path.join(BASE_DIR, 'pige'),
    'source_url': 'http://localhost:8000/fmmonitor',
    'bitrate_kbps': 128,          # sert uniquement à estimer la durée des segments
    'export_max_minutes': 240,
    'min_free_mb': 500,           # en dessous : purge des segments les plus anciens
}

SEGMENT_RE = re.compile(r'^(\d{8}-\d{6})\.mp3$')
PIGE_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
EXTRA_DEFAULTS = {'enabled': True, 'retention_hours': 168, 'bitrate_kbps': 128}


def load_pige_config(config_file=CONFIG_FILE):
    cfg = dict(DEFAULTS)
    try:
        with open(config_file) as f:
            cfg.update(json.load(f).get('pige', {}) or {})
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Lecture config pige impossible ({e}), valeurs par défaut")
    if not os.path.isabs(cfg['directory']):
        cfg['directory'] = os.path.join(BASE_DIR, cfg['directory'])
    return cfg


# ════════════════════════════════════════════════════════════════════
# Lecture / export (utilisé par Flask)
# ════════════════════════════════════════════════════════════════════

def load_piges(config_file=CONFIG_FILE):
    """Toutes les piges : d'abord la FM (id « fm »), puis les piges DAB+."""
    fm = load_pige_config(config_file)
    fm.update(id='fm', kind='fm', name=fm.get('name') or 'FM', reencode=False)
    out = [fm]
    try:
        with open(config_file) as f:
            full = json.load(f)
    except (OSError, ValueError):
        full = {}
    dab_port = int(((full.get('dab') or {}).get('port')) or 7979)
    for e in (full.get('pige') or {}).get('extra') or []:
        pid = str(e.get('id', '')).lower()
        sid = str(e.get('sid', '')).lower()
        if not PIGE_ID_RE.match(pid) or pid == 'fm' or not re.match(r'^[0-9a-fx]{1,8}$', sid):
            continue
        cfg = dict(fm)
        cfg.update(EXTRA_DEFAULTS)
        cfg.update({k: v for k, v in e.items() if k in ('name', 'enabled', 'retention_hours', 'bitrate_kbps')})
        cfg.update(id=pid, kind='dab', sid=sid, name=e.get('name') or f'DAB+ {sid}', reencode=True,
                   directory=os.path.join(fm['directory'], 'extra', pid),
                   source_url=f'http://127.0.0.1:{dab_port}/mp3/{sid}')
        out.append(cfg)
    return out


def pige_config(pige_id='fm', config_file=CONFIG_FILE):
    for cfg in load_piges(config_file):
        if cfg['id'] == pige_id:
            return cfg
    return None


class PigeError(Exception):
    pass


class PigeStore:
    def __init__(self, cfg=None):
        self.cfg = cfg or load_pige_config()
        self.dir = self.cfg['directory']
        self.seg_len = int(self.cfg['segment_minutes']) * 60
        self.byterate = int(self.cfg['bitrate_kbps']) * 1000 / 8

    def reload(self):
        self.__init__(pige_config(self.cfg.get('id', 'fm')) or load_pige_config())

    # ── Segments ────────────────────────────────────────────────────
    def segments(self):
        """Liste triée de dicts {name, path, start, end, size} (epoch UTC)."""
        items = []
        for path in glob.glob(os.path.join(self.dir, '*.mp3')):
            name = os.path.basename(path)
            m = SEGMENT_RE.match(name)
            if not m:
                continue
            try:
                start = datetime.strptime(m.group(1), '%Y%m%d-%H%M%S') \
                    .replace(tzinfo=timezone.utc).timestamp()
                size = os.path.getsize(path)
            except (ValueError, OSError):
                continue
            items.append({'name': name, 'path': path, 'start': start, 'size': size})
        items.sort(key=lambda s: s['start'])

        for i, s in enumerate(items):
            est_end = s['start'] + s['size'] / self.byterate
            if i + 1 < len(items):
                # Jamais au-delà du début du segment suivant
                s['end'] = min(est_end, items[i + 1]['start'])
            else:
                s['end'] = min(est_end, time.time())
        return items

    def coverage(self, segs=None, tolerance=3.0):
        """Plages continues [[début, fin], ...] fusionnées."""
        ranges = []
        for s in (segs if segs is not None else self.segments()):
            if ranges and s['start'] - ranges[-1][1] <= tolerance:
                ranges[-1][1] = max(ranges[-1][1], s['end'])
            else:
                ranges.append([s['start'], s['end']])
        return ranges

    def days(self, segs=None):
        """Secondes enregistrées par jour (heure locale), sur la durée de conservation."""
        segs = self.segments() if segs is None else segs
        out = {}
        for s in segs:
            a, b = s['start'], s['end']
            while a < b:
                d = datetime.fromtimestamp(a)
                nxt = datetime(d.year, d.month, d.day) + timedelta(days=1)
                cut = min(b, nxt.timestamp())
                key = d.strftime('%Y-%m-%d')
                out[key] = out.get(key, 0) + (cut - a)
                a = cut
        return {k: round(v) for k, v in sorted(out.items())}

    def used_bytes(self):
        return sum(s['size'] for s in self.segments())

    def segment_path(self, name):
        if not SEGMENT_RE.match(name or ''):
            return None
        path = os.path.join(self.dir, name)
        return path if os.path.isfile(path) else None

    def status(self, seg_from=None, seg_to=None):
        """État de la pige. La liste des segments est limitée à la fenêtre
        [seg_from, seg_to] (par défaut les dernières 48 h) : sur 30 jours,
        elle compterait plus de 8 000 segments à chaque rafraîchissement."""
        segs = self.segments()
        newest_mtime = max((os.path.getmtime(s['path']) for s in segs[-1:]), default=0)
        try:
            du = shutil.disk_usage(self.dir)
            free = du.free
        except OSError:
            free = None
        return {
            'id': self.cfg.get('id', 'fm'),
            'name': self.cfg.get('name', 'FM'),
            'kind': self.cfg.get('kind', 'fm'),
            'sid': self.cfg.get('sid'),
            'days': self.days(segs),
            'enabled': bool(self.cfg['enabled']),
            'recording': (time.time() - newest_mtime) < 30,
            'retention_hours': self.cfg['retention_hours'],
            'export_max_minutes': self.cfg['export_max_minutes'],
            'now': time.time(),
            'oldest': segs[0]['start'] if segs else None,
            'newest': segs[-1]['end'] if segs else None,
            'segments': [{'name': s['name'], 'start': s['start'], 'end': s['end']} for s in segs
                         if s['end'] >= (seg_from if seg_from is not None else time.time() - 48 * 3600)
                         and (seg_to is None or s['start'] <= seg_to)],
            'coverage': self.coverage(segs),
            'used_bytes': sum(s['size'] for s in segs),
            'free_bytes': free,
        }

    # ── Export ──────────────────────────────────────────────────────
    def plan_export(self, start, end):
        """Vérifie un créneau et calcule les morceaux à extraire.

        Retourne {parts: [(path, ss, to)], duration, recorded, gaps}.
        """
        if end <= start:
            raise PigeError("L'heure de fin doit être après l'heure de début")
        max_s = int(self.cfg['export_max_minutes']) * 60
        if end - start > max_s:
            raise PigeError(f"Créneau trop long (maximum {max_s // 60} min)")

        segs = self.segments()
        parts, recorded = [], 0.0
        for s in segs:
            if s['end'] <= start or s['start'] >= end:
                continue
            ss = max(0.0, start - s['start'])
            to = min(s['end'], end) - s['start']
            if to - ss < 0.05:
                continue
            parts.append((s['path'], ss, to))
            recorded += to - ss
        if not parts:
            raise PigeError("Aucun enregistrement disponible sur ce créneau")

        gaps = []
        cursor = start
        for r0, r1 in self.coverage(segs):
            if r1 <= start or r0 >= end:
                continue
            if r0 - cursor > 3:
                gaps.append([cursor, r0])
            cursor = max(cursor, r1)
        if end - cursor > 3:
            gaps.append([cursor, end])

        return {'parts': parts, 'duration': end - start,
                'recorded': recorded, 'gaps': gaps}

    def export(self, start, end, title=None):
        """Extrait [start, end] en un MP3 (copie sans réencodage).

        Retourne le chemin d'un fichier temporaire à supprimer par l'appelant.
        Les trous éventuels (coupure du flux) sont simplement absents du fichier.
        """
        plan = self.plan_export(start, end)
        workdir = tempfile.mkdtemp(prefix='export-', dir=self.dir)
        try:
            pieces = []
            for i, (path, ss, to) in enumerate(plan['parts']):
                out = os.path.join(workdir, f'part{i:03d}.mp3')
                cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y']
                if ss > 0:
                    cmd += ['-ss', f'{ss:.3f}']
                cmd += ['-i', path, '-t', f'{to - ss:.3f}',
                        '-map', '0:a', '-c', 'copy', '-write_xing', '0', out]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode == 0 and os.path.getsize(out) > 0:
                    pieces.append(out)
                else:
                    logger.warning(f"Pige export : morceau {path} ignoré ({r.stderr.strip()[:200]})")
            if not pieces:
                raise PigeError("Extraction impossible")

            listfile = os.path.join(workdir, 'list.txt')
            with open(listfile, 'w') as f:
                for p in pieces:
                    f.write(f"file '{p}'\n")

            fd, final = tempfile.mkstemp(prefix='pige-', suffix='.mp3', dir=self.dir)
            os.close(fd)
            cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'concat', '-safe', '0', '-i', listfile,
                   '-map', '0:a', '-c', 'copy']
            if title:
                cmd += ['-metadata', f'title={title}']
            cmd += [final]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                os.remove(final)
                raise PigeError(f"Assemblage impossible : {r.stderr.strip()[:200]}")
            return final
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
# Enregistreur (service systemd)
# ════════════════════════════════════════════════════════════════════

class PigeRecorder:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = PigeStore(cfg)
        self.proc = None
        self.running = True

    def purge(self):
        segs = self.store.segments()
        limit = time.time() - float(self.cfg['retention_hours']) * 3600
        for s in segs[:-1]:                      # jamais le segment en cours
            if s['end'] < limit:
                self._remove(s['path'], 'rétention')
        # Garde-fou disque
        min_free = int(self.cfg['min_free_mb']) * 1024 * 1024
        segs = self.store.segments()
        while len(segs) > 1:
            try:
                if shutil.disk_usage(self.store.dir).free >= min_free:
                    break
            except OSError:
                break
            self._remove(segs.pop(0)['path'], 'espace disque faible')
        # Exports temporaires oubliés
        for p in glob.glob(os.path.join(self.store.dir, 'pige-*.mp3')) + \
                 glob.glob(os.path.join(self.store.dir, 'export-*')):
            try:
                if time.time() - os.path.getmtime(p) > 3600:
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass

    @staticmethod
    def _remove(path, reason):
        try:
            os.remove(path)
            logger.info(f"Purge ({reason}) : {os.path.basename(path)}")
        except OSError as e:
            logger.warning(f"Purge impossible {path} : {e}")

    def _purge_loop(self):
        while self.running:
            try:
                self.purge()
            except Exception as e:
                logger.error(f"Erreur purge : {e}")
            for _ in range(60):
                if not self.running:
                    return
                time.sleep(1)

    def _ffmpeg_cmd(self):
        return [
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning',
            '-rw_timeout', '15000000',            # 15 s sans données → sortie, on relance
            '-i', self.cfg['source_url'],
            '-map', '0:a',
        ] + (['-c:a', 'libmp3lame', '-b:a', f"{int(self.cfg['bitrate_kbps'])}k", '-ar', '44100']
             if self.cfg.get('reencode') else ['-c', 'copy']) + [
            '-f', 'segment',
            # Pas d'en-tête Xing : le segment en cours d'écriture reste lisible
            # et « seekable » dans le navigateur (durée déduite du débit CBR)
            '-segment_format_options', 'write_xing=0',
            '-segment_time', str(int(self.cfg['segment_minutes']) * 60),
            '-segment_atclocktime', '1',
            '-reset_timestamps', '1',
            '-strftime', '1',
            os.path.join(self.store.dir, '%Y%m%d-%H%M%S.mp3'),
        ]

    def stop(self, *_):
        self.running = False
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def run(self, own_signals=True):
        os.makedirs(self.store.dir, exist_ok=True)
        if own_signals:
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
        threading.Thread(target=self._purge_loop, daemon=True).start()

        env = dict(os.environ, TZ='UTC')        # noms de fichiers en UTC
        logger.info(f"Pige {self.cfg.get('id', 'fm')} : {self.cfg['source_url']} → {self.store.dir} "
                    f"(segments {self.cfg['segment_minutes']} min, "
                    f"rétention {self.cfg['retention_hours']} h)")
        backoff = 2
        while self.running:
            started = time.time()
            self.proc = subprocess.Popen(self._ffmpeg_cmd(), env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         text=True)
            _, err = self.proc.communicate()
            if not self.running:
                break
            # Icecast sans source (bl-rmo qui redémarre) : on réessaie vite
            backoff = 2 if time.time() - started > 60 else min(backoff * 2, 30)
            logger.warning(f"ffmpeg pige {self.cfg.get('id', 'fm')} arrêté (code {self.proc.returncode}) "
                           f"{(err or '').strip()[-200:]} - relance dans {backoff} s")
            time.sleep(backoff)
        logger.info(f"Pige {self.cfg.get('id', 'fm')} arrêtée")


class PigeSupervisor:
    """Une pige par thread ; relit config.json toutes les 20 s pour démarrer,
    arrêter ou relancer les piges créées, supprimées ou modifiées."""

    KEYS = ('enabled', 'source_url', 'directory', 'retention_hours', 'bitrate_kbps',
            'segment_minutes', 'reencode', 'min_free_mb')

    def __init__(self):
        self.running = True
        self.recorders = {}     # id -> (recorder, thread, signature)

    def stop(self, *_):
        self.running = False
        for rec, _, _ in self.recorders.values():
            rec.stop()

    def sync(self):
        wanted = {c['id']: c for c in load_piges() if c['enabled']}
        for pid in list(self.recorders):
            rec, th, sig = self.recorders[pid]
            c = wanted.get(pid)
            if c is None or sig != tuple(c.get(k) for k in self.KEYS) or not th.is_alive():
                rec.stop()
                th.join(timeout=10)
                del self.recorders[pid]
                if c is None:
                    logger.info(f"Pige {pid} arrêtée (supprimée ou désactivée)")
        for pid, c in wanted.items():
            if pid not in self.recorders:
                rec = PigeRecorder(c)
                th = threading.Thread(target=rec.run, kwargs={'own_signals': False},
                                      name=f'pige-{pid}', daemon=True)
                th.start()
                self.recorders[pid] = (rec, th, tuple(c.get(k) for k in self.KEYS))

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while self.running:
            try:
                self.sync()
            except Exception as e:
                logger.error(f"Superviseur de pige : {e}")
            for _ in range(20):
                if not self.running:
                    break
                time.sleep(1)
        for rec, th, _ in self.recorders.values():
            th.join(timeout=10)
        logger.info("Pige arrêtée")


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - pige - %(levelname)s - %(message)s')
    if not any(c['enabled'] for c in load_piges()):
        logger.info("Aucune pige active dans config.json, arrêt.")
        # Code 0 : systemd ne relance pas en boucle
        return 0
    PigeSupervisor().run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
