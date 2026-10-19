#!/usr/bin/env python3
"""
Détection des défauts audio « numériques » sur le signal reçu par le tuner.

Le détecteur reçoit le PCM stéréo 48 kHz du tuner, par morceaux (50 ms dans
BL-RMO), et repère trois sortes de défauts produits par la chaîne de
diffusion (codecs IP, faisceau, traitement) :

  - coupure      : le programme disparaît d'un coup pendant quelques
                   millisecondes à 2 s, puis revient. Sur une chaîne IP, un
                   paquet perdu est souvent remplacé par du silence ; après
                   l'émetteur FM, il ne reste que le souffle du récepteur.
  - clic         : discontinuité brève de la forme d'onde, isolée. Un saut
                   (morceau d'audio sauté ou répété par un codec) se présente
                   ainsi : une cassure nette de la forme d'onde.
  - craquements  : série de clics rapprochés (au moins 3 en 0,5 s).

Méthodes, en numpy et scipy, sans autre dépendance :

  - Coupures : enveloppe de crête sur des blocs de 2 ms. Un défaut se
    distingue d'une pause naturelle par sa brutalité : le niveau chute de
    plus de `drop_db` dB d'un bloc à l'autre (2 ms), alors qu'une fin de
    phrase ou de morceau décroît sur des dizaines de millisecondes.
  - Clics : résidu de prédiction linéaire (LPC, ordre 16) de la voie
    centrale (L+R)/2. La musique se prédit bien, une cassure non. Un
    échantillon dont le résidu dépasse `click_k` fois le niveau robuste
    local du résidu, et qui reste isolé (pas une attaque de batterie),
    est un clic. La voie centrale élimine l'essentiel du bruit propre à
    la stéréo FM, qui se trouve dans la voie différence (L-R).

Chaque défaut est horodaté, accompagné du contexte de réception (niveau en
dBf, multitrajet) pour séparer un défaut de la chaîne d'un défaut de
réception, et d'un extrait WAV de quelques secondes du signal brut (avant
tout encodage MP3) pour l'écouter.
"""
import logging
import os
import queue
import sqlite3
import threading
import time
import wave
from datetime import datetime, timedelta

import numpy as np
from scipy.signal import butter, lfilter, sosfilt

logger = logging.getLogger(__name__)

DEFAULTS = {
    'enabled': False,         # désactivé : trop de fausses alertes sur la réception réelle
    # Coupures
    'drop_db': 30.0,          # chute minimale entre deux blocs de 2 ms
    'gap_max_dbfs': -45.0,    # niveau de crête maximal pendant le trou
    'program_min_dbfs': -40.0,  # le programme doit être présent avant le trou
    'gap_min_ms': 4,          # en dessous : ignoré
    'gap_max_ms': 2000,       # au-dessus : c'est un silence, pas une coupure
    'recover_db': 15.0,       # fin du trou : retour à moins de 15 dB du niveau d'avant
    # Clics
    'click_k': 20.0,          # seuil du résidu LPC, en multiples du niveau robuste
    'click_concentration': 0.85,  # part de l'énergie du résidu dans ±8 échantillons
    'clip_guard': 0.85,       # ignoré si le programme est à plus de 85 % de sa crête
    'click_min_dbfs': -50.0,  # programme trop faible : pas d'analyse des clics
    'crackle_count': 4,       # craquements : plus de 3 clics...
    'crackle_window_ms': 1000,  # ... en moins d'une seconde
    'group_ms': 500,          # clics distants de moins de 500 ms : même événement
    # Sauts du flux audio du tuner (capture locale, pas l'antenne) : énergie
    # au-dessus de 20 kHz. Après l'émetteur et le tuner, l'audio FM s'arrête
    # vers 15 kHz ; un saut du flux numérique, lui, couvre toute la bande.
    'hf_hz': 20000,
    'hf_db': 12.0,            # au-dessus du plancher habituel de cette bande
    'warmup_s': 5.0,          # rien n'est signalé au démarrage de la capture
    # Contexte de réception
    'rf_margin_db': 10.0,     # suspect si dBf < seuil de perte + marge
    'rf_max_multipath': 25.0, # suspect si multitrajet au-dessus (valeur brute du tuner)
    # Extraits
    'clip_pre_s': 1.5,
    'clip_post_s': 1.5,
}

