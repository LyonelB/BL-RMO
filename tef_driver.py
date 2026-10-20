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
    Callbacks : on_ps(ps_str), on_rt(rt_str), on_ms(bool)

    Fiabilité (comme FM-DX-Webserver) :
    - L'octet d'état des lignes R donne le niveau d'erreur de chaque bloc
      (format XDR historique : B = bits 1-0, C = bits 3-2, D = bits 5-4 ;
      0 = sans erreur, 1 = petite erreur corrigée, 2 = grosse erreur corrigée,
      3 = bloc perdu). Seuls les blocs sans erreur sont utilisés : un caractère
      corrompu n'entre jamais dans le PS ou le RadioText.
    - RadioText : un nouveau texte remplace l'ancien en entier. Le basculement
      A/B, ou un segment sans erreur dont le contenu change, efface le texte en
      cours ; le RadioText n'est rendu qu'une fois tous ses segments reçus de
      nouveau, jamais en mélange de l'ancien et du nouveau titre.
    - Groupes 2A (64 caractères) et 2B (32 caractères).
    """

    MAX_ERR = 0     # niveau d'erreur accepté par bloc (0 : blocs intacts seulement)

    def __init__(self, on_ps=None, on_rt=None, on_ms=None):
        self.on_ps = on_ps
        self.on_rt = on_rt
        self.on_ms = on_ms
        self.stats = {'groups': 0, 'used': 0, 'b_err': 0, 'cd_err': 0}
        self.reset()

    def reset(self):
        self._ps_buf    = [' '] * 8
        self._ps_seen   = [False] * 4
        self._ps_prev   = ''          # PS du cycle précédent
        self._rt_clear(None)

    def _rt_clear(self, version):
        self._rt_buf     = [' '] * 64
        self._rt_seen    = [False] * 16
        self._rt_ab      = None if version is None else getattr(self, '_rt_ab', None)
        self._rt_len     = 64 if version != 1 else 32
        self._rt_version = version     # 0 = 2A, 1 = 2B
        self._rt_emitted = ''

    @staticmethod
    def errors(status):
        """Niveaux d'erreur (B, C, D) de l'octet d'état XDR historique."""
        return status & 0x03, (status >> 2) & 0x03, (status >> 4) & 0x03

    def feed(self, block_b, block_c, block_d, status):
        """Traite un groupe RDS (blocks B/C/D + octet d'état)."""
        self.stats['groups'] += 1
        self._seen_total = getattr(self, '_seen_total', 0) + 1
        err_b, err_c, err_d = self.errors(status)
        # Garde-fou : si presque tout est rejeté dès le départ, l'octet d'état
        # n'a sans doute pas le format attendu ; on revient au décodage sans filtre.
        if self._seen_total == 300 and self.MAX_ERR < 3 and self.stats['b_err'] > 270:
            logger.warning("RDS : 90 % des groupes rejetés au démarrage, filtrage des erreurs désactivé")
            self.MAX_ERR = 3
        if err_b > self.MAX_ERR:          # type de groupe et segment incertains
            self.stats['b_err'] += 1
            return
        group_type = (block_b >> 12) & 0x0F
        version    = (block_b >> 11) & 0x01   # 0 = A, 1 = B
        ok_c, ok_d = err_c <= self.MAX_ERR, err_d <= self.MAX_ERR
        if not (ok_c and ok_d):
            self.stats['cd_err'] += 1

        if group_type == 0:
            ms = (block_b >> 3) & 0x01    # bit MS : 1 = musique, 0 = parole (pas la stéréo)
            if self.on_ms:
                self.on_ms(bool(ms))
            if ok_d:
                self.stats['used'] += 1
                self._group_0(block_b, block_d)
        elif group_type == 2:
            if version == 0 and ok_c and ok_d:
                self.stats['used'] += 1
                self._group_2(block_b, [block_c >> 8, block_c & 0xFF, block_d >> 8, block_d & 0xFF], 0)
            elif version == 1 and ok_d:
                self.stats['used'] += 1
                self._group_2(block_b, [block_d >> 8, block_d & 0xFF], 1)

    # ── Groupe 0A/0B : PS (8 caractères, 4 segments de 2) ──────────────

    def _group_0(self, block_b, block_d):
        seg = block_b & 0x03
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

    # ── Groupe 2A/2B : RadioText (64 ou 32 caractères) ─────────────────

    def _group_2(self, block_b, codes, version):
        ab  = (block_b >> 4) & 0x01
        seg = block_b & 0x0F
        width = len(codes)                     # 4 caractères (2A) ou 2 (2B)

        # Changement de version (2A/2B) ou du drapeau A/B : nouveau texte
        if version != self._rt_version or (self._rt_ab is not None and ab != self._rt_ab):
            self._rt_clear(version)
        self._rt_ab = ab

        pos0 = seg * width
        chars = []
        end = None
        for i, code in enumerate(codes):
            c = chr(code)
            if c == '\r':                     # fin de texte explicite
                end = pos0 + i
                break
            chars.append(c if self._printable(c) else ' ')

        # Segment au-delà de la fin connue du texte : du remplissage (espaces)
        # est ignoré ; des caractères signifient un nouveau texte, plus long.
        if end is None and pos0 >= self._rt_len and self._rt_len < 64:
            if not ''.join(chars).strip():
                return
            self._rt_clear(version)
            self._rt_ab = ab
        # Un segment déjà reçu dont le contenu change : le texte a changé sans
        # basculement A/B (beaucoup de codeurs ne basculent pas). On repart de zéro.
        elif self._rt_seen[seg]:
            old = self._rt_buf[pos0:pos0 + len(chars)]
            if old != chars or (end is not None and end != self._rt_len):
                self._rt_clear(version)
                self._rt_ab = ab

        for i, c in enumerate(chars):
            if pos0 + i < 64:
                self._rt_buf[pos0 + i] = c
        if end is not None:
            self._rt_len = end
        self._rt_seen[seg] = True

        # Texte complet quand tous les segments jusqu'à la fin sont reçus
        segs_needed = max(1, (self._rt_len + width - 1) // width)
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

    RDS_REPORT_EVERY = 900   # s : bilan de qualité RDS dans le journal

    def _rds_report(self):
        """Toutes les 15 min : part des groupes RDS reçus sans erreur (journal bl-rmo)."""
        now = time.time()
        if not hasattr(self, '_rds_report_at'):
            self._rds_report_at = now
            return
        if now - self._rds_report_at < self.RDS_REPORT_EVERY:
            return
        st = self._rds.stats
        n = st['groups']
        if n:
            logger.info(f"RDS : {n} groupes en {int(now - self._rds_report_at)} s, "
                        f"type illisible {100 * st['b_err'] / n:.1f} %, "
                        f"données avec erreur {100 * st['cd_err'] / n:.1f} %")
        self._rds.stats = {k: 0 for k in st}
        self._rds_report_at = now

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
            self._rds_report()

        # T / M / OK → ignorés (confirmations)
