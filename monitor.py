#!/usr/bin/env python3
"""
Module de surveillance du signal FM
Supporte deux sources :
  - RTL-SDR (mode historique, rtl_fm + redsea)
  - TEF668X Headless USB Tuner (mode natif, tef_driver)
"""
import subprocess
import threading
import queue
import json
import logging
import time
import os
import collections
import numpy as np
import requests
from datetime import datetime
from email_alert import EmailAlert
from database import FMDatabase
from mpx_analyzer import MPXAnalyzer
try:
    from tef_driver import TEFDriver
    _TEF_AVAILABLE = True
except ImportError:
    _TEF_AVAILABLE = False

try:
    from tef_audio_analyzer import TEFAudioAnalyzer
    _TEF_AUDIO_AVAILABLE = True
except ImportError:
    _TEF_AUDIO_AVAILABLE = False

try:
    from audio_defects import AudioDefectDetector, AudioDefectStore
    _DEFECTS_AVAILABLE = True
except ImportError:
    _DEFECTS_AVAILABLE = False

logger = logging.getLogger(__name__)

class FMMonitor:
    def __init__(self, config_path='config.json'):
        """Initialise le moniteur FM"""
        with open(config_path, 'r') as f:
            self.config = json.load(f)

        self.rtl_config = self.config['rtl_sdr']
        self.audio_config = self.config['audio']
        self.tef_config = self.config.get('tef', {})
        self.use_tef = self.tef_config.get('enabled', False)
        decoder = self.config.get('decoder', 'rtl_fm')
        self.use_gnuradio = (decoder == 'gnuradio') and not self.use_tef
        self.tef_driver = None

        # En mode TEF : remplacer MPXAnalyzer par TEFAudioAnalyzer
        if self.use_tef and _TEF_AUDIO_AVAILABLE:
            alsa_dev = self.tef_config.get('alsa_device', 'hw:Tuner')
            self.mpx_analyzer = TEFAudioAnalyzer(alsa_device=alsa_dev)
        else:
            self.mpx_analyzer = MPXAnalyzer(sample_rate=171000, process_every=4)

        # Défauts audio de la chaîne (micro-coupures, clics, craquements),
        # sur le PCM brut du tuner TEF. Relevé seul pour l'instant : pas de mail.
        self.defect_detector = None
        self.defect_store = None
        dcfg = self.config.get('audio_defects', {}) or {}
        if self.use_tef and _DEFECTS_AVAILABLE and dcfg.get('enabled', True):
            try:
                self.defect_store = AudioDefectStore(
                    keep_days=dcfg.get('keep_days', 30), clip_days=dcfg.get('clip_days', 7),
                    max_clips_per_hour=dcfg.get('max_clips_per_hour', 30))
                self.defect_detector = AudioDefectDetector(
                    params=dcfg, on_event=self.defect_store.submit,
                    rf_context=self._defect_rf_context,
                    signal_threshold_dbf=self.tef_config.get('signal_threshold_dbf', 20.0))
            except Exception as e:
                logger.error(f"Détection des défauts audio indisponible : {e}")
                self.defect_detector = self.defect_store = None

        # Queue pour le streaming MP3
        self.stream_queue = queue.Queue(maxsize=500)

        # Processus
        self.master_process = None
        self.monitor_thread = None
        self.running = False

        # État de la surveillance
        self.signal_ok = True
        self.silence_start_time = None
        self.alert_sent = False

        # Surveillance de la modulation
        self.level_history = collections.deque(maxlen=30)
        # Historique 60s pour pré-chargement graphique au rechargement de page
        self.signal_history = collections.deque(maxlen=240)  # 120s à 2/sec
        self.modulation_ok = True
        self.modulation_alert_sent = False
        self.no_modulation_start = None
        self.modulation_std_threshold = float(self.audio_config.get('modulation_std_threshold', 1.5))
        self.modulation_alert_delay = int(self.audio_config.get('modulation_alert_delay', 30))
        # En mode TEF : seuil en dBf (ex : 20.0) ; en mode RTL-SDR : seuil en dBFS (ex : -50.0)
        if self.use_tef:
            self.signal_lost_threshold = float(self.tef_config.get('signal_threshold_dbf', 20.0))
            # Seuil modulation en mode TEF : niveau audio sous lequel → absence modulation
            self.tef_modulation_threshold = float(
                self.tef_config.get('modulation_threshold_dbfs', -40.0)
            )
        else:
            self.signal_lost_threshold = float(self.audio_config.get('signal_lost_threshold', -50.0))
            # Sans antenne, le niveau RF reste élevé (bruit) mais le SNR s'effondre
            # (~1 dB au lieu de 25-40 dB). Le SNR est le seul indicateur fiable.
            self.snr_lost_threshold = float(self.audio_config.get('snr_lost_threshold', 8.0))
            self.tef_modulation_threshold = -40.0

        # Surveillance RDS
        self.rds_ok = False
        self.rds_ever_received = False
        self.rds_last_seen = None
        self.rds_alert_sent = False
        self.rt_alert_sent = False
        self.rt_last_seen = None
        self.rds_timeout = int(self.audio_config.get('rds_timeout', 120))
        self.rt_last_seen = None
        self.rt_alert_sent = False
        self.rt_timeout = int(self.audio_config.get('rt_timeout', 300))  # 5 min par défaut
        # Détection présence signal en mode TEF : débit de trames Ss.
        # Le firmware n'émet des trames Ss que s'il capte un signal.
        self.tef_ss_times = []  # horodatages des trames Ss (fenêtre glissante)
        self.signal_frame_window = int(self.tef_config.get('signal_frame_window', 30))
        self.signal_frame_min = int(self.tef_config.get('signal_frame_min', 5))

        # Système d'alertes
        self.email_alert = EmailAlert(config_path)

        # Statistiques
        self.stats = {
            'start_time': None,
            'uptime': 0,
            'alerts_sent': 0,
            'last_alert': None,
            'current_level': -100.0,
            'modulation_active': False,
            'ps': '-',
            'rt': '-',
            'pi': '-',
            'station_logo': None,
            'status': 'Arrêté'
        }

        # Buffer pour accumuler le RadioText
        self.rt_buffer = ''
        self.rt_ab_flag = None
        self._rt_stable_count = 0
        self._rt_last_candidate = ''
        # Historique des RadioText (RT_HISTORY_PATCH)
        self._rth_candidate = ''      # texte en cours de validation
        self._rth_since = 0.0         # depuis quand il est reçu sans changement
        self._rth_recent = {}         # texte -> heure du dernier enregistrement
        self._rth_last = None         # dernier texte enregistré
        self._logo_searched = False
        self._logo_last_attempt = 0
        self._logo_fail_count = 0
        self._rds_db_reload = False
        self._rds_lookup = None

        # Lock pour thread-safety
        self.stats_lock = threading.Lock()

        # Base de données
        self.db = FMDatabase()
        self.last_db_save = time.time()

        # =============================================
        # FLAGS DE SERVICES (activables/désactivables)
        # =============================================
        self.vu_meter_enabled = True      # Calcul RMS et affichage VU-mètre
        self.audio_enabled = True          # Streaming audio (player)
        self.watchdog_enabled = False       # Watchdog auto-relance rtl_fm
        self.rds_enabled = True           # Lecteur RDS automatique (désactivé par défaut)
        self.history_enabled = False        # Enregistrement historique audio 24h
        self.mpx_enabled = True            # Analyse MPX (déviation, pilote, stéréo, RDS RF)

        # Alerte sur-déviation
        self.deviation_alert_threshold = float(
            self.audio_config.get('deviation_alert_threshold', 80.0)
        )  # kHz — alerte si déviation peak > seuil
        self.deviation_alert_sent = False
        self.deviation_over_start = None
        self.deviation_alert_delay = int(
            self.audio_config.get('deviation_alert_delay', 10)
        )  # secondes de sur-déviation avant alerte

        # Queue pour sauvegarde BDD non-bloquante
        self.db_queue = queue.Queue(maxsize=100)

    def get_services_status(self):
        """Retourne l'état de tous les services"""
        return {
            'vu_meter': self.vu_meter_enabled,
            'audio': self.audio_enabled,
            'watchdog': self.watchdog_enabled,
            'rds': self.rds_enabled,
            'history': self.history_enabled,
            'mpx': self.mpx_enabled,
            'monitoring': self.running
        }

    def toggle_service(self, service, enabled):
        """Active ou désactive un service"""
        if service == 'vu_meter':
            self.vu_meter_enabled = enabled
            logger.info(f"VU-mètre {'activé' if enabled else 'désactivé'}")

        elif service == 'audio':
            self.audio_enabled = enabled
            if not enabled:
                # Vider la queue audio
                while not self.stream_queue.empty():
                    try:
                        self.stream_queue.get_nowait()
                    except queue.Empty:
                        break
            logger.info(f"Player audio {'activé' if enabled else 'désactivé'}")

        elif service == 'watchdog':
            self.watchdog_enabled = enabled
            logger.info(f"Watchdog {'activé' if enabled else 'désactivé'}")

        elif service == 'rds':
            self.rds_enabled = enabled
            if self.use_tef:
                # En mode TEF le RDS arrive toujours via le driver série
                logger.info(f"Mode TEF : RDS toujours actif (flag mis à {enabled})")
            elif enabled:
                # Lancer le thread RDS si pas déjà actif
                if not hasattr(self, 'rds_thread') or not self.rds_thread.is_alive():
                    self.rds_thread = threading.Thread(target=self._rds_reader, daemon=True)
                    self.rds_thread.start()
                    logger.info("Lecteur RDS démarré")
            else:
                logger.info("Lecteur RDS désactivé")

        elif service == 'history':
            self.history_enabled = enabled
            logger.info(f"Historique audio {'activé' if enabled else 'désactivé'}")

        elif service == 'mpx':
            self.mpx_enabled = enabled
            if not enabled:
                self.mpx_analyzer.reset()
            logger.info(f"Analyse MPX {'activée' if enabled else 'désactivée'}")

        else:
            logger.warning(f"Service inconnu: {service}")
            return False

        return True

    def start(self):
        """Démarre la capture et la surveillance FM"""
        if self.running:
            logger.warning("Le moniteur est déjà en cours d'exécution")
            return

        if self.defect_store:
            self.defect_store.start()

        # Clôturer les alertes ouvertes d'une session précédente
        self.db.close_open_alerts()
        logger.info(f"Démarrage de la surveillance FM sur {self.rtl_config['frequency']}")
        self.running = True
        self.stats['start_time'] = datetime.now()
        self.stats['status'] = 'En cours'

        try:
            if self.use_tef:
                # ── Mode TEF668X ──────────────────────────────────────────────
                self._start_tef_mode()
            elif self.use_gnuradio:
                # ── Mode GNU Radio (stéréo WFM) ──────────────────────────────
                self._start_gnuradio_mode()
            else:
                # ── Mode RTL-SDR (original) ───────────────────────────────────
                fifo_path = '/tmp/fm_stream.mp3'
                if os.path.exists(fifo_path):
                    os.remove(fifo_path)
                os.mkfifo(fifo_path)
                logger.info(f"FIFO créé : {fifo_path}")

                os.system("pkill -9 rtl_fm 2>/dev/null")
                os.system("pkill -9 sox 2>/dev/null")
                time.sleep(0.5)

                self.master_thread = threading.Thread(target=self._master_monitor, daemon=True)
                self.master_thread.start()

                self.stream_thread = threading.Thread(target=self._stream_monitor, daemon=True)
                self.stream_thread.start()

                if self.rds_enabled:
                    self.rds_thread = threading.Thread(target=self._rds_reader, daemon=True)
                    self.rds_thread.start()

            # ── Threads communs aux deux modes ────────────────────────────
            # Thread de surveillance du signal (alertes)
            self.monitor_thread = threading.Thread(target=self._monitor_signal, daemon=True)
            self.monitor_thread.start()

            # Démarrer le watchdog
            self.watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
            self.watchdog_thread.start()

            # Thread dédié pour sauvegarde BDD (non-bloquant)
            self.db_writer_thread = threading.Thread(target=self._db_writer, daemon=True)
            self.db_writer_thread.start()

            # RDS désactivé par défaut (activable depuis le dashboard)
            if not self.use_tef and self.rds_enabled:
                self.rds_thread = threading.Thread(target=self._rds_reader, daemon=True)
                self.rds_thread.start()

            self.rds_db_watcher_thread = threading.Thread(
                target=self._rds_db_watcher, daemon=True
            )
            self.rds_db_watcher_thread.start()
            logger.info("Watcher rds-station-db démarré (cycle 24h)")
            logger.info("Surveillance FM démarrée avec succès")
        except Exception as e:
            logger.error(f"Erreur lors du démarrage: {e}")
            self.stop()
            raise
        self._start_webhook()

    def _watchdog(self):
        """Thread de surveillance qui relance le processus maître si crash"""
        logger.info("Watchdog démarré")

        while self.running:
            try:
                time.sleep(10)

                if not self.running:
                    break

                # Ne rien faire si watchdog désactivé
                if not self.watchdog_enabled:
                    continue

                if self.use_tef:
                    # Mode TEF : surveiller le driver série et le processus audio
                    if self.tef_driver and not self.tef_driver.is_alive():
                        logger.error("TEFDriver mort — relance automatique")
                        freq_khz = self._parse_freq_khz(self.rtl_config['frequency'])
                        self.tef_driver.start(freq_khz)

                    if self.master_process and self.master_process.poll() is not None:
                        logger.error("Audio TEF planté — relance automatique")
                        alsa_dev = self.tef_config.get('alsa_device', 'hw:Tuner')
                        self.master_thread = threading.Thread(
                            target=self._tef_audio, args=(alsa_dev,), daemon=True
                        )
                        self.master_thread.start()
                else:
                    # Mode RTL-SDR : surveiller master_process
                    if self.master_process and self.master_process.poll() is not None:
                        logger.error("rtl_fm a planté ! Relance automatique...")
                        os.system("pkill -9 rtl_fm 2>/dev/null")
                        os.system("pkill -9 sox 2>/dev/null")
                        os.system("pkill -9 redsea 2>/dev/null")
                        time.sleep(2)
                        self.master_thread = threading.Thread(target=self._master_monitor, daemon=True)
                        self.master_thread.start()
                        logger.info("rtl_fm relancé automatiquement")

            except Exception as e:
                logger.error(f"Erreur watchdog: {e}")

    def _db_writer(self):
        """Thread dédié pour écriture BDD non-bloquante"""
        logger.info("Thread BDD démarré")
        while self.running:
            try:
                item = self.db_queue.get(timeout=1)
                self.db.save_audio_level(item['level'], item['signal_ok'])
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Erreur écriture BDD: {e}")

    def _master_monitor(self):
        """
        Processus maître : UN SEUL rtl_fm à 171k avec tee pour tout
        - Branche 1 : redsea pour RDS
        - Branche 2 : sox pour MP3
        - Stdout : Python pour calcul RMS
        """
        sample_rate = '171000'

        cmd = (
            f"stdbuf -o0 rtl_fm "
            f"-f {self.rtl_config['frequency']} "
            f"-M wbfm -s 171k -r 171k "
            f"-g {self.rtl_config['gain']} "
            f"-p {self.rtl_config['ppm_error']} "
            f"-A fast | "
            f"tee >(stdbuf -oL redsea -p > /tmp/rds_output.json) | "
            f"tee >(ffmpeg -f s16le -ar {sample_rate} -ac 1 -i - "
            f"-codec:a libmp3lame -b:a 128k -ar {self.audio_config['output_rate']} -ac 2 "
            f"-content_type audio/mpeg -f mp3 "
            f"icecast://source:fmmonitor2026@localhost:8000/fmmonitor 2>/dev/null) | cat"
        )

        logger.info("Lancement du processus maître rtl_fm 171k avec tee (RDS+Audio+RMS)")

        if os.path.exists('/tmp/rds_output.json'):
            os.remove('/tmp/rds_output.json')

        try:
            self.master_process = subprocess.Popen(
                cmd,
                shell=True,
                executable='/bin/bash',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )

            chunk_size = 4096

            while self.running and self.master_process.poll() is None:
                chunk = self.master_process.stdout.read(chunk_size)
                if not chunk:
                    break

                # VU-mètre désactivé : on lit quand même le stdout pour ne pas bloquer rtl_fm
                # mais on ne calcule pas le RMS
                if not self.vu_meter_enabled:
                    continue

                try:
                    samples = np.frombuffer(chunk, dtype=np.int16)

                    if len(samples) < 10:
                        continue

                    rms = np.sqrt(np.mean(np.square(samples.astype(np.float32))))

                    if rms > 0:
                        db = 20 * np.log10(rms / 32768.0)
                    else:
                        db = -100.0

                    modulation_active = bool(db > -60.0)
                    with self.stats_lock:
                        self.stats['current_level'] = float(db)
                        self.stats['modulation_active'] = modulation_active

                    # ── Analyse MPX (déviation, pilote, stéréo, RDS RF) ──
                    if self.mpx_enabled:
                        self.mpx_analyzer.process_chunk(samples)

                    # Envoyer en BDD via queue non-bloquante toutes les 5 secondes
                    if self.history_enabled and time.time() - self.last_db_save >= 5:
                        try:
                            self.db_queue.put_nowait({'level': float(db), 'signal_ok': self.signal_ok})
                            self.last_db_save = time.time()
                        except queue.Full:
                            pass

                except Exception as e:
                    logger.error(f"Erreur calcul RMS: {e}")

        except Exception as e:
            logger.error(f"Erreur processus maître: {e}")
        finally:
            if self.master_process:
                self.master_process.kill()

    # ══════════════════════════════════════════════════════════════════
    # MÉTHODES TEF668X
    # ══════════════════════════════════════════════════════════════════

    def _start_tef_mode(self):
        """Initialise le driver TEF série et le pipeline audio ALSA→Icecast."""
        if not _TEF_AVAILABLE:
            raise RuntimeError("tef_driver.py introuvable — impossible de démarrer en mode TEF")

        freq_khz = self._parse_freq_khz(self.rtl_config['frequency'])
        port     = self.tef_config.get('serial_port', '/dev/ttyACM0')
        alsa_dev = self.tef_config.get('alsa_device', 'hw:Tuner')

        self.tef_driver = TEFDriver(
            port=port,
            on_signal=self._on_tef_signal,
            on_pi=self._on_tef_pi,
            on_ps=self._on_tef_ps,
            on_rt=self._on_tef_rt,
            on_ms=self._on_tef_ms,
        )
        self.tef_driver.start(freq_khz)

        # TEFAudioAnalyzer : pas de start() autonome,
        # il est alimenté directement par _tef_audio via _process()
        # (évite le conflit ALSA avec arecord)

        self.master_thread = threading.Thread(
            target=self._tef_audio, args=(alsa_dev,), daemon=True, name='tef-audio'
        )
        self.master_thread.start()
        logger.info(f"Mode TEF démarré — série {port}, audio {alsa_dev}, {freq_khz} kHz")

    def _parse_freq_khz(self, freq_str):
        """
        Convertit une fréquence config en kHz entier.
        '88.6M' → 88600 | '88600000' → 88600 | '88.600' → 88600
        """
        s = str(freq_str).strip().upper().replace(' ', '')
        if s.endswith('M'):
            return int(float(s[:-1]) * 1000)
        val = float(s)
        if val > 1_000_000:    # Hz
            return int(val / 1000)
        if val > 10_000:       # kHz
            return int(val)
        return int(val * 1000) # MHz

    def _tef_audio(self, alsa_device='hw:Tuner'):
        """
        Thread audio TEF : ffmpeg asplit pour deux sorties simultanées :
          - Output 0 → Icecast (MP3)
          - Output 1 → stdout PCM brut → TEFAudioAnalyzer._process()
        Un seul process, pas de bash tee, latence minimale.
        """
        output_rate = self.audio_config.get('output_rate', '44100')
        icecast_url = 'icecast://source:fmmonitor2026@localhost:8000/fmmonitor'

        cmd = (
            f'ffmpeg -hide_banner -loglevel error '
            f'-fflags nobuffer -flags low_delay '
            f'-f alsa -ar 48000 -ac 2 -i {alsa_device} '
            f'-filter_complex "[0:a]asplit=2[icecast][pcm]" '
            f'-map "[icecast]" -codec:a libmp3lame -b:a 128k -ar {output_rate} -ac 2 '
            f'-content_type audio/mpeg -f mp3 {icecast_url} '
            f'-map "[pcm]" -fflags nobuffer -f s16le -ar 48000 -ac 2 pipe:1'
        )

        logger.info(f"Audio TEF : {alsa_device} → Icecast + analyse via ffmpeg asplit")
        try:
            self.master_process = subprocess.Popen(
                cmd, shell=True, executable='/bin/bash',
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0,
            )

            # Lire le PCM depuis stdout et alimenter _process() toutes les 50ms
            period_bytes  = 480 * 4   # 10ms de PCM S16_LE stéréo
            process_every = 5         # 5 × 10ms = 50ms par analyse
            count = 0
            accum = b''
            buf   = b''

            while self.running and self.master_process.poll() is None:
                chunk = self.master_process.stdout.read(period_bytes - len(buf))
                if not chunk:
                    break
                buf += chunk
                if len(buf) >= period_bytes:
                    accum += buf[:period_bytes]
                    buf    = buf[period_bytes:]
                    count += 1
                    if count >= process_every:
                        if hasattr(self.mpx_analyzer, '_process'):
                            self.mpx_analyzer._process(accum)
                        if self.defect_detector:
                            self.defect_detector.feed(accum)
                        accum = b''
                        count = 0

        except Exception as e:
            logger.error(f"Erreur audio TEF: {e}")
        finally:
            if self.master_process:
                try:
                    self.master_process.kill()
                except Exception:
                    pass

    def _defect_rf_context(self):
        """Réception au moment d'un défaut audio (dernières valeurs du tuner)."""
        with self.stats_lock:
            return {'dbf': self.stats.get('signal_dbf'), 'multipath': self.stats.get('multipath'),
                    'snr': self.stats.get('snr')}

    # ── Callbacks TEF → stats ────────────────────────────────────────

    def _on_tef_signal(self, dbf, snr, multipath, offset):
        """Reçoit les métriques signal du TEF (~1/s)."""
        threshold  = self.tef_config.get('signal_threshold_dbf', 20.0)
        signal_ok  = dbf >= threshold
        # Présence signal : on horodate chaque trame Ss (fenêtre glissante).
        # C'est le vrai détecteur de présence (le dBf seul n'est pas fiable).
        now = time.time()
        self.tef_ss_times.append(now)
        cutoff = now - self.signal_frame_window
        self.tef_ss_times = [t for t in self.tef_ss_times if t >= cutoff]
        # Normalisation pour le VU-mètre du dashboard (qui attend -100..0 dBFS)
        # On mappe dBf (0..60) vers (-60..0) : valeur_display = dBf - 60
        with self.stats_lock:
            self.stats['current_level']     = dbf - 60.0
            self.stats['signal_dbf']        = dbf
            self.stats['snr']               = snr
            self.stats['multipath']         = multipath
            self.stats['freq_offset']       = offset
            self.stats['modulation_active'] = signal_ok
        # NOTE : en mode TEF, self.signal_ok n'est PLUS écrit ici. Il est
        # piloté uniquement par le thread de surveillance (comptage de trames
        # Ss), sinon le retour des trames court-circuitait la détection de
        # rétablissement (recovery jamais envoyé). La variable locale
        # signal_ok reste utilisée pour les stats/historique ci-dessous.
        if self.history_enabled and time.time() - self.last_db_save >= 5:
            try:
                self.db_queue.put_nowait({'level': dbf, 'signal_ok': signal_ok})
                self.last_db_save = time.time()
            except queue.Full:
                pass

    def _on_tef_pi(self, pi):
        """Reçoit le code PI RDS du TEF."""
        with self.stats_lock:
            old_pi = self.stats.get('pi', '-')
            if pi != old_pi and pi not in ('', '-'):
                logger.info(f'PI changé {old_pi} → {pi}')
                self._logo_searched     = False
                self._logo_last_attempt = 0
                self.stats['station_logo'] = None
                self._rds_db_reload     = True
                self.stats['pi']        = pi
                import threading as _t
                _t.Thread(target=self._fetch_station_logo, daemon=True).start()
            else:
                self.stats['pi'] = pi
            self.rds_last_seen     = time.time()
            self.rds_ever_received = True
            self.rds_ok            = True

    def _on_tef_ps(self, ps):
        """Reçoit le PS (nom station RDS) du TEF."""
        with self.stats_lock:
            self.stats['ps']       = ps
            self.rds_last_seen     = time.time()
            self.rds_ever_received = True
            self.rds_ok            = True
            if not self._logo_searched:
                import threading as _t
                _t.Thread(target=self._fetch_station_logo, daemon=True).start()

    def _on_tef_rt(self, rt):
        """Reçoit le RadioText RDS du TEF."""
        with self.stats_lock:
            if rt:
                self.stats['rt']  = rt
                self.rt_buffer    = rt
                self.rt_last_seen = time.time()   # alimente l'alerte « RadioText absent »
            ps = self.stats.get('ps', '-')
        if rt:
            self._rt_history_note(rt, ps)

    # Un RadioText n'entre dans l'historique qu'après RT_HISTORY_STABLE s de réception
    # identique (écarte les textes partiels ou corrompus), et pas s'il a déjà été
    # enregistré dans les RT_HISTORY_REPEAT dernières secondes (messages en rotation).
    RT_HISTORY_STABLE = 8
    RT_HISTORY_REPEAT = 600

    def _rt_history_note(self, rt, ps=None):
        """Valide un RadioText reçu et l'enregistre s'il est nouveau (RT_HISTORY_PATCH)."""
        text = ' '.join(rt.split())
        if not text:
            return
        now = time.time()
        if text != self._rth_candidate:
            self._rth_candidate, self._rth_since = text, now
            return
        if now - self._rth_since < self.RT_HISTORY_STABLE or text == self._rth_last:
            return
        if self._rth_last is None:                # premier texte depuis le démarrage
            last = self.db.get_rt_history(limit=1)
            if last and last[0]['rt'] == text:
                self._rth_last = text
                return
        self._rth_last = text
        if now - self._rth_recent.get(text, 0) < self.RT_HISTORY_REPEAT:
            return
        self._rth_recent = {t: ts for t, ts in self._rth_recent.items()
                            if now - ts < self.RT_HISTORY_REPEAT}
        self._rth_recent[text] = now
        self.db.save_rt_title(None if ps in (None, '-') else ps, text)

    def _on_tef_ms(self, is_stereo):
        """Reçoit le flag MS (Music/Stereo) depuis le groupe RDS 0A."""
        with self.stats_lock:
            self.stats['stereo_present'] = is_stereo

    # ══════════════════════════════════════════════════════════════════


    def _start_gnuradio_mode(self):
        """Initialise le mode GNU Radio : stéréo WFM + RDS via FIFO."""
        import os
        RDS_FIFO = '/tmp/rds_gnuradio.pcm'
        MPX_FIFO = '/tmp/mpx_gnuradio.pcm'
        for fifo in [RDS_FIFO, MPX_FIFO]:
            if os.path.exists(fifo):
                os.remove(fifo)
            os.mkfifo(fifo)
        logger.info("Mode GNU Radio : FIFO RDS créé")

        self.master_thread = threading.Thread(
            target=self._master_monitor_gnuradio, daemon=True, name='gnuradio-audio'
        )
        self.master_thread.start()

        self.redsea_gnuradio_thread = threading.Thread(
            target=self._redsea_gnuradio, daemon=True, name='gnuradio-rds'
        )
        self.redsea_gnuradio_thread.start()

        if self.mpx_enabled:
            self.mpx_gnuradio_thread = threading.Thread(
                target=self._mpx_gnuradio_reader, daemon=True, name='gnuradio-mpx'
            )
            self.mpx_gnuradio_thread.start()

        if self.rds_enabled:
            self.rds_thread = threading.Thread(target=self._rds_reader, daemon=True)
            self.rds_thread.start()

        logger.info("Mode GNU Radio démarré")

    def _master_monitor_gnuradio(self):
        """
        Lance wfm_stereo.py | ffmpeg → Icecast
        Calcule aussi le RMS depuis stdout pour le VU-mètre.
        """
        import os
        import numpy as np

        freq_mhz = self.rtl_config['frequency'].replace('M', '').replace('m', '')
        gain     = self.rtl_config['gain']
        ppm      = self.rtl_config['ppm_error']
        output_rate = self.audio_config.get('output_rate', '44100')
        script   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wfm_stereo.py')

        cmd = (
            f"python3 {script} {freq_mhz} {gain} {ppm} | "
            f"tee >(ffmpeg -f s16le -ar 48000 -ac 2 -i - "
            f"-codec:a libmp3lame -b:a 128k -ar {output_rate} -ac 2 "
            f"-content_type audio/mpeg -f mp3 "
            f"icecast://source:fmmonitor2026@localhost:8000/fmmonitor 2>/dev/null) | cat"
        )

        logger.info("GNU Radio : lancement wfm_stereo.py | ffmpeg → Icecast")

        try:
            self.master_process = subprocess.Popen(
                cmd,
                shell=True,
                executable='/bin/bash',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )

            chunk_size = 4096  # 4096 shorts = 2 canaux × 2048 frames

            while self.running and self.master_process.poll() is None:
                chunk = self.master_process.stdout.read(chunk_size)
                if not chunk:
                    break

                if not self.vu_meter_enabled:
                    continue

                try:
                    samples = np.frombuffer(chunk, dtype=np.int16)
                    if len(samples) < 10:
                        continue

                    # Stéréo interleaved → L pair, R impair
                    stereo = samples.reshape(-1, 2).astype(np.float32)
                    rms_l  = np.sqrt(np.mean(np.square(stereo[:, 0])))
                    rms_r  = np.sqrt(np.mean(np.square(stereo[:, 1])))
                    db_l   = 20 * np.log10(rms_l / 32768.0) if rms_l > 0 else -100.0
                    db_r   = 20 * np.log10(rms_r / 32768.0) if rms_r > 0 else -100.0
                    db     = (db_l + db_r) / 2.0

                    with self.stats_lock:
                        self.stats['current_level']     = float(db)
                        self.stats['level_left']        = float(db_l)
                        self.stats['level_right']       = float(db_r)
                        self.stats['modulation_active'] = bool(db > -60.0)

                    # MPXAnalyzer alimenté par thread dédié depuis FIFO (voir _mpx_gnuradio_reader)

                    if self.history_enabled and time.time() - self.last_db_save >= 5:
                        try:
                            self.db_queue.put_nowait({'level': float(db), 'signal_ok': self.signal_ok})
                            self.last_db_save = time.time()
                        except Exception:
                            pass

                except Exception as e:
                    logger.error(f"GNU Radio RMS: {e}")

        except Exception as e:
            logger.error(f"Erreur GNU Radio audio: {e}")
        finally:
            if self.master_process:
                self.master_process.kill()

    def _mpx_gnuradio_reader(self):
        """Lit le signal MPX brut depuis le FIFO GNU Radio et alimente MPXAnalyzer."""
        import os
        MPX_FIFO = '/tmp/mpx_gnuradio.pcm'
        while not os.path.exists(MPX_FIFO):
            time.sleep(0.2)
        time.sleep(1)
        logger.info("GNU Radio MPX : démarrage analyse spectre")
        try:
            with open(MPX_FIFO, 'rb') as f:
                while self.running:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    samples = np.frombuffer(chunk, dtype=np.int16)
                    if self.mpx_enabled and len(samples) >= 512:
                        self.mpx_analyzer.process_chunk(samples)
        except Exception as e:
            logger.error(f"Erreur MPX GNU Radio reader: {e}")

    def _redsea_gnuradio(self):
        """
        Lance redsea en lisant depuis le FIFO MPX GNU Radio.
        Écrit dans /tmp/rds_output.json — compatible avec _rds_reader().
        """
        RDS_FIFO = '/tmp/rds_gnuradio.pcm'
        rds_json = '/tmp/rds_output.json'
        import os

        # Attendre que le FIFO existe
        while not os.path.exists(RDS_FIFO):
            time.sleep(0.2)

        logger.info("GNU Radio RDS : ouverture FIFO MPX (attente GNU Radio)...")
        try:
            # open() bloque naturellement jusqu'à ce que GNU Radio ouvre le write end
            fifo_in = open(RDS_FIFO, "rb")
            rds_out  = open(rds_json, "w", encoding="utf-8", buffering=1)
            cmd = ["stdbuf", "-oL", "redsea", "-p", "-r", "171428"]
            logger.info("GNU Radio RDS : lancement redsea -r 171428")
            self.redsea_process = subprocess.Popen(
                cmd,
                stdin=fifo_in,
                stdout=rds_out,
                stderr=subprocess.DEVNULL
            )
            self.redsea_process.wait()
        except Exception as e:
            logger.error(f"Erreur redsea GNU Radio: {e}")

    def _stream_monitor(self):
        """Lit /tmp/fm_stream.mp3 et le met dans la queue"""
        logger.info("Démarrage du monitoring du stream MP3")

        while not os.path.exists('/tmp/fm_stream.mp3'):
            time.sleep(0.1)

        try:
            with open('/tmp/fm_stream.mp3', 'rb') as f:
                while self.running:
                    chunk = f.read(8192)
                    if chunk:
                        # Si audio désactivé, on lit quand même pour ne pas bloquer sox
                        # mais on ne met pas dans la queue
                        if not self.audio_enabled:
                            time.sleep(0.05)
                            continue
                        try:
                            self.stream_queue.put(chunk, timeout=0.1)
                        except queue.Full:
                            try:
                                self.stream_queue.get_nowait()
                            except queue.Empty:
                                pass
                    else:
                        time.sleep(0.05)
        except Exception as e:
            logger.error(f"Erreur stream MP3: {e}")

    def _rds_reader(self):
        """Lit les données RDS depuis /tmp/rds_output.json"""
        logger.info("Démarrage lecteur RDS automatique")

        while not os.path.exists('/tmp/rds_output.json'):
            time.sleep(0.5)

        try:
            proc = subprocess.Popen(
                ['tail', '-f', '/tmp/rds_output.json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                bufsize=1
            )

            for line in proc.stdout:
                if not self.running or not self.rds_enabled:
                    break

                try:
                    data = json.loads(line.strip())

                    with self.stats_lock:
                        if 'ps' in data:
                            self.stats['ps'] = data['ps'].strip()
                            self.rds_last_seen = time.time()
                            self.rds_ever_received = True
                            self.rds_ok = True
                            if not self._logo_searched:
                                import threading as _t
                                _t.Thread(target=self._fetch_station_logo, daemon=True).start()

                        if 'pi' in data:
                            new_pi = data['pi'].strip().upper().lstrip('0X').lstrip('0x')
                            if new_pi.startswith('X'):
                                new_pi = new_pi[1:]
                            old_pi = self.stats.get('pi', '-')
                            if new_pi != old_pi and new_pi not in ('', '-'):
                                logger.info(f'PI changé {old_pi} -> {new_pi}, réinit logo')
                                self._logo_searched = False
                                self._logo_last_attempt = 0
                                self.stats['station_logo'] = None
                                self._rds_db_reload = True
                                self.stats['pi'] = new_pi  # affecter AVANT de lancer le thread
                                import threading as _t
                                _t.Thread(target=self._fetch_station_logo, daemon=True).start()
                            else:
                                self.stats['pi'] = new_pi
                            self.rds_last_seen = time.time()
                            self.rds_ever_received = True
                            self.rds_ok = True
                            if not self._logo_searched:
                                import threading as _t
                                _t.Thread(target=self._fetch_station_logo, daemon=True).start()

                        if 'radiotext' in data:
                            rt_full = data['radiotext'].strip()
                            if rt_full:
                                self.stats['rt'] = rt_full
                                self.rt_buffer = rt_full
                                self.rt_last_seen = time.time()
                        elif 'partial_radiotext' in data:
                            rt_segment = data['partial_radiotext'].strip()
                            rt_ab = data.get('rt_ab', 'A')
                            if self.rt_ab_flag is not None and self.rt_ab_flag != rt_ab:
                                self.rt_buffer = ''
                                self._rt_stable_count = 0
                                self._rt_last_candidate = ''
                            self.rt_ab_flag = rt_ab
                            if rt_segment and len(rt_segment) > len(self.rt_buffer):
                                self.rt_buffer = rt_segment
                            # Stabilisation : même texte 3 fois de suite → RT validé
                            if self.rt_buffer and self.rt_buffer == self._rt_last_candidate:
                                self._rt_stable_count += 1
                                if self._rt_stable_count >= 3:
                                    self.stats['rt'] = self.rt_buffer
                                    self.rt_last_seen = time.time()
                                    self._rt_stable_count = 0
                            else:
                                self._rt_last_candidate = self.rt_buffer
                                self._rt_stable_count = 1

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.error(f"Erreur lecture RDS: {e}")

        except Exception as e:
            logger.error(f"Erreur processus RDS: {e}")

    def read_rds_once(self, duration=10):
        """Lit les données RDS depuis le flux existant (/tmp/rds_output.json)"""
        try:
            logger.info(f"Lecture RDS ponctuelle ({duration}s) depuis flux existant...")

            if not os.path.exists('/tmp/rds_output.json'):
                logger.warning("Fichier RDS non disponible")
                return False

            ps_found = False
            rt_found = False
            start_time = time.time()

            proc = subprocess.Popen(
                ['tail', '-f', '/tmp/rds_output.json'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                bufsize=1
            )

            import select

            while time.time() - start_time < duration:
                ready = select.select([proc.stdout], [], [], 0.5)
                if ready[0]:
                    line = proc.stdout.readline()
                    if line:
                        try:
                            data = json.loads(line.strip())

                            if 'ps' in data and not ps_found:
                                self.stats['ps'] = data['ps'].strip()
                                ps_found = True
                                logger.info(f"PS trouvé: {self.stats['ps']}")

                            if 'partial_radiotext' in data:
                                # Ignoré : on n'affiche que le RT complet
                                pass

                            if ps_found and rt_found:
                                logger.info("PS et RT trouvés, arrêt anticipé")
                                break

                        except json.JSONDecodeError:
                            pass

            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

            logger.info(f"Lecture RDS terminée - PS: {ps_found}, RT: {rt_found}")
            return True

        except Exception as e:
            logger.error(f"Erreur lecture RDS: {e}")
            return False

    def _monitor_signal(self):
        """Surveille le niveau du signal et la modulation audio"""
        logger.info("Thread de surveillance démarré")

        while self.running:
            try:
                with self.stats_lock:
                    current_level = self.stats['current_level']

                # Alimenter le buffer d'historique modulation
                self.level_history.append(current_level)

                # ── 1. PERTE TOTALE DE L'ÉMETTEUR (porteuse absente) ──────────
                # En mode TEF : current_level est en dBf, signal_ok déjà géré
                # par _on_tef_signal(). On surveille quand même pour les alertes.
                if not self.use_tef:
                    # Seuil très bas : -50 dB par défaut → vrai crash émetteur
                    level_lost = current_level < self.signal_lost_threshold
                    # SNR : sans antenne le niveau reste haut (bruit) mais le SNR chute
                    snr_now = 99.0
                    if self.mpx_enabled:
                        snr_now = self.mpx_analyzer.get_results().get('snr', 99.0)
                    snr_lost = snr_now < self.snr_lost_threshold
                    signal_lost = level_lost or snr_lost
                else:
                    # Mode TEF : présence signal = débit de trames Ss.
                    # Le firmware n'émet des trames que s'il capte un signal ;
                    # moins de signal_frame_min trames sur la fenêtre = perte.
                    now = time.time()
                    cutoff = now - self.signal_frame_window
                    recent_ss = [t for t in self.tef_ss_times if t >= cutoff]
                    signal_lost = len(recent_ss) < self.signal_frame_min

                if signal_lost:
                    if self.signal_ok:
                        self.signal_ok = False
                        self.silence_start_time = time.time()
                        logger.warning(f"Perte émetteur détectée: {current_level:.2f} dB")
                    else:
                        silence_duration = time.time() - self.silence_start_time
                        if silence_duration >= self.audio_config['silence_duration'] and not self.alert_sent:
                            # Libellés selon le mode : le TEF mesure la réception
                            # (débit de trames), pas l'émission → libellé distinct.
                            if self.use_tef:
                                _sig_type = "Signal FM non capté"
                                _sig_detail = (
                                    f"Aucune trame signal reçue du tuner depuis {int(silence_duration)}s.\n"
                                    f"Le tuner ne capte plus de signal FM sur {self.stats.get('signal_dbf', 0):.1f} dBf.\n"
                                    f"Causes possibles : réception/antenne du tuner, ou interruption de la diffusion."
                                )
                                _sig_msg = f"Signal FM non capté - {current_level:.2f} dB"
                            else:
                                _sig_type = "Émetteur FM hors ligne"
                                _sig_detail = f"Aucune porteuse FM détectée.\nNiveau: {current_level:.2f} dB\nDurée: {int(silence_duration)}s"
                                _sig_msg = f"Émetteur hors ligne - {current_level:.2f} dB"
                            logger.error(f"{_sig_type} depuis {silence_duration:.0f}s - ENVOI ALERTE")
                            success = self.email_alert.send_alert(
                                alert_type=_sig_type,
                                details=_sig_detail,
                                skip_cooldown=True
                            )
                            if success:
                                self.alert_sent = True
                                with self.stats_lock:
                                    self.stats['alerts_sent'] += 1
                                    self.stats['last_alert'] = datetime.now().isoformat()
                                self.db.save_alert(
                                    alert_type='signal_lost',
                                    level_db=current_level,
                                    duration_seconds=int(silence_duration),
                                    message=_sig_msg,
                                    email_sent=True
                                )
                else:
                    if not self.signal_ok:
                        logger.info(f"Émetteur rétabli: {current_level:.2f} dB")
                        if self.alert_sent:
                            self.email_alert.send_recovery_alert()
                            self.db.save_alert(
                                alert_type='signal_restored',
                                level_db=current_level,
                                duration_seconds=int(time.time() - self.silence_start_time),
                                message=f"Émetteur rétabli - {current_level:.2f} dB",
                                email_sent=True
                            )
                    self.signal_ok = True
                    self.silence_start_time = None
                    self.alert_sent = False

                # ── 2. ABSENCE DE MODULATION (console coupée, porteuse présente) ──
                if self.use_tef:
                    # Mode TEF : surveiller la puissance audio depuis TEFAudioAnalyzer
                    mpx_results = self.mpx_analyzer.get_results()
                    mpx_power = mpx_results.get('mpx_power', -100.0)
                    no_modulation = (
                        mpx_power < self.tef_modulation_threshold and
                        mpx_power > -100.0  # -100 = pas encore de données
                    )
                    mod_detail = (
                        f"Signal RF présent ({self.stats.get('signal_dbf', 0):.1f} dBf) "
                        f"mais niveau audio très bas ({mpx_power:.1f} dBFS).\n"
                        f"Seuil : {self.tef_modulation_threshold:.0f} dBFS\n"
                        f"Vérifier la console du studio et la chaîne audio."
                    )
                    mod_restored_detail = (
                        f"La modulation audio est à nouveau détectée.\n"
                        f"Niveau audio : {mpx_power:.1f} dBFS"
                    )
                    mod_message = f"Absence modulation TEF - {mpx_power:.1f} dBFS"
                    mod_restored_message = f"Modulation rétablie TEF - {mpx_power:.1f} dBFS"

                elif len(self.level_history) >= 20:
                    # Mode RTL-SDR : écart-type sur historique PCM
                    std = float(np.std(list(self.level_history)))
                    no_modulation = std < self.modulation_std_threshold
                    mod_detail = (
                        f"Signal FM présent ({current_level:.1f} dB) mais sans modulation audio depuis {{}}s.\n"
                        f"Variation du niveau : ±{std:.2f} dB (seuil : ±{self.modulation_std_threshold} dB)\n"
                        f"Vérifier la console du studio et la chaîne audio."
                    )
                    mod_restored_detail = (
                        f"La modulation audio est à nouveau détectée.\n"
                        f"Variation du niveau : ±{std:.2f} dB"
                    )
                    mod_message = f"Absence modulation - std {std:.2f} dB"
                    mod_restored_message = f"Modulation rétablie - std {std:.2f} dB"
                else:
                    no_modulation = False
                    mod_detail = mod_restored_detail = mod_message = mod_restored_message = ''

                if no_modulation:
                    if self.no_modulation_start is None:
                        self.no_modulation_start = time.time()
                    absence_duration = time.time() - self.no_modulation_start

                    if absence_duration >= self.modulation_alert_delay and not self.modulation_alert_sent:
                        logger.warning(f"Absence modulation ({absence_duration:.0f}s) - ENVOI ALERTE")
                        success = self.email_alert.send_alert(
                            alert_type="Absence de modulation audio",
                            details=mod_detail.format(int(absence_duration)) if '{}' in mod_detail else mod_detail,
                            skip_cooldown=True
                        )
                        if success:
                            self.modulation_alert_sent = True
                            self.modulation_ok = False
                            with self.stats_lock:
                                self.stats['alerts_sent'] += 1
                                self.stats['last_alert'] = datetime.now().isoformat()
                            self.db.save_alert(
                                alert_type='no_modulation',
                                level_db=current_level,
                                duration_seconds=int(absence_duration),
                                message=mod_message,
                                email_sent=True
                            )
                elif mod_message:
                    if self.modulation_alert_sent:
                        logger.info("Modulation rétablie")
                        self.email_alert.send_alert(
                            alert_type="Modulation audio rétablie",
                            details=mod_restored_detail,
                            skip_cooldown=True
                        )
                        self.db.save_alert(
                            alert_type='modulation_restored',
                            level_db=current_level,
                            duration_seconds=0,
                            message=mod_restored_message,
                            email_sent=True
                        )
                    self.modulation_ok = True
                    self.modulation_alert_sent = False
                    self.no_modulation_start = None

                # ── 3. SURVEILLANCE RDS ────────────────────────────────────────
                if self.rds_enabled and self.rds_ever_received:
                    rds_absence = time.time() - self.rds_last_seen if self.rds_last_seen else 0
                    if rds_absence >= self.rds_timeout:
                        if not self.rds_alert_sent:
                            self.rds_ok = False
                            logger.warning(f"RDS absent depuis {rds_absence:.0f}s - ENVOI ALERTE")
                            success = self.email_alert.send_alert(
                                alert_type="Signal RDS absent",
                                details=f"Aucune donnée RDS reçue depuis {int(rds_absence)}s.\n"
                                        f"Vérifier le codeur RDS de la station.",
                                skip_cooldown=True
                            )
                            if success:
                                self.rds_alert_sent = True
                                self.db.save_alert(
                                    alert_type='rds_lost',
                                    level_db=current_level,
                                    duration_seconds=int(rds_absence),
                                    message=f"RDS absent depuis {int(rds_absence)}s",
                                    email_sent=True
                                )
                    else:
                        if self.rds_alert_sent:
                            logger.info("Signal RDS rétabli")
                            self.email_alert.send_alert(
                                alert_type="Signal RDS rétabli",
                                details="Les données RDS sont à nouveau reçues correctement.",
                                skip_cooldown=True
                            )
                            self.db.save_alert(
                                alert_type='rds_restored',
                                level_db=current_level,
                                duration_seconds=0,
                                message="RDS rétabli",
                                email_sent=True
                            )
                        self.rds_ok = True
                        self.rds_alert_sent = False

                # ── 3b. SURVEILLANCE RT (RadioText) ───────────────────────────
                if self.rds_enabled and self.rds_ever_received and self.rds_ok:
                    if self.rt_last_seen is not None:
                        rt_absence = time.time() - self.rt_last_seen
                        if rt_absence >= self.rt_timeout:
                            if not self.rt_alert_sent:
                                logger.warning(f"RT absent depuis {rt_absence:.0f}s - ENVOI ALERTE")
                                success = self.email_alert.send_alert(
                                    alert_type="RadioText absent",
                                    details=f"Aucun RadioText (RT) reçu depuis {int(rt_absence)}s.\n"
                                            f"Vérifier le codeur RDS de la station.",
                                    skip_cooldown=True
                                )
                                if success:
                                    self.rt_alert_sent = True
                                    self.db.save_alert(
                                        alert_type='rt_lost',
                                        level_db=current_level,
                                        duration_seconds=int(rt_absence),
                                        message=f"RadioText absent depuis {int(rt_absence)}s",
                                        email_sent=True
                                    )
                        else:
                            if self.rt_alert_sent:
                                logger.info("RadioText rétabli")
                                self.email_alert.send_alert(
                                    alert_type="RadioText rétabli",
                                    details="Le RadioText est à nouveau reçu correctement.",
                                    skip_cooldown=True
                                )
                                self.db.save_alert(
                                    alert_type='rt_restored',
                                    level_db=current_level,
                                    duration_seconds=0,
                                    message="RadioText rétabli",
                                    email_sent=True
                                )
                            self.rt_alert_sent = False

                # ── 4. SURVEILLANCE SUR-DÉVIATION FM ─────────────────────────
                if self.mpx_enabled and not self.use_tef:
                    mpx = self.mpx_analyzer.get_results()
                    deviation = mpx.get('deviation_peak', 0.0)

                    if deviation > self.deviation_alert_threshold:
                        if self.deviation_over_start is None:
                            self.deviation_over_start = time.time()
                        over_duration = time.time() - self.deviation_over_start

                        if over_duration >= self.deviation_alert_delay and not self.deviation_alert_sent:
                            logger.warning(
                                f"Sur-déviation FM : {deviation:.1f} kHz > "
                                f"{self.deviation_alert_threshold:.0f} kHz depuis {over_duration:.0f}s"
                            )
                            success = self.email_alert.send_alert(
                                alert_type="Sur-déviation FM détectée",
                                details=(
                                    f"Déviation FM : {deviation:.1f} kHz "
                                    f"(seuil : {self.deviation_alert_threshold:.0f} kHz)\n"
                                    f"Durée : {int(over_duration)}s\n"
                                    f"Pilote 19 kHz : {mpx.get('pilot_level', -100):.1f} dBFS\n"
                                    f"Vérifier le processeur audio et le limiter de déviation."
                                ),
                                skip_cooldown=True
                            )
                            if success:
                                self.deviation_alert_sent = True
                                self.db.save_alert(
                                    alert_type='over_deviation',
                                    level_db=current_level,
                                    duration_seconds=int(over_duration),
                                    message=f"Sur-déviation {deviation:.1f} kHz",
                                    email_sent=True
                                )
                    else:
                        if self.deviation_alert_sent:
                            logger.info(f"Déviation revenue dans les limites : {deviation:.1f} kHz")
                            self.email_alert.send_alert(
                                alert_type="Déviation FM normalisée",
                                details=f"Déviation revenue à {deviation:.1f} kHz.",
                                skip_cooldown=True
                            )
                        self.deviation_alert_sent = False
                        self.deviation_over_start = None

                # ── Mettre à jour uptime ──────────────────────────────────────
                if self.stats['start_time']:
                    uptime = (datetime.now() - self.stats['start_time']).total_seconds()
                    with self.stats_lock:
                        self.stats['uptime'] = int(uptime)

                # Historique signal RF : 2 samples/sec pour correspondre au graphique
                time.sleep(0.5)
                with self.stats_lock:
                    mid_level = self.stats.get('signal_dbf') or self.stats['current_level']
                self.add_signal_sample(mid_level)
                time.sleep(0.5)
                with self.stats_lock:
                    end_level = self.stats.get('signal_dbf') or self.stats['current_level']
                self.add_signal_sample(end_level)

            except Exception as e:
                logger.error(f"Erreur surveillance: {e}")
                time.sleep(1)

    def _get_rds_lookup(self, force_refresh=False):
        """Instance RDSLookup partagée, rechargée si demandé."""
        try:
            from rds_lookup import RDSLookup
            if self._rds_lookup is None:
                self._rds_lookup = RDSLookup(country='FR', auto_refresh=False)
            if force_refresh:
                self._rds_lookup.force_refresh()
                logger.info("Base rds-station-db rechargée depuis GitHub")
            return self._rds_lookup
        except Exception as e:
            logger.warning(f"rds_lookup indisponible: {e}")
            return None

    def _rds_db_watcher(self):
        """
        Thread de surveillance rds-station-db.
        Toutes les 24h (ou sur changement de PI) :
          - Recharge la base depuis GitHub
          - Met a jour station_logo si le logo du PI courant a change
"""
        import time as _time
        INTERVAL = 24 * 3600
        while self.running:
            elapsed = 0
            while self.running and elapsed < INTERVAL:
                _time.sleep(60)
                elapsed += 60
                if self._rds_db_reload:
                    logger.info("Watcher: rechargement anticipe (changement PI)")
                    self._rds_db_reload = False
                    break
            if not self.running:
                break
            try:
                pi = self.stats.get('pi', '').strip().upper()
                if not pi or pi == '-':
                    continue
                ps_current = self.stats.get('ps', '').strip()
                lookup = self._get_rds_lookup(force_refresh=True)
                if not lookup:
                    continue
                # Recherche exacte PI+PS pour gérer les PI partagés (ex: RCF)
                station = lookup.get(pi=pi, ps=ps_current) if ps_current else lookup.get_by_pi(pi)
                if not station:
                    logger.info(f"Watcher: PI {pi} / PS '{ps_current}' non trouve dans la base")
                    continue
                new_logo = station.get('logo_url')
                current_logo = self.stats.get('station_logo')
                if new_logo and new_logo != current_logo:
                    logger.info(
                        f"Watcher: logo mis a jour [{pi}] {station.get('name')}: {new_logo}"
                    )
                    with self.stats_lock:
                        self.stats['station_logo'] = new_logo
                else:
                    logger.info(
                        f"Watcher: logo inchange [{pi}] {station.get('name')}"
                    )
            except Exception as e:
                logger.warning(f"Watcher rds-station-db erreur: {e}")

    def _fetch_station_logo(self):
        """
        Recherche le logo de la station recue.
        Recherche le logo de la station recue via rds-station-db (PI + PS).
"""
        import time as _time
        if _time.time() - self._logo_last_attempt < 60:
            return
        self._logo_last_attempt = _time.time()
        try:
            for _ in range(10):
                pi = self.stats.get('pi', '').strip().upper()
                ps = self.stats.get('ps', '').strip().upper()
                if pi and pi != '-' and ps and ps != '-':
                    break
                _time.sleep(0.5)
            pi = self.stats.get('pi', '').strip().upper()
            ps = self.stats.get('ps', '').strip()
            ps_upper = ps.upper()
            freq_raw = self.rtl_config.get('frequency', '')
            freq_mhz = freq_raw.replace('M', '').replace('m', '')
            station_name = self.config.get('station', {}).get('name', ps) or ps

            # Validation minimale : PI et PS doivent être disponibles
            if not pi or pi == '-' or not ps or ps == '-':
                logger.info("PI ou PS non disponible, logo non recherché")
                return

            # Priorite 1 : rds-station-db
            # get(pi+ps) : correspondance exacte, gère les PI partagés (ex: RCF)
            lookup = self._get_rds_lookup(force_refresh=True)
            if lookup:
                station = lookup.get(pi=pi, ps=ps)
                if station:
                    if station.get('logo_url'):
                        logo_url = station['logo_url']
                        logger.info(
                            f"Logo rds-station-db [{pi}/{ps}] "
                            f"{station.get('name')}: {logo_url}"
                        )
                        self._logo_searched = True
                        with self.stats_lock:
                            self.stats['station_logo'] = logo_url
                        return
                    else:
                        logger.info(
                            f"Station [{pi}/{ps}] {station.get('name')} "
                            f"trouvee mais sans logo"
                        )

            # Aucun logo trouvé dans rds-station-db
            self._logo_fail_count += 1
            logger.info(
                f"Aucun logo trouve pour [{pi}/{ps}] dans rds-station-db "
                f"(echec #{self._logo_fail_count})"
            )

        except Exception as e:
            logger.warning(f"Erreur recherche logo: {e}")

    def get_audio_chunk(self):
        """Récupère un chunk audio pour le streaming"""
        if not self.audio_enabled:
            return None
        try:
            return self.stream_queue.get(timeout=0.1)
        except queue.Empty:
            return None

    def _start_webhook(self):
        """Démarre le thread de push webhook si configuré."""
        webhook_cfg = self.config.get('webhook', {})
        logger.info(f"_start_webhook: cfg={webhook_cfg}")
        if not webhook_cfg.get('enabled') or not webhook_cfg.get('url'):
            logger.info("_start_webhook: désactivé ou URL manquante")
            return
        self._webhook_thread = threading.Thread(
            target=self._webhook_loop, daemon=True, name='webhook-push'
        )
        self._webhook_thread.start()
        logger.info(f"Webhook push démarré → {webhook_cfg['url']}")

    def _webhook_loop(self):
        """Pousse les stats vers l'URL webhook toutes les N secondes."""
        import datetime
        webhook_cfg = self.config.get('webhook', {})
        url      = webhook_cfg.get('url', '')
        interval = float(webhook_cfg.get('interval', 1))
        while self.running:
            try:
                stats = self.get_stats()
                payload = {
                    'station':    stats.get('ps', '-'),
                    'frequency':  stats.get('frequency', ''),
                    'signal_db':  round(float(stats.get('current_level', -100)), 1),
                    'signal_ok':  bool(stats.get('signal_ok', False)),
                    'ps':         stats.get('ps', '-'),
                    'rt':         stats.get('rt', '-'),
                    'pi':         stats.get('pi', '-'),
                    'stereo':     bool(stats.get('stereo_present', False)),
                    'alert':      stats.get('last_alert'),
                    'timestamp':  datetime.datetime.now().isoformat()
                }
                requests.post(url, json=payload, timeout=3)
            except Exception as e:
                logger.debug(f"Webhook push erreur: {e}")
            time.sleep(interval)

    def get_stats(self):
        """Récupère les statistiques"""
        with self.stats_lock:
            stats = self.stats.copy()

        stats['signal_ok'] = self.signal_ok
        stats['modulation_ok'] = self.modulation_ok
        stats['rds_ok'] = self.rds_ok
        stats['rds_ever_received'] = self.rds_ever_received
        stats['use_tef'] = self.use_tef
        stats['frequency'] = self.rtl_config['frequency']
        stats['station_logo'] = self.stats.get('station_logo')

        # Données MPX (déviation, pilote, stéréo, RDS RF)
        if self.mpx_enabled:
            mpx = self.mpx_analyzer.get_results()
            if self.use_tef:
                # En mode TEF, stereo_present vient du bit MS RDS — ne pas écraser
                mpx.pop('stereo_present', None)
            stats.update(mpx)

        if stats['start_time']:
            stats['start_time'] = stats['start_time'].strftime('%d/%m/%Y %H:%M:%S')

        return stats

    def add_signal_sample(self, level):
        self.signal_history.append({'t': int(time.time() * 1000), 'l': round(level, 1)})

    def get_signal_history(self):
        return list(self.signal_history)

    def stop(self):
        """Arrête le moniteur"""
        logger.info("Arrêt du moniteur FM")
        self.running = False
        self.level_history.clear()
        self.modulation_ok = True
        self.modulation_alert_sent = False
        self.no_modulation_start = None
        self.rds_ok = False
        self.rds_ever_received = False
        self.rds_alert_sent = False
        self._logo_searched = False
        self._logo_last_attempt = 0
        self._logo_fail_count = 0
        self._rds_db_reload = False
        self._rds_lookup = None
        self.stats['station_logo'] = None
        self.deviation_alert_sent = False
        self.deviation_over_start = None
        self.mpx_analyzer.reset()

        if self.master_process:
            self.master_process.kill()

        if self.tef_driver:
            self.tef_driver.stop()
            self.tef_driver = None

        if _TEF_AUDIO_AVAILABLE and hasattr(self.mpx_analyzer, 'stop'):
            self.mpx_analyzer.stop()

        os.system("pkill -9 rtl_fm 2>/dev/null")
        os.system("pkill -9 -f wfm_stereo.py 2>/dev/null")
        if hasattr(self, 'redsea_process') and self.redsea_process:
            try: self.redsea_process.kill()
            except: pass
        os.system("pkill -9 sox 2>/dev/null")
        os.system("pkill -9 ffmpeg 2>/dev/null")
        os.system("pkill -9 redsea 2>/dev/null")

        # Attendre que tous les threads soient bien terminés
        for attr in ['master_thread', 'monitor_thread', 'watchdog_thread',
                     'stream_thread', 'db_writer_thread']:
            t = getattr(self, attr, None)
            if t and t.is_alive():
                t.join(timeout=3)
                if t.is_alive():
                    logger.warning(f"Thread {attr} non terminé après 3s")

        # Supprimer le FIFO
        fifo_path = '/tmp/fm_stream.mp3'
        if os.path.exists(fifo_path):
            try:
                os.remove(fifo_path)
                logger.info("FIFO supprimé")
            except:
                pass

        self.stats['status'] = 'Arrêté'