LPC_ORDER = 16
FRAME = 480        # trame LPC : 10 ms
BLOCK_MS = 2


def _db(x):
    return 20.0 * np.log10(np.maximum(x, 1e-9))


def _lpc(x, order):
    """Coefficients LPC (méthode d'autocorrélation, Levinson-Durbin)."""
    n = len(x)
    w = x * np.hanning(n)
    full = np.correlate(w, w, mode='full')[n - 1:n + order]
    r = full.astype(np.float64)
    if r[0] <= 1e-12:
        return None
    r[0] *= 1.0 + 1e-6          # léger plancher de bruit, stabilité numérique
    a = np.zeros(order + 1)
    a[0] = 1.0
    err = r[0]
    for i in range(1, order + 1):
        acc = r[i] + np.dot(a[1:i], r[i - 1:0:-1])
        k = -acc / err
        a[1:i] = a[1:i] + k * a[i - 1:0:-1]
        a[i] = k
        err *= 1.0 - k * k
        if err <= 0:
            return None
    return a


class AudioDefectDetector:
    """
    feed(pcm) : pcm en int16 entrelacé (bytes) ou tableau (N, 2).
    on_event(event, clip) : appelé quand un défaut est terminé et que la
      fin de l'extrait est disponible ; clip est un tableau int16 (N, 2).
    rf_context() : renvoie {'dbf': .., 'multipath': .., 'snr': ..} ou None.
    """

    def __init__(self, sample_rate=48000, params=None, on_event=None,
                 rf_context=None, signal_threshold_dbf=20.0, clock=time.time):
        self.sr = sample_rate
        self.clock = clock
        self.p = dict(DEFAULTS)
        if params:
            self.p.update({k: v for k, v in params.items() if k in DEFAULTS})
        self.on_event = on_event
        self.rf_context = rf_context
        self.signal_threshold_dbf = float(signal_threshold_dbf)

        self.block = int(self.sr * BLOCK_MS / 1000)
        self.total = 0                       # échantillons reçus depuis le début
        self.wall_end = clock()              # heure du dernier échantillon reçu

        # Mémoire circulaire pour les extraits
        self.ring_len = int(self.sr * (self.p['clip_pre_s'] + self.p['clip_post_s'] + 3.0))
        self.ring = np.zeros((self.ring_len, 2), dtype=np.int16)

        # Coupures
        self._pend = np.zeros((0, 2), dtype=np.float32)  # reste < 1 bloc
        self._pend_start = 0
        self._lv_hist = []                   # niveaux des 10 derniers blocs
        self._gap = None                     # trou en cours
        self._recent_gaps = []               # (début, fin) en échantillons

        # Clics
        self._m_hist = np.zeros(0)           # voie centrale, 20 dernières ms
        self._e_tail = np.zeros(0)           # résidu, 5 dernières ms
        self._mm_tail = np.zeros(0)
        self._peaks = []                     # crêtes des derniers morceaux
        self._group = None                   # groupe de clics en cours

        # Sauts du flux du tuner
        self._hf_sos = butter(8, min(0.99, self.p['hf_hz'] / (self.sr / 2)), 'high', output='sos')
        self._hf_zi = np.zeros((self._hf_sos.shape[0], 2, 2))
        self._hf_medians = []                # plancher : médianes des derniers morceaux
        self._hf_run = None                  # saut en cours
        self._glitches = []                  # (début, fin) des derniers sauts

        self._waiting = []                   # événements en attente de leur extrait
        self.counters = {'coupure': 0, 'clic': 0, 'craquements': 0, 'capture': 0}

    # ── Entrée ──────────────────────────────────────────────────────

    def feed(self, pcm):
        if isinstance(pcm, (bytes, bytearray, memoryview)):
            pcm = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 2)
        if len(pcm) == 0:
            return
        start = self.total
        self._ring_write(pcm)
        self.total += len(pcm)
        self.wall_end = self.clock()
        x = pcm.astype(np.float32) / 32768.0
        try:
            self._hf(x, start)
            self._gaps(x, start)
            self._clicks(x, start)
        except Exception as e:                # ne jamais casser la capture
            logger.debug(f"AudioDefectDetector : {e}")
        self._flush()

    def _ring_write(self, pcm):
        n = len(pcm)
        if n >= self.ring_len:
            pcm = pcm[-self.ring_len:]
            n = self.ring_len
        i = self.total % self.ring_len
        first = min(n, self.ring_len - i)
        self.ring[i:i + first] = pcm[:first]
        if first < n:
            self.ring[:n - first] = pcm[first:]

    def _ring_read(self, a, b):
        a = max(a, self.total - self.ring_len, 0)
        b = min(b, self.total)
        if b <= a:
            return np.zeros((0, 2), dtype=np.int16)
        idx = np.arange(a, b) % self.ring_len
        return self.ring[idx].copy()

    def _wall(self, sample):
        return self.wall_end - (self.total - sample) / self.sr

    # ── Coupures ────────────────────────────────────────────────────

    def _gaps(self, x, start):
        if len(self._pend):
            x = np.concatenate([self._pend, x])
            start = self._pend_start
        nb = len(x) // self.block
        self._pend = x[nb * self.block:]
        self._pend_start = start + nb * self.block
        if nb == 0:
            return
        blocks = np.abs(x[:nb * self.block]).reshape(nb, self.block, 2).max(axis=(1, 2))
        levels = _db(blocks)
        p = self.p
        for j, lv in enumerate(levels):
            pos = start + j * self.block
            hist = self._lv_hist
            if self._gap is None:
                if len(hist) >= 5:
                    before = max(hist[-5:])        # niveau du programme sur 10 ms
                    last = hist[-1]
                    # Programme présent sur la durée (médiane), pas un clic isolé
                    # dans un passage calme, qui retomberait aussitôt.
                    steady = sorted(hist[-5:])[2]
                    if (before >= p['program_min_dbfs'] and steady >= p['program_min_dbfs']
                            and lv <= p['gap_max_dbfs']
                            and lv <= before - p['drop_db']
                            and last - lv >= p['drop_db'] - 10.0):
                        self._gap = {'start': pos, 'before': before, 'floor': lv,
                                     'fall_db': last - lv, 'low_blocks': 1}
            else:
                g = self._gap
                if lv >= g['before'] - p['recover_db']:
                    dur_ms = (pos - g['start']) * 1000.0 / self.sr
                    if g['low_blocks'] >= 2 and dur_ms >= p['gap_min_ms']:
                        self._emit_gap(g, pos, dur_ms, rise_db=lv - hist[-1])
                    self._gap = None
                else:
                    g['floor'] = max(g['floor'], lv)
                    g['low_blocks'] += 1
                    if (pos - g['start']) * 1000.0 / self.sr > p['gap_max_ms']:
                        self._gap = None       # silence long : géré par l'alerte modulation
            hist.append(lv)
            if len(hist) > 10:
                del hist[0]

    # ── Sauts du flux du tuner ──────────────────────────────────────

    def _hf(self, x, start):
        y, self._hf_zi = sosfilt(self._hf_sos, x, axis=0, zi=self._hf_zi)
        b = 48                                            # blocs de 1 ms
        nb = len(y) // b
        if nb == 0:
            return
        rms = np.sqrt((y[:nb * b].reshape(nb, b, 2) ** 2).mean(axis=(1, 2))) + 1e-9
        self._hf_medians.append(float(np.median(rms)))
        if len(self._hf_medians) > 40:                    # 2 s de plancher
            del self._hf_medians[0]
        if len(self._hf_medians) < 10:
            return
        floor = float(np.median(self._hf_medians))
        db = 20 * np.log10(rms / floor)
        for j in np.flatnonzero(db > self.p['hf_db']):
            pos = start + int(j) * b
            r = self._hf_run
            if r and pos - r['end'] <= 5 * b:
                r['end'] = pos + b
                r['peak'] = max(r['peak'], float(db[j]))
            else:
                if r:
                    self._emit_glitch(r)
                self._hf_run = {'start': pos, 'end': pos + b, 'peak': float(db[j])}
        r = self._hf_run
        if r and start + nb * b - r['end'] > 5 * b:
            self._emit_glitch(r)

    def _emit_glitch(self, r):
        self._hf_run = None
        self._glitches.append((r['start'], r['end']))
        self._glitches = self._glitches[-50:]
        self._queue({
            'type': 'capture',
            'start': r['start'], 'end': r['end'],
            'duration_ms': round((r['end'] - r['start']) * 1000.0 / self.sr, 1),
            'count': 1,
            'level_before_dbfs': None,
            'depth_db': round(r['peak'], 1),
            'detail': f"saut du flux audio du tuner : +{r['peak']:.0f} dB au-dessus de "
                      f"{self.p['hf_hz'] / 1000:g} kHz, hors de la bande FM",
        })

    def _near_glitch(self, a, b=None, margin_ms=3):
        m = int(self.sr * margin_ms / 1000)
        b = a if b is None else b
        r = self._hf_run
        spans = self._glitches + ([(r['start'], r['end'])] if r else [])
        return any(g0 - m <= b and a <= g1 + m for g0, g1 in spans)

    def _emit_gap(self, g, end, dur_ms, rise_db):
        self._recent_gaps.append((g['start'], end))
        self._recent_gaps = self._recent_gaps[-20:]
        if g['floor'] <= -100.0:
            # Zéros exacts : impossible après une réception FM (il reste le
            # souffle du récepteur). Le trou vient de la capture.
            self._queue({
                'type': 'capture', 'start': g['start'], 'end': end,
                'duration_ms': round(dur_ms, 1), 'count': 1,
                'level_before_dbfs': round(float(g['before']), 1), 'depth_db': None,
                'detail': f"silence numérique de {dur_ms:.0f} ms (zéros exacts) : trou dans la capture",
            })
            return
        ev = {
            'type': 'coupure',
            'start': g['start'], 'end': end,
            'duration_ms': round(dur_ms, 1),
            'count': 1,
            'level_before_dbfs': round(float(g['before']), 1),
            'depth_db': round(float(g['before'] - g['floor']), 1),
            'detail': f"chute de {g['fall_db']:.0f} dB en {BLOCK_MS} ms, "
                      f"remontée de {rise_db:.0f} dB",
        }
        self._queue(ev)

    # ── Clics ───────────────────────────────────────────────────────

    def _clicks(self, x, start):
        """Résidu LPC trame par trame (10 ms, prédicteur sur les 20 ms d'avant), puis
        recherche de pics isolés. Les 5 derniers ms du morceau précédent
        servent de contexte pour ne pas manquer un clic à la jointure."""
        p = self.p
        m = ((x[:, 0] + x[:, 1]) * 0.5).astype(np.float64)
        n = len(m)
        hist = np.concatenate([self._m_hist, m])       # contexte + morceau
        off = len(self._m_hist)
        e = np.zeros(n)
        F = FRAME
        for f0 in range(0, n, F):
            f1 = min(n, f0 + F)
            # Prédicteur appris sur les 20 ms qui précèdent la trame : un
            # clic dans la trame ne peut pas « s'apprendre » lui-même.
            w = hist[max(0, off + f0 - 2 * F):off + f0]
            a = _lpc(w, LPC_ORDER) if len(w) >= F else None
            seg = hist[max(0, off + f0 - LPC_ORDER):off + f1]
            if a is None or off + f0 < LPC_ORDER:
                continue
            e[f0:f1] = lfilter(a, [1.0], seg)[-(f1 - f0):]
        self._m_hist = hist[-2 * F:]

        peak_now = float(np.max(np.abs(m))) if n else 0.0
        self._peaks.append(peak_now)
        if len(self._peaks) > 40:                      # 2 s de crêtes
            del self._peaks[0]
        prog_peak = max(self._peaks)

        # Résidu avec 5 ms du morceau précédent, pour les pics en bordure
        ctx = len(self._e_tail)
        ee = np.concatenate([self._e_tail, e])
        mm = np.concatenate([self._mm_tail, m])
        sb = int(self.sr * 0.005)
        self._e_tail = e[-sb:].copy()
        self._mm_tail = m[-sb:].copy()
        if peak_now < 10 ** (p['click_min_dbfs'] / 20.0):
            return
        nb = len(ee) // sb
        if nb < 3:
            return
        ae = np.abs(ee[:nb * sb]).reshape(nb, sb)
        sig = 1.4826 * np.median(ae, axis=1) + 1e-7
        sig_n = np.maximum(sig, np.maximum(np.r_[sig[0], sig[:-1]], np.r_[sig[1:], sig[-1]]))
        thr = np.repeat(sig_n, sb) * p['click_k']
        cand = np.flatnonzero(np.abs(ee[:nb * sb]) > thr)
        half = int(self.sr * 0.0025)
        last_peak = -10 ** 9
        for i in cand:
            if i - last_peak < half:
                continue
            lo, hi = max(0, i - half), min(len(ee), i + half)
            pk = lo + int(np.argmax(np.abs(ee[lo:hi])))
            last_peak = pk
            if pk < ctx:
                continue                               # déjà vu au morceau précédent
            # Isolement : un clic concentre son énergie sur quelques
            # échantillons (lobe du filtre 15 kHz de la chaîne FM) ; une
            # attaque musicale l'étale sur des millisecondes.
            win = ee[max(0, pk - half):pk + half]
            core = ee[max(0, pk - 8):pk + 9]
            conc = float(np.sum(core ** 2) / (np.sum(win ** 2) + 1e-20))
            if conc < p['click_concentration']:
                continue
            # Écrêteur du processeur : ses angles ressemblent à des clics,
            # mais seulement quand le programme touche sa crête.
            around = np.concatenate([mm[max(0, pk - 60):max(0, pk - 10)], mm[pk + 10:pk + 60]])
            if len(around) and np.max(np.abs(around)) > p['clip_guard'] * prog_peak:
                continue
            self._add_click(start + pk - ctx, float(np.abs(ee[pk]) / sig_n[min(pk // sb, nb - 1)]))

    def _add_click(self, pos, ratio):
        if self._near_glitch(pos):
            return                                 # c'est le saut du tuner, déjà signalé
        g = self._group
        gap = int(self.sr * self.p['group_ms'] / 1000)
        if g and pos - g['end'] <= gap:
            g['end'] = pos
            g['count'] += 1
            g['ratio'] = max(g['ratio'], ratio)
            g['pos'].append(pos)
        else:
            if g:
                self._close_group()
            self._group = {'start': pos, 'end': pos, 'count': 1, 'ratio': ratio, 'pos': [pos]}

    def _close_group(self):
        g, self._group = self._group, None
        if not g:
            return
        margin = int(self.sr * 0.02)
        for a, b in self._recent_gaps:           # bords d'une coupure : déjà signalée
            if a - margin <= g['start'] and g['end'] <= b + margin:
                return
        # Clics qui tombent sur un saut du tuner : déjà signalés comme tels
        pos = [q for q in g['pos'] if not self._near_glitch(q)]
        if not pos:
            return
        if len(pos) != len(g['pos']):
            g.update(pos=pos, count=len(pos), start=pos[0], end=pos[-1])
        n = g['count']
        # Plus grand nombre de clics tenant dans une fenêtre d'une seconde
        win = int(self.sr * self.p['crackle_window_ms'] / 1000)
        pos, best, j = g['pos'], 0, 0
        for i in range(len(pos)):
            while pos[i] - pos[j] >= win:
                j += 1
            best = max(best, i - j + 1)
        kind = 'craquements' if best >= self.p['crackle_count'] else 'clic'
        sec = self.p['crackle_window_ms'] / 1000
        ev = {
            'type': kind,
            'start': g['start'], 'end': g['end'],
            'duration_ms': round((g['end'] - g['start']) * 1000.0 / self.sr, 1),
            'count': n,
            'max_per_s': best,
            'level_before_dbfs': None,
            'depth_db': round(20 * np.log10(g['ratio']), 1),
            'detail': (f"{n} clics, jusqu'à {best} en moins de {sec:g} s, " if n > 1 else "1 clic, ")
                      + f"pic à {g['ratio']:.0f} fois le résidu habituel",
        }
        self._queue(ev)

    # ── Sortie ──────────────────────────────────────────────────────

    def _queue(self, ev):
        if ev['start'] < self.p['warmup_s'] * self.sr:
            return                                 # démarrage de la capture
        ev['time'] = self._wall(ev['start'])
        rf = None
        if self.rf_context:
            try:
                rf = self.rf_context()
            except Exception:
                rf = None
        ev['rf'] = rf
        ev['rf_suspect'] = self._rf_suspect(rf)
        ev['ready_at'] = ev['end'] + int(self.p['clip_post_s'] * self.sr)
        self._waiting.append(ev)

    def _rf_suspect(self, rf):
        if not rf:
            return False
        dbf = rf.get('dbf')
        mp = rf.get('multipath')
        if dbf is not None and dbf < self.signal_threshold_dbf + self.p['rf_margin_db']:
            return True
        if mp is not None and mp > self.p['rf_max_multipath']:
            return True
        return False

    def _flush(self):
        # Un groupe de clics se ferme quand plus rien n'arrive depuis group_ms,
        # et jamais pendant une coupure en cours (ses bords font des clics).
        g = self._group
        if g and self._gap is None and self._hf_run is None \
                and self.total - g['end'] > int(self.sr * self.p['group_ms'] / 1000):
            self._close_group()
        if not self._waiting:
            return
        keep = []
        for ev in self._waiting:
            if self.total < ev['ready_at']:
                keep.append(ev)
                continue
            a = ev['start'] - int(self.p['clip_pre_s'] * self.sr)
            clip = self._ring_read(a, ev['ready_at']) if ev['type'] != 'capture' else None
            ev['clip_offset_s'] = round((ev['start'] - max(a, 0)) / self.sr, 3)
            self.counters[ev['type']] += 1
            for k in ('start', 'end', 'ready_at'):
                ev.pop(k, None)
            if self.on_event:
                try:
                    self.on_event(ev, clip)
                except Exception as e:
                    logger.error(f"Défaut audio, enregistrement : {e}")
        self._waiting = keep


# ════════════════════════════════════════════════════════════════════
# Enregistrement : table audio_defects (fm_monitor.db) et extraits WAV
# ════════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'fm_monitor.db')
CLIP_DIR = os.path.join(BASE_DIR, 'audio_defects')

TYPE_LABELS = {'coupure': 'Micro-coupure', 'clic': 'Clic', 'craquements': 'Craquements',
               'capture': 'Saut du tuner'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS audio_defects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,          -- heure locale ISO, à la milliseconde
    type TEXT NOT NULL,               -- coupure | clic | craquements | capture (saut local du tuner)
    duration_ms REAL,
    count INTEGER,
    depth_db REAL,                    -- coupure : profondeur ; clic : pic du résidu (dB)
    level_before_dbfs REAL,
    detail TEXT,
    dbf REAL, multipath REAL, snr REAL,
    max_per_s INTEGER,                -- clics : nombre maximal dans une fenêtre d'1 s
    rf_suspect INTEGER DEFAULT 0,     -- réception douteuse au même moment
    clip TEXT DEFAULT ''              -- chemin relatif de l'extrait WAV
);
CREATE INDEX IF NOT EXISTS idx_audio_defects_ts ON audio_defects(timestamp);
'''


class AudioDefectStore:
    """Écritures dans un fil à part : la capture audio ne doit jamais attendre
    le disque (le tube ffmpeg ne tient que quelques centaines de ms)."""

    def __init__(self, db_path=DB_FILE, clip_dir=CLIP_DIR, keep_days=30, clip_days=7,
                 max_clips_per_hour=30):
        self.db_path = db_path
        self.clip_dir = clip_dir
        self.keep_days = int(keep_days)
        self.clip_days = int(clip_days)
        self.max_clips_per_hour = int(max_clips_per_hour)
        self._q = queue.Queue(maxsize=200)
        self._clip_times = []
        self._last_purge = 0.0
        self._thread = None
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r['name'] for r in c.execute('PRAGMA table_info(audio_defects)')}
            if 'max_per_s' not in cols:                 # table créée par la première version
                c.execute('ALTER TABLE audio_defects ADD COLUMN max_per_s INTEGER')

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=5)
        c.row_factory = sqlite3.Row
        return c

    # ── écriture ────────────────────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True, name='audio-defects')
        self._thread.start()

    def submit(self, ev, clip):
        try:
            self._q.put_nowait((ev, clip))
        except queue.Full:
            logger.warning("Défauts audio : file pleine, événement ignoré")

    def _worker(self):
        while True:
            ev, clip = self._q.get()
            try:
                self.save(ev, clip)
                if time.time() - self._last_purge > 3600:
                    self.purge()
            except Exception as e:
                logger.error(f"Défauts audio : {e}")

    def save(self, ev, clip=None, sample_rate=48000):
        ts = datetime.fromtimestamp(ev['time'])
        rel = ''
        now = time.time()
        self._clip_times = [t for t in self._clip_times if t > now - 3600]
        if clip is not None and len(clip) and len(self._clip_times) < self.max_clips_per_hour:
            rel = os.path.join(ts.strftime('%Y-%m-%d'), f"{ts.strftime('%H%M%S_%f')[:10]}_{ev['type']}.wav")
            path = os.path.join(self.clip_dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with wave.open(path, 'wb') as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(sample_rate)
                w.writeframes(np.ascontiguousarray(clip, dtype='<i2').tobytes())
            self._clip_times.append(now)
        rf = ev.get('rf') or {}
        with self._conn() as c:
            c.execute('INSERT INTO audio_defects (timestamp, type, duration_ms, count, max_per_s, depth_db, '
                      'level_before_dbfs, detail, dbf, multipath, snr, rf_suspect, clip) '
                      'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (ts.isoformat(timespec='milliseconds'), ev['type'], ev.get('duration_ms'),
                       ev.get('count', 1), ev.get('max_per_s'), ev.get('depth_db'), ev.get('level_before_dbfs'),
                       ev.get('detail', ''), rf.get('dbf'), rf.get('multipath'), rf.get('snr'),
                       1 if ev.get('rf_suspect') else 0, rel))
        logger.info(f"Défaut audio : {TYPE_LABELS.get(ev['type'], ev['type'])}, "
                    f"{ev.get('duration_ms', 0):.0f} ms, {ev.get('detail', '')}"
                    + (" (réception douteuse)" if ev.get('rf_suspect') else ''))

    def purge(self):
        self._last_purge = time.time()
        cut_ev = (datetime.now() - timedelta(days=self.keep_days)).isoformat()
        cut_clip = (datetime.now() - timedelta(days=self.clip_days)).strftime('%Y-%m-%d')
        with self._conn() as c:
            c.execute('DELETE FROM audio_defects WHERE timestamp < ?', (cut_ev,))
            c.execute("UPDATE audio_defects SET clip='' WHERE clip != '' AND substr(clip, 1, 10) < ?", (cut_clip,))
        if os.path.isdir(self.clip_dir):
            for d in os.listdir(self.clip_dir):
                if len(d) == 10 and d < cut_clip:
                    full = os.path.join(self.clip_dir, d)
                    for f in os.listdir(full):
                        try:
                            os.remove(os.path.join(full, f))
                        except OSError:
                            pass
                    try:
                        os.rmdir(full)
                    except OSError:
                        pass

    # ── lecture (application web) ───────────────────────────────────
    def events(self, hours=24, limit=500, include_suspect=True, include_local=True):
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        q = 'SELECT * FROM audio_defects WHERE timestamp >= ?'
        if not include_suspect:
            q += ' AND rf_suspect = 0'
        if not include_local:
            q += " AND type != 'capture'"
        q += ' ORDER BY timestamp DESC LIMIT ?'
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, (since, int(limit)))]
        for r in rows:
            r['label'] = TYPE_LABELS.get(r['type'], r['type'])
            r['has_clip'] = bool(r['clip']) and os.path.isfile(os.path.join(self.clip_dir, r['clip']))
        return rows

    def summary(self):
        """Nombre d'événements par type sur 24 h et 7 jours (réception douteuse à part)."""
        out = {}
        with self._conn() as c:
            for label, h in (('24h', 24), ('7j', 168)):
                since = (datetime.now() - timedelta(hours=h)).isoformat()
                d = {t: 0 for t in TYPE_LABELS}
                d['rf_suspect'] = 0
                for r in c.execute('SELECT type, rf_suspect, COUNT(*) n FROM audio_defects '
                                   'WHERE timestamp >= ? GROUP BY type, rf_suspect', (since,)):
                    if r['rf_suspect'] and r['type'] != 'capture':
                        d['rf_suspect'] += r['n']
                    else:
                        d[r['type']] = d.get(r['type'], 0) + r['n']
                out[label] = d
        return out

    def export_zip(self, path, hours=24, with_clips=True, max_clips=300, params=None, context=None):
        """Écrit dans `path` un ZIP pour analyse : CSV des défauts, réglages
        du détecteur et, au choix, les extraits WAV (les plus récents d'abord,
        `max_clips` au plus). Renvoie (nombre de défauts, nombre d'extraits)."""
        import csv
        import io
        import json
        import zipfile
        rows = self.events(hours=hours, limit=100000)
        cols = ['id', 'timestamp', 'type', 'duration_ms', 'count', 'max_per_s', 'depth_db', 'level_before_dbfs',
                'detail', 'dbf', 'multipath', 'snr', 'rf_suspect', 'clip']
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=';', lineterminator='\n')
        w.writerow(cols + ['extrait_inclus'])
        clips = []
        for r in rows:
            inc = with_clips and r['has_clip'] and len(clips) < max_clips
            if inc:
                clips.append(r)
            w.writerow([r.get(c) if r.get(c) is not None else '' for c in cols]
                       + ['oui' if inc else 'non'])
        info = {
            'genere': datetime.now().isoformat(timespec='seconds'),
            'periode_heures': hours,
            'defauts': len(rows),
            'extraits_inclus': len(clips),
            'extraits_max': max_clips,
            'reglages_detecteur': dict(DEFAULTS, **{k: v for k, v in (params or {}).items() if k in DEFAULTS}),
            'contexte': context or {},
            'format_extraits': 'WAV PCM 16 bits, 48 kHz, stéréo, signal brut du tuner ; '
                               'le défaut commence à clip_pre_s secondes du début du fichier',
        }
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('defauts_audio.csv', buf.getvalue().encode('utf-8-sig'))
            z.writestr('reglages.json', json.dumps(info, ensure_ascii=False, indent=2))
            for r in clips:
                z.write(os.path.join(self.clip_dir, r['clip']),
                        arcname='extraits/' + f"{r['id']:06d}_" + os.path.basename(r['clip']),
                        compress_type=zipfile.ZIP_STORED)
        return len(rows), len(clips)

    def clip_path(self, event_id):
        with self._conn() as c:
            r = c.execute('SELECT clip FROM audio_defects WHERE id = ?', (int(event_id),)).fetchone()
        if not r or not r['clip']:
            return None
        path = os.path.realpath(os.path.join(self.clip_dir, r['clip']))
        if not path.startswith(os.path.realpath(self.clip_dir) + os.sep) or not os.path.isfile(path):
            return None
        return path
