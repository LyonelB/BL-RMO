#!/usr/bin/env python3
"""
Analyseur de spectre en tiers d'octave pour les pages FM et DAB+.

31 bandes normalisées de 20 Hz à 20 kHz, niveaux en dBFS (une sinusoïde pleine
échelle donne 0 dB), et niveaux G/D (efficace et crête) sur le même son.

Le calcul ne tourne que pendant qu'une page le demande : chaque client du flux
temps réel signale sa présence (touch) et l'analyse s'arrête IDLE_S secondes
après le dernier signe de vie.

  - FM : le son du tuner arrive déjà dans monitor.py, qui appelle feed().
  - DAB+ : le segment MP3 que la pige DAB+ du service est en train d'écrire
    est suivi et décodé par ffmpeg (aucun flux de plus demandé à welle-cli).
"""
import logging
import os
import subprocess
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

RATE = 48000
FFT_SIZE = 8192            # 170 ms : 5,9 Hz par point, assez fin pour les bandes graves
HOP = RATE // 10           # un résultat toutes les 100 ms
IDLE_S = 30                # arrêt après 30 s sans client
CENTERS = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800,
           1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000]
LABELS = ['20', '25', '31.5', '40', '50', '63', '80', '100', '125', '160', '200', '250', '315', '400',
          '500', '630', '800', '1k', '1.25k', '1.6k', '2k', '2.5k', '3.15k', '4k', '5k', '6.3k', '8k',
          '10k', '12.5k', '16k', '20k']
FLOOR = -120.0


def _db(x):
    return float(10.0 * np.log10(x)) if x > 1e-12 else FLOOR


class BandAnalyzer:
    """Accumule du PCM S16LE stéréo et calcule bandes et niveaux toutes les 100 ms."""

    def __init__(self, rate=RATE, size=FFT_SIZE, hop=HOP):
        self.rate, self.size, self.hop = rate, size, hop
        self.win = np.hanning(size).astype(np.float32)
        self.norm = float(np.sum(self.win ** 2)) * size     # Parseval, fenêtre comprise
        freqs = np.fft.rfftfreq(size, 1.0 / rate)
        self.bins = []
        for fc in CENTERS:
            lo, hi = fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)
            idx = np.where((freqs >= lo) & (freqs < hi))[0]
            if not len(idx):                                 # bande plus étroite qu'un point
                idx = np.array([int(np.argmin(np.abs(freqs - fc)))])
            self.bins.append((int(idx[0]), int(idx[-1]) + 1))
        self.buf = np.zeros((0, 2), dtype=np.float32)
        self.pending = 0
        self.lock = threading.Lock()
        self.result = None

    def reset(self):
        with self.lock:
            self.buf = np.zeros((0, 2), dtype=np.float32)
            self.pending = 0
            self.result = None

    def feed(self, raw):
        s = np.frombuffer(raw[:len(raw) - len(raw) % 4], dtype=np.int16)
        if not len(s):
            return
        s = s.reshape(-1, 2).astype(np.float32) / 32768.0
        with self.lock:
            self.buf = np.concatenate((self.buf, s))[-self.size:]
            self.pending += len(s)
            if self.pending < self.hop or len(self.buf) < self.size:
                return
            self.pending = 0
            block = self.buf.copy()
        self._compute(block)

    def _compute(self, block):
        recent = block[-self.hop:]
        rms = np.sqrt(np.mean(recent ** 2, axis=0))
        peak = np.max(np.abs(recent), axis=0)
        mono = block.mean(axis=1) * self.win
        power = np.abs(np.fft.rfft(mono)) ** 2
        # Puissance de la bande ramenée en dBFS efficace (sinus pleine échelle = 0 dB)
        bands = [round(_db(4.0 * float(power[a:b].sum()) / self.norm), 1) for a, b in self.bins]
        with self.lock:
            self.result = {
                't': time.time(), 'bands': bands,
                'rms': [round(_db(float(v) ** 2), 1) for v in rms],
                'peak': [round(_db(float(v) ** 2), 1) for v in peak],
            }

    def latest(self, max_age=1.0):
        with self.lock:
            r = self.result
        return r if r and time.time() - r['t'] <= max_age else None


class _Source:
    def __init__(self):
        self.analyzer = BandAnalyzer()
        self.last_touch = 0.0
        self.state = 'idle'
        self.message = ''

    def active(self):
        return time.time() - self.last_touch < IDLE_S


