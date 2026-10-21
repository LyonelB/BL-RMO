#!/usr/bin/env python3
"""
Analyseur de spectre en tiers d'octave pour les pages FM et DAB+.

31 bandes normalisées de 20 Hz à 20 kHz, niveaux en dBFS (une sinusoïde pleine
échelle donne 0 dB), et niveaux G/D (efficace et crête) sur le même son.

Le calcul ne tourne que pendant qu'une page le demande : chaque client du flux
temps réel signale sa présence (touch) et l'analyse s'arrête IDLE_S secondes
après le dernier signe de vie.

  - FM : le son du tuner arrive déjà dans monitor.py, qui appelle feed().
  - DAB+ : le service affiché est décodé par ffmpeg depuis welle-cli
    (http://127.0.0.1:<port>/mp3/<sid>, le flux qui sert déjà à la pige).
"""
import logging
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
    """Décode le service affiché avec ffmpeg, tant qu'une page le demande."""

    def __init__(self, url_for):
        super().__init__()
        self.url_for = url_for          # sid -> URL du flux MP3 de welle-cli, ou None
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
                self._kill()                       # le fil de lecture repart sur le nouveau service
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

    def _run(self):
        while self.active():
            sid = self.sid
            url = self.url_for(sid) if sid else None
            if not url:
                self.state, self.message = 'unavailable', 'Réception DAB+ désactivée ou service inconnu'
                time.sleep(2)
                continue
            self.state, self.message = 'starting', 'Décodage du service…'
            cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-reconnect', '1', '-reconnect_delay_max', '2', '-i', url,
                   '-vn', '-f', 's16le', '-ac', '2', '-ar', str(RATE), 'pipe:1']
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                while self.active() and self.sid == sid:
                    chunk = self.proc.stdout.read(4800 * 4 // 5)     # 20 ms
                    if not chunk:
                        break
                    self.state, self.message = 'ok', ''
                    self.analyzer.feed(chunk)
            except Exception as e:
                logger.debug(f"Spectre DAB+ : {e}")
            finally:
                self._kill()
                self.proc = None
            if self.active() and self.sid == sid:
                self.state, self.message = 'unavailable', 'Service non disponible pour le moment'
                time.sleep(3)
        self.state = 'idle'

    def snapshot(self):
        r = self.analyzer.latest()
        state = 'ok' if r else (self.state if self.state != 'ok' else 'starting')
        return {'state': state, 'sid': self.sid, 'message': '' if r else (self.message or 'Décodage du service…'), **(r or {})}
