"""
tef_driver.py — Driver TEF668X Headless USB Tuner pour BL-RMO
Protocole XDR-GTK via port série USB CDC (/dev/ttyACM0)

Format des lignes reçues :
  Ss<dBf>,<SNR>,<multipath>,<offset>  — métriques signal FM
  P<PI_hex>                           — code PI RDS (Block A)
  R<BlockB><BlockC><BlockD><Status>   — groupe RDS (14 hex chars)
  T<freq_kHz>,<bw>                    — confirmation tune
  M<mode>                             — confirmation mode
"""

import serial
import threading
import time
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Décodeur RDS léger (Groupes 0A → PS, 2A → RT)
# ─────────────────────────────────────────────

class RDSDecoder:
    """
    Décode les groupes RDS reçus du TEF668X.
    Callbacks : on_ps(ps_str), on_rt(rt_str)
    """

    def __init__(self, on_ps=None, on_rt=None, on_ms=None):
        self.on_ps = on_ps
        self.on_rt = on_rt
        self.on_ms = on_ms
        self.reset()

    def reset(self):
        self._ps_buf    = [' '] * 8
        self._ps_seen   = [False] * 4
        self._ps_prev   = ''          # PS du cycle précédent
        self._rt_buf    = [' '] * 64
        self._rt_seen   = [False] * 16
        self._rt_ab     = None
        self._rt_len    = 64

    def feed(self, block_b, block_c, block_d, status):
        """Traite un groupe RDS (blocks B/C/D + octet status)."""
        group_type = (block_b >> 12) & 0x0F
        version    = (block_b >> 11) & 0x01   # 0 = A, 1 = B

        if group_type == 0:
            self._group_0(block_b, block_d)
        elif group_type == 2 and version == 0:
            self._group_2a(block_b, block_c, block_d)

    # ── Groupe 0A/0B : PS (8 caractères, 4 segments de 2) ──────────────

    def _group_0(self, block_b, block_d):
        seg = block_b & 0x03
        ms  = (block_b >> 3) & 0x01   # bit MS : 1=Stéréo/Musique, 0=Mono/Parole
        if self.on_ms:
            self.on_ms(bool(ms))
        c1, c2 = chr(block_d >> 8), chr(block_d & 0xFF)
        if self._printable(c1) and self._printable(c2):
            self._ps_buf[seg * 2]     = c1
            self._ps_buf[seg * 2 + 1] = c2
            self._ps_seen[seg] = True
            if all(self._ps_seen):
                ps = ''.join(self._ps_buf).strip()
                if ps:
                    if ps == self._ps_prev:
                        # Deux cycles consécutifs identiques → PS stable
                        if self.on_ps:
                            self.on_ps(ps)
                    else:
                        self._ps_prev = ps
                # Réarmer pour le prochain cycle
                self._ps_seen = [False] * 4

    # ── Groupe 2A : RadioText (64 caractères, 16 segments de 4) ────────

    def _group_2a(self, block_b, block_c, block_d):
        ab  = (block_b >> 4) & 0x01
        seg = block_b & 0x0F

        # Changement du flag A/B → début d'un nouveau RT
        if self._rt_ab is not None and ab != self._rt_ab:
            self._rt_buf  = [' '] * 64
            self._rt_seen = [False] * 16
            self._rt_len  = 64

        self._rt_ab = ab
        chars = [
            chr(block_c >> 8), chr(block_c & 0xFF),
            chr(block_d >> 8), chr(block_d & 0xFF),
        ]

        for i, c in enumerate(chars):
            pos = seg * 4 + i
            if pos >= 64:
                break
            if c == '\r':                        # fin de RT explicite
                self._rt_len = pos
                self._rt_seen[seg] = True
                rt = ''.join(self._rt_buf[:self._rt_len]).strip()
                if rt and self.on_rt:
                    self.on_rt(rt)
                return
            if self._printable(c):
                self._rt_buf[pos] = c

        self._rt_seen[seg] = True

        # RT complet quand tous les segments jusqu'à _rt_len sont vus
        segs_needed = (self._rt_len + 3) // 4
        if all(self._rt_seen[:segs_needed]):
            rt = ''.join(self._rt_buf[:self._rt_len]).strip()
            if rt and self.on_rt:
                self.on_rt(rt)

    @staticmethod
    def _printable(c):
        return 32 <= ord(c) <= 126


# ─────────────────────────────────────────────
# Driver TEF668X
# ─────────────────────────────────────────────