class FMSpectrum(_Source):
    """Le son du tuner est poussé par monitor.py : on ne fait que l'analyser."""

    def touch(self, *_):
        if not self.active():
            self.analyzer.reset()
        self.last_touch = time.time()

    def feed(self, raw):
        if self.active():
            self.analyzer.feed(raw)

    def snapshot(self):
        r = self.analyzer.latest()
        return {'state': 'ok' if r else 'waiting', 'message': '' if r else 'En attente du son du tuner', **(r or {})}


class DABSpectrum(_Source):
    """Spectre du service DAB+ affiché, lu dans sa pige pendant qu'elle s'écrit.

    Ouvrir un flux de plus sur welle-cli perturbait la réception (qualité FIC qui
    tombe à 0 à chaque ouverture de la page DAB+) : le spectre lit donc le segment
    MP3 que la pige DAB+ du service est en train d'enregistrer, sans rien demander
    à welle-cli. Un service sans pige DAB+ n'a pas de spectre.
    """

    def __init__(self, dir_for):
        super().__init__()
        self.dir_for = dir_for          # sid -> dossier de la pige DAB+ du service, ou None
        self.sid = None
        self.thread = None
        self.proc = None
        self.lock = threading.Lock()

    def touch(self, sid=None):
        sid = (sid or '').strip().lower() or None
        with self.lock:
            self.last_touch = time.time()
            if sid and sid != self.sid:
                self.sid = sid
                self.analyzer.reset()
                self._kill()
            if not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._run, daemon=True, name='dab-spectrum')
                self.thread.start()

    def _kill(self):
        p = self.proc
        if p:
            try:
                p.kill()
            except Exception:
                pass

    @staticmethod
    def _newest(directory):
        try:
            names = sorted(n for n in os.listdir(directory) if len(n) == 19 and n.endswith('.mp3') and n[8] == '-')
        except OSError:
            return None
        return os.path.join(directory, names[-1]) if names else None

    def _run(self):
        while self.active():
            sid = self.sid
            directory = self.dir_for(sid) if sid else None
            path = self._newest(directory) if directory else None
            if not path:
                self.state = 'unavailable'
                self.message = ('Pas de pige DAB+ pour ce service : lancez l\'écoute pour voir le spectre'
                                if sid else 'Aucun service choisi')
                time.sleep(2)
                continue
            try:
                stale = time.time() - os.path.getmtime(path) > 20
            except OSError:
                stale = True
            if stale:                                       # pige arrêtée ou suspendue
                self.state = 'unavailable'
                self.message = 'La pige DAB+ de ce service n\'enregistre pas : lancez l\'écoute pour voir le spectre'
                time.sleep(3)
                continue
            self.state, self.message = 'starting', 'Lecture de la pige du service…'
            try:
                self._tail(sid, directory, path)
            except Exception as e:
                logger.debug(f"Spectre DAB+ : {e}")
            finally:
                self._kill()
                self.proc = None
            if self.active() and self.sid == sid:
                time.sleep(1)
        self.state = 'idle'

    def _tail(self, sid, directory, path):
        """Suit le segment en cours (et les suivants) et décode le MP3 vers l'analyseur."""
        self.proc = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                                      '-probesize', '32768', '-analyzeduration', '0', '-fflags', 'nobuffer',
                                      '-f', 'mp3', '-i', 'pipe:0', '-f', 's16le', '-ac', '2', '-ar', str(RATE), '-flush_packets', '1', 'pipe:1'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        proc = self.proc

        def reader():
            try:
                while True:
                    chunk = proc.stdout.read(3840)
                    if not chunk:
                        return
                    self.state, self.message = 'ok', ''
                    self.analyzer.feed(chunk)
            except Exception:
                pass
        threading.Thread(target=reader, daemon=True).start()

        f = open(path, 'rb')
        try:
            f.seek(max(0, os.path.getsize(path) - 16000))     # environ 1 s avant la fin
            idle = 0.0
            while self.active() and self.sid == sid and proc.poll() is None:
                data = f.read(8192)
                if data:
                    idle = 0.0
                    proc.stdin.write(data)
                    continue
                newer = self._newest(directory)
                if newer and newer != path:                    # segment suivant
                    f.close()
                    path, f = newer, open(newer, 'rb')
                    continue
                time.sleep(0.1)
                idle += 0.1
                if idle > 15:
                    self.state, self.message = 'unavailable', 'La pige DAB+ de ce service n\'enregistre plus : lancez l\'écoute pour voir le spectre'
                    return
        finally:
            f.close()
            try:
                proc.stdin.close()
            except Exception:
                pass

    def snapshot(self):
        r = self.analyzer.latest()
        state = 'ok' if r else (self.state if self.state != 'ok' else 'starting')
        return {'state': state, 'sid': self.sid, 'message': '' if r else (self.message or 'Lecture de la pige du service…'), **(r or {})}