class TEFDriver:
    """
    Pilote série pour TEF668X Headless USB Tuner (FM-DX-Tuner firmware).

    Utilisation :
        driver = TEFDriver(
            port='/dev/ttyACM0',
            on_signal=lambda dbf, snr, mp, off: ...,
            on_pi=lambda pi: ...,
            on_ps=lambda ps: ...,
            on_rt=lambda rt: ...,
        )
        driver.start(freq_khz=88600)
        driver.tune(87500)
        driver.stop()
    """

    _INIT_CMDS = [
        b'x\n',      # info / réveil
        b'Q0\n',     # squelch désactivé
        b'M0\n',     # mode FM
        b'Z1\n',     # antenne 1
        b'A0\n',     # AGC automatique
        b'F-1\n',    # filtre automatique
        b'W0\n',     # largeur de bande automatique
        b'D1\n',     # décodage RDS actif
        b'Y100\n',   # volume 100 %
    ]

    def __init__(self, port='/dev/ttyACM0',
                 on_signal=None, on_pi=None, on_ps=None, on_rt=None, on_ms=None):
        self.port      = port
        self.on_signal = on_signal
        self.on_pi     = on_pi
        self._rds      = RDSDecoder(on_ps=on_ps, on_rt=on_rt, on_ms=on_ms)
        self.stereo      = False     # indicateur stéréo du tuner (trames Ss / Sm)
        self.stereo_seen = False

        self._ser           = None
        self._thread        = None
        self._running       = False
        self._freq_khz      = None
        self._lock          = threading.Lock()

    # ── API publique ────────────────────────────────────────────────────

    def start(self, freq_khz):
        """Démarre la lecture série et tune sur freq_khz."""
        self._freq_khz = int(freq_khz)
        self._running  = True
        self._thread   = threading.Thread(target=self._loop, daemon=True,
                                          name='tef-serial')
        self._thread.start()
        logger.info(f"TEFDriver démarré — {self.port} @ {freq_khz} kHz")

    def stop(self):
        """Arrête proprement le driver."""
        self._running = False
        with self._lock:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=4)
        logger.info("TEFDriver arrêté")

    def tune(self, freq_khz):
        """Change la fréquence (thread-safe)."""
        self._freq_khz = int(freq_khz)
        self._rds.reset()
        self._write(f'T{self._freq_khz}\n'.encode())
        logger.info(f"TEF tune → {freq_khz} kHz")

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    # ── Boucle interne ──────────────────────────────────────────────────

    def _loop(self):
        retry = 5
        while self._running:
            try:
                with self._lock:
                    self._ser = serial.Serial(self.port, 115200, timeout=1)
                    self._ser.dtr = True
                time.sleep(0.3)
                self._send_init()
                self._read_loop()
            except serial.SerialException as e:
                logger.error(f"TEF port série: {e}")
            except Exception as e:
                logger.error(f"TEF erreur inattendue: {e}")
            finally:
                with self._lock:
                    if self._ser:
                        try:
                            self._ser.close()
                        except Exception:
                            pass
                        self._ser = None
            if self._running:
                logger.warning(f"TEF reconnexion dans {retry}s…")
                time.sleep(retry)

    def _send_init(self):
        for cmd in self._INIT_CMDS:
            self._write(cmd)
            time.sleep(0.05)
        self._write(f'T{self._freq_khz}\n'.encode())
        time.sleep(0.1)
        logger.info(f"TEF init OK — tune {self._freq_khz} kHz")

    def _read_loop(self):
        buf = b''
        while self._running:
            with self._lock:
                if not self._ser:
                    break
                chunk = self._ser.read(256)
            if not chunk:
                continue
            buf += chunk
            while b'\n' in buf:
                raw, buf = buf.split(b'\n', 1)
                line = raw.decode('ascii', errors='ignore').strip()
                if line:
                    self._parse(line)

    def _write(self, data):
        with self._lock:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.write(data)
                except Exception as e:
                    logger.error(f"TEF write: {e}")

    # ── Parser ──────────────────────────────────────────────────────────

    def _parse(self, line):
        cmd  = line[0]
        data = line[1:]

        if cmd == 'S' and data[:1] in ('s', 'm', 'S', 'M'):
            # Ss / Sm <dBf>,<SNR>,<multipath>,<offset> : s = stéréo (pilote 19 kHz
            # détecté), m = mono ; en majuscule quand le mono est forcé.
            self.stereo = data[:1] == 's'
            self.stereo_seen = True
            parts = data[1:].split(',')
            if len(parts) >= 2 and self.on_signal:
                try:
                    dbf      = float(parts[0])
                    snr      = float(parts[1])
                    mpath    = float(parts[2]) if len(parts) > 2 else 0
                    offset   = float(parts[3]) if len(parts) > 3 else 0
                    self.on_signal(dbf, snr, mpath, offset)
                except (ValueError, IndexError):
                    pass

        elif cmd == 'P':
            # PI code (Block A) — doit être exactement 4 chiffres hex
            pi = data.strip().upper()
            if pi and len(pi) == 4 and all(c in '0123456789ABCDEF' for c in pi) and self.on_pi:
                self.on_pi(pi)

        elif cmd == 'R' and len(data) == 14:
            # Groupe RDS : BlockB + BlockC + BlockD + Status
            try:
                bb = int(data[0:4],  16)
                bc = int(data[4:8],  16)
                bd = int(data[8:12], 16)
                st = int(data[12:14], 16)
                self._rds.feed(bb, bc, bd, st)
            except ValueError:
                pass

        # T / M / OK → ignorés (confirmations)
