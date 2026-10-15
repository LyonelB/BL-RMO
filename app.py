#!/usr/bin/env python3
"""
Application Flask pour le monitoring FM - Version sécurisée complète
"""
from flask import Flask, render_template, Response, jsonify, request, session, redirect, url_for
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, generate_csrf
import logging
import time
import json
import subprocess
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from monitor import FMMonitor
from auth import Auth
import network_config

# Charger les variables d'environnement
load_dotenv()

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Secret key depuis variable d'environnement (ou génération aléatoire)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32).hex()

# Configuration Bcrypt pour hashage sécurisé des mots de passe
bcrypt = Bcrypt(app)

# Configuration CSRF Protection
csrf = CSRFProtect(app)

# Configuration Rate Limiting pour protection contre les attaques
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

auth = Auth()

SESSION_TIMEOUT = timedelta(minutes=60)

@app.before_request
def check_session_timeout():
    # Endpoints exclus du check session (polling automatique)
    POLLING_ENDPOINTS = ('chain_status', 'stream_stats', 'proxy_stream', 'static',
                         'get_stats', 'get_signal_history', 'get_record_status',
                         'pige_status')
    if request.endpoint in POLLING_ENDPOINTS:
        # Vérifier expiration mais NE PAS mettre à jour last_active
        if session.get('logged_in'):
            last_active = session.get('last_active')
            if last_active:
                elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_active).replace(tzinfo=timezone.utc)
                if elapsed > SESSION_TIMEOUT:
                    session.clear()
                    if request.is_json:
                        from flask import abort
                        abort(401)
                    return redirect(url_for('login', timeout=1))
        return
    if session.get('logged_in'):
        last_active = session.get('last_active')
        if last_active:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_active).replace(tzinfo=timezone.utc)
            if elapsed > SESSION_TIMEOUT:
                session.clear()
                if request.is_json:
                    from flask import abort
                    abort(401)
                return redirect(url_for('login', timeout=1))
        session['last_active'] = datetime.now(timezone.utc).isoformat()

    # ── Redirection vers le wizard de première connexion ──────────────────
    # Tant que la configuration initiale n'est pas terminée, on force /setup.
    if session.get('logged_in') and request.endpoint not in (
        'setup_wizard', 'save_config', 'scan_dongle', 'get_config_full',
        'login', 'logout', 'static'
    ):
        try:
            with open('config.json', 'r') as _f:
                _cfg = json.load(_f)
            if not _cfg.get('setup', {}).get('completed', True):
                # Ne rediriger que les pages HTML, pas les appels API/JSON.
                if not request.path.startswith('/api/') and not request.is_json:
                    return redirect(url_for('setup_wizard'))
        except Exception:
            pass

# Instance globale du moniteur
monitor = None

# Cache pour les stats (évite les verrous excessifs)
stats_cache = {'data': None, 'timestamp': 0}

def generate_stats_sse():
    """Générateur SSE qui pousse les stats toutes les 50ms"""
    import json
    while True:
        try:
            if monitor:
                data = monitor.get_stats()
                yield f"data: {json.dumps(data)}\n\n"
            time.sleep(0.05)
        except GeneratorExit:
            break
        except Exception as e:
            logger.error(f"Erreur SSE: {e}")
            time.sleep(0.1)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")  # Maximum 5 tentatives de connexion par minute
def login():
    """Page de connexion - Sécurisée avec rate limiting"""
    if request.method == 'POST':
        # Supporter à la fois JSON et form data
        if request.is_json:
            data = request.get_json()
            username = data.get('username')
            password = data.get('password')
            remember = data.get('remember', False)
        else:
            username = request.form.get('username')
            password = request.form.get('password')
            remember = request.form.get('remember') == 'on'

        if auth.verify_credentials(username, password):
            session['logged_in'] = True
            session['username'] = username
            session['last_active'] = datetime.now(timezone.utc).isoformat()
            session.permanent = True

            logger.info(f"Connexion réussie pour {username}")

            # Réponse JSON pour la nouvelle page
            if request.is_json:
                next_page = request.args.get('next', '/')
                return jsonify({
                    'status': 'success',
                    'message': 'Connexion réussie',
                    'redirect': next_page
                })
            # Réponse classique pour ancienne page
            else:
                next_page = request.args.get('next', '/')
                return redirect(next_page)
        else:
            logger.warning(f"Tentative de connexion échouée pour {username}")

            # Réponse JSON pour la nouvelle page
            if request.is_json:
                return jsonify({
                    'status': 'error',
                    'message': 'Nom d\'utilisateur ou mot de passe incorrect'
                }), 401
            # Réponse classique pour ancienne page
            else:
                return render_template('login.html', error='Identifiants incorrects')

    return render_template('login.html')

@app.route('/logout')
def logout():
    """Déconnexion"""
    username = session.get('username', 'unknown')
    session.clear()
    logger.info(f"Déconnexion de {username}")
    return redirect(url_for('login'))

@app.route('/api/csrf-token')
def get_csrf_token():
    """Retourne un token CSRF pour les requêtes AJAX"""
    return jsonify({'csrf_token': generate_csrf()})

@app.route('/public')
def public_dashboard():
    return render_template('index.html', public_mode=True)

@app.route('/')
@auth.login_required
def index():
    """Page d'accueil avec le dashboard"""
    return render_template('index.html')

@app.route('/setup')
@auth.login_required
def setup_wizard():
    """Assistant de première connexion."""
    return render_template('setup.html')

@app.route('/config')
@auth.login_required
def config():
    """Page de configuration"""
    return render_template('config.html')

@app.route('/stats')
@auth.login_required
def stats():
    """Page des statistiques"""
    return render_template('stats.html')

@app.route('/api/stats')
@limiter.exempt
def get_stats():
    """Récupère les statistiques avec cache 100ms"""
    now = time.time()
    if stats_cache['data'] and (now - stats_cache['timestamp']) < 0.1:
        return jsonify(stats_cache['data'])

    if monitor:
        data = monitor.get_stats()
        stats_cache['data'] = data
        stats_cache['timestamp'] = now
        return jsonify(data)

    return jsonify({'error': 'Monitor not initialized'}), 503

@app.route('/api/stream/stats')
@limiter.exempt  # Exemption rate limiting pour SSE continu
@csrf.exempt  # Exemption CSRF pour SSE (Server-Sent Events)
def stream_stats():
    """SSE : pousse les stats en continu vers le navigateur"""
    return Response(
        generate_stats_sse(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


@app.route('/api/config/full')
def get_config_full():
    """Retourne la configuration complète (mot de passe masqué)"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        # Masquer le mot de passe email
        if 'email' in config and 'sender_password' in config['email']:
            config['email']['sender_password'] = '********'
        return jsonify(config)
    except Exception as e:
        logger.error(f"Erreur lors de la lecture de la config: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/config/save', methods=['POST'])
@auth.login_required
def save_config():
    """Sauvegarde la configuration"""
    try:
        data = request.get_json()

        with open('config.json', 'r') as f:
            config = json.load(f)

        # Configuration station
        if 'station' in data:
            if 'name' in data['station']:
                config['station']['name'] = data['station']['name']
            if 'frequency' in data['station']:
                config['station']['frequency'] = data['station']['frequency']
                config['station']['frequency_display'] = data['station']['frequency']

        # Configuration RTL-SDR
        if 'rtl_sdr' in data:
            if 'frequency' in data['rtl_sdr']:
                config['rtl_sdr']['frequency'] = data['rtl_sdr']['frequency']

            if 'gain' in data['rtl_sdr']:
                gain = str(data['rtl_sdr']['gain']).strip()
                if gain == '0':
                    gain = 'auto'
                config['rtl_sdr']['gain'] = gain
                logger.info(f"Gain modifié: {gain}")

        # Configuration audio
        if 'audio' in data:
            if 'silence_threshold' in data['audio']:
                config['audio']['silence_threshold'] = float(data['audio']['silence_threshold'])
            if 'silence_duration' in data['audio']:
                config['audio']['silence_duration'] = int(data['audio']['silence_duration'])
            if 'modulation_alert_delay' in data['audio']:
                config['audio']['modulation_alert_delay'] = int(data['audio']['modulation_alert_delay'])
                if monitor:
                    monitor.modulation_alert_delay = int(data['audio']['modulation_alert_delay'])
            if 'modulation_std_threshold' in data['audio']:
                config['audio']['modulation_std_threshold'] = float(data['audio']['modulation_std_threshold'])
                if monitor:
                    monitor.modulation_std_threshold = float(data['audio']['modulation_std_threshold'])
            if 'signal_lost_threshold' in data['audio']:
                config['audio']['signal_lost_threshold'] = float(data['audio']['signal_lost_threshold'])
                if monitor:
                    monitor.signal_lost_threshold = float(data['audio']['signal_lost_threshold'])
            if 'rds_timeout' in data['audio']:
                config['audio']['rds_timeout'] = int(data['audio']['rds_timeout'])
                if monitor:
                    monitor.rds_timeout = int(data['audio']['rds_timeout'])
            if 'rt_timeout' in data['audio']:
                config['audio']['rt_timeout'] = int(data['audio']['rt_timeout'])
                if monitor:
                    monitor.rt_timeout = int(data['audio']['rt_timeout'])

        # Configuration email
        if 'email' in data:
            if 'sender_email' in data['email']:
                config['email']['sender_email'] = data['email']['sender_email']
            if 'sender_password' in data['email']:
                pwd = data['email']['sender_password']
                if pwd and pwd.strip() and pwd.strip() != '********':
                    # Supprimer tous les espaces (format Google : "xxxx xxxx xxxx xxxx")
                    config['email']['sender_password'] = pwd.replace(' ', '')
            if 'recipient_emails' in data['email']:
                emails = data['email']['recipient_emails']
                if isinstance(emails, str):
                    emails = [e.strip() for e in emails.split(',') if e.strip()]
                config['email']['recipient_emails'] = emails
            if 'alerts_enabled' in data['email']:
                enabled = bool(data['email']['alerts_enabled'])
                config['email']['alerts_enabled'] = enabled
                if monitor and getattr(monitor, 'email_alert', None):
                    monitor.email_alert.alerts_enabled = enabled

        # Configuration des identifiants de connexion (avec Bcrypt)
        if 'auth' in data:
            if 'auth' not in config:
                config['auth'] = {}

            auth_data = data['auth']

            # Modifier le nom d'utilisateur si fourni
            if 'username' in auth_data and auth_data['username']:
                old_username = config['auth'].get('username', 'unknown')
                config['auth']['username'] = auth_data['username']
                logger.info(f"Nom d'utilisateur modifié: {old_username} → {auth_data['username']}")

            # Modifier le mot de passe si fourni (hashage avec Bcrypt)
            if 'password' in auth_data and auth_data['password']:
                # Hasher le mot de passe avec Bcrypt (SÉCURISÉ)
                password_hash = bcrypt.generate_password_hash(auth_data['password']).decode('utf-8')
                config['auth']['password_hash'] = password_hash
                logger.info("Mot de passe modifié et hashé avec Bcrypt")

                # Recharger l'instance Auth avec les nouveaux identifiants
                global auth
                auth = Auth()

        # Configuration réseau (nouveau)
        if 'network' in data:
            if 'network' not in config:
                config['network'] = {}

            network_data = data['network']
            # Valider AVANT d'écrire quoi que ce soit (400 si saisie invalide)
            try:
                network_config.validate(network_data)
            except network_config.NetworkConfigError as e:
                return jsonify({'status': 'error', 'message': f'Réseau : {e}'}), 400
            config['network']['mode'] = network_data.get('mode', 'dhcp')
            config['network']['ip'] = network_data.get('ip', '')
            config['network']['netmask'] = network_data.get('netmask', '')
            config['network']['gateway'] = network_data.get('gateway', '')
            config['network']['dns'] = network_data.get('dns', '')
            config['network']['wifi_ssid'] = network_data.get('wifi_ssid', '')

            # Ne pas écraser le mot de passe WiFi s'il est vide (sécurité)
            wifi_password = network_data.get('wifi_password', '')
            if wifi_password and wifi_password.strip():
                config['network']['wifi_password'] = wifi_password
                logger.info("Mot de passe WiFi mis à jour")

        # Configuration de la source (TEF vs RTL-SDR) — pour le wizard
        if 'tef' in data:
            if 'tef' not in config:
                config['tef'] = {}
            if 'enabled' in data['tef']:
                config['tef']['enabled'] = bool(data['tef']['enabled'])
            if 'serial_port' in data['tef']:
                config['tef']['serial_port'] = data['tef']['serial_port']
            if 'alsa_device' in data['tef']:
                config['tef']['alsa_device'] = data['tef']['alsa_device']

        # Champs SMTP (structure email complète attendue par email_alert.py)
        if 'email' in data:
            if 'smtp_server' in data['email']:
                config['email']['smtp_server'] = data['email']['smtp_server']
            if 'smtp_port' in data['email']:
                config['email']['smtp_port'] = int(data['email']['smtp_port'])

        # Flag de fin du wizard
        if 'setup' in data:
            if 'setup' not in config:
                config['setup'] = {}
            if 'completed' in data['setup']:
                config['setup']['completed'] = bool(data['setup']['completed'])

        # Sauvegarder le fichier config.json
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)

        logger.info("Configuration sauvegardee avec succes")

        # Recharger a chaud le nom de station / frequence affiches et les identifiants
        # SMTP utilises par les emails d'alerte. EmailAlert ne les lit qu'une fois au
        # demarrage du service : sans ce rechargement, changer le nom de station ou la
        # frequence dans ce formulaire laisse les emails d'alerte utiliser les anciennes
        # valeurs tant que le service fm-monitor n'est pas redemarre.
        if ('station' in data or 'email' in data) and monitor and getattr(monitor, 'email_alert', None):
            monitor.email_alert.reload_config()

        # Redemarrer le monitoring si la frequence, le gain OU la source a change.
        needs_restart = (
            ('rtl_sdr' in data and ('frequency' in data['rtl_sdr'] or 'gain' in data['rtl_sdr']))
            or ('station' in data and 'frequency' in data['station'])
            or ('tef' in data and 'enabled' in data['tef'])
        )
        if needs_restart and monitor:
            logger.info("Config source/frequence modifiee - redemarrage du monitoring")
            monitor.stop()
            time.sleep(2)
            monitor.config = config
            monitor.rtl_config = config['rtl_sdr']
            monitor.audio_config = config['audio']
            # Recharger la config TEF pour une bascule de source a chaud.
            if 'tef' in config:
                monitor.tef_config = config['tef']
                monitor.use_tef = config['tef'].get('enabled', False)
            monitor.start()

        # Appliquer la configuration réseau (NetworkManager) si elle diffère de l'état actuel
        network_result = None
        if 'network' in data:
            try:
                network_result = network_config.schedule_apply(data['network'])
                if network_result.get('changed'):
                    logger.info(f"Changement réseau planifié : {network_result}")
            except Exception as e:
                logger.error(f"Configuration réseau non appliquée : {e}")
                return jsonify({'status': 'error',
                                'message': f'Configuration enregistrée mais réseau non appliqué : {e}'}), 500

        return jsonify({'status': 'success', 'message': 'Configuration enregistrée',
                        'network': network_result})

    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde de la config: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/presets')
@auth.login_required
def get_presets():
    """Retourne la liste des presets"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return jsonify(config.get('presets', []))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs')
@auth.login_required
def get_logs():
    """Récupère les derniers logs système"""
    try:
        import subprocess
        result = subprocess.run(
            ['journalctl', '-u', 'fm-monitor', '-n', '100', '--no-pager'],
            capture_output=True,
            text=True
        )
        return jsonify({'logs': result.stdout})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test-email', methods=['POST'])
@auth.login_required
def test_email():
    """Envoie un email de test"""
    try:
        if monitor and monitor.email_alert:
            success = monitor.email_alert.send_alert(
                alert_type="Test",
                details="Ceci est un email de test depuis FM Monitor.",
                skip_cooldown=True
            )
            if success:
                return jsonify({'status': 'success', 'message': 'Email envoyé'})
            else:
                return jsonify({'status': 'error', 'message': "Erreur d'envoi"}), 500
        return jsonify({'status': 'error', 'message': 'Monitor not initialized'}), 503
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/signal/history')
@limiter.exempt
def get_signal_history():
    """Retourne les 60 dernières secondes de niveau RF"""
    if monitor:
        return jsonify(monitor.get_signal_history())
    return jsonify([])

@app.route('/api/audio/history')
@auth.login_required
@limiter.exempt  # Exemption rate limiting pour historique appelé fréquemment
def get_audio_history():
    """Récupère l'historique des niveaux audio (24h)"""
    try:
        if monitor and hasattr(monitor, 'db'):
            history = monitor.db.get_audio_history(hours=24)
            return jsonify({'status': 'success', 'data': history})
        return jsonify({'status': 'error', 'message': 'Database not available'}), 503
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/alerts/history')
@auth.login_required
def get_alerts_history():
    """Récupère l'historique des alertes"""
    try:
        if monitor and hasattr(monitor, 'db'):
            alerts = monitor.db.get_alerts_history(limit=50)
            return jsonify({'status': 'success', 'data': alerts})
        return jsonify({'status': 'error', 'message': 'Database not available'}), 503
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/alerts/history/grouped')
@limiter.exempt
@auth.login_required
def get_alerts_history_grouped():
    """Récupère l'historique des alertes groupées par paires"""
    try:
        if monitor and hasattr(monitor, 'db'):
            alerts = monitor.db.get_alerts_history_grouped(limit=50)
            return jsonify({'status': 'success', 'data': alerts})
        return jsonify({'status': 'error', 'message': 'Database not available'}), 503
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/webhook', methods=['GET', 'POST'])
@auth.login_required
def webhook_settings():
    if request.method == 'GET':
        with open('config.json') as f:
            cfg = json.load(f)
        return jsonify(cfg.get('webhook', {}))
    data = request.get_json()
    with open('config.json') as f:
        cfg = json.load(f)
    cfg['webhook'] = {
        'enabled': bool(data.get('enabled', False)),
        'url':     data.get('url', ''),
        'interval': int(data.get('interval', 1))
    }
    with open('config.json', 'w') as f:
        json.dump(cfg, f, indent=2)
    return jsonify({'status': 'ok', 'message': 'Webhook sauvegardé — redémarrez le service pour appliquer'})

@app.route('/api/restart', methods=['POST'])
@auth.login_required
def restart_monitoring():
    """Redémarre le monitoring"""
    global monitor
    try:
        if monitor:
            logger.info("Redémarrage du monitoring demandé")
            monitor.stop()
            time.sleep(2)
            monitor.start()
            return jsonify({'status': 'success', 'message': 'Monitoring redémarré'})
        return jsonify({'status': 'error', 'message': 'Monitor not initialized'}), 503
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/rds/read_ps', methods=['POST'])
@auth.login_required
def read_rds_ps():
    """Lecture ponctuelle PS et RT"""
    try:
        monitor.read_rds_once(duration=10)
        return jsonify({
            'status': 'success',
            'ps': monitor.stats.get('ps', '-'),
            'rt': monitor.stats.get('rt', '-')
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/rds/read_rt', methods=['POST'])
@auth.login_required
def read_rds_rt():
    """Lecture ponctuelle du RadioText"""
    try:
        monitor.read_rds_once(duration=10)
        return jsonify({'status': 'success', 'rt': monitor.stats.get('rt', 'Non disponible')})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =============================================
# VOLUME DAC+
# =============================================

@app.route('/api/volume')
@auth.login_required
def get_volume():
    """Retourne le volume actuel de la carte DAC+ (contrôle Digital, en %)."""
    try:
        import re
        result = subprocess.run(
            ['amixer', '-c', 'DAC', 'sget', 'Digital'],
            capture_output=True, text=True, timeout=5
        )
        match = re.search(r'\[(\d+)%\]', result.stdout)
        if match:
            return jsonify({'status': 'success', 'volume': int(match.group(1))})
        return jsonify({'status': 'error', 'message': 'Impossible de lire le volume'}), 500
    except Exception as e:
        logger.error(f"Erreur lecture volume DAC+ : {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/volume/set', methods=['POST'])
@auth.login_required
def set_volume():
    """Applique un volume sur la carte DAC+ via amixer."""
    try:
        data = request.get_json()
        volume = int(data.get('volume', 80))
        volume = max(0, min(100, volume))
        result = subprocess.run(
            ['amixer', '-c', 'DAC', 'sset', 'Digital', f'{volume}%'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            logger.info(f"Volume DAC+ réglé à {volume}%")
            return jsonify({'status': 'success', 'volume': volume})
        else:
            logger.error(f"Erreur amixer : {result.stderr}")
            return jsonify({'status': 'error', 'message': result.stderr}), 500
    except Exception as e:
        logger.error(f"Erreur réglage volume DAC+ : {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# =============================================
# PAGE À PROPOS
# =============================================

@app.route('/about')
@auth.login_required
def about_page():
    """Page de documentation"""
    return render_template('about.html')

@app.route('/stream.mp3')
@limiter.exempt  # Exemption rate limiting pour le stream audio
def proxy_stream():
    """Proxifie le stream Icecast HTTPS via Flask"""
    import requests

    def generate():
        try:
            # Connexion au stream Icecast en HTTPS (verify=False car certificat auto-signé)
            with requests.get('http://localhost:8000/fmmonitor', stream=True, timeout=5) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        except Exception as e:
            logger.error(f"Erreur proxy stream: {e}")

    return app.response_class(
        generate(),
        mimetype='audio/mpeg',
        headers={
            'Cache-Control': 'no-cache, no-store',
            'X-Content-Type-Options': 'nosniff',
            'Access-Control-Allow-Origin': '*'
        }
    )

# =============================================
# ENREGISTREMENT AUDIO
# =============================================

record_process = None
record_filepath = None
RECORD_MAX_BYTES = 50 * 1024 * 1024  # 50 Mo
RECORD_DIR = '/tmp'
RECORD_MAX_AGE = 30 * 60  # 30 minutes

def cleanup_orphan_records():
    """Supprime les fichiers d'enregistrement orphelins dans /tmp"""
    import glob, time
    pattern = os.path.join(RECORD_DIR, 'fm-monitor_*.mp3')
    for f in glob.glob(pattern):
        try:
            age = time.time() - os.path.getmtime(f)
            if age > RECORD_MAX_AGE:
                os.remove(f)
                logger.info(f"Fichier orphelin supprimé: {f}")
        except Exception as e:
            logger.warning(f"Impossible de supprimer {f}: {e}")

def start_cleanup_scheduler():
    """Lance un thread de nettoyage toutes les 10 minutes"""
    import threading, time
    def _loop():
        while True:
            time.sleep(600)  # 10 minutes
            cleanup_orphan_records()
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

@app.route('/api/record/start', methods=['POST'])
@auth.login_required
def record_start():
    global record_process, record_filepath
    if record_process and record_process.poll() is None:
        return jsonify({'status': 'error', 'message': 'Enregistrement déjà en cours'}), 400

    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    record_filepath = os.path.join(RECORD_DIR, f'fm-monitor_{timestamp}.mp3')

    try:
        record_process = subprocess.Popen([
            'ffmpeg', '-y',
            '-i', 'http://localhost:8000/fmmonitor',
            '-c', 'copy',
            '-fs', str(RECORD_MAX_BYTES),
            record_filepath
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({'status': 'ok', 'message': 'Enregistrement démarré'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/record/stop', methods=['POST'])
@auth.login_required
def record_stop():
    global record_process, record_filepath
    if not record_process or record_process.poll() is not None:
        return jsonify({'status': 'error', 'message': 'Aucun enregistrement en cours'}), 400

    record_process.terminate()
    try:
        record_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        record_process.kill()

    record_process = None

    if record_filepath and os.path.exists(record_filepath):
        size = os.path.getsize(record_filepath)
        return jsonify({'status': 'ok', 'filepath': record_filepath, 'size': size})
    else:
        return jsonify({'status': 'error', 'message': 'Fichier introuvable'}), 500

@app.route('/api/record/download')
@auth.login_required
def record_download():
    global record_filepath
    if not record_filepath or not os.path.exists(record_filepath):
        return jsonify({'status': 'error', 'message': 'Aucun fichier à télécharger'}), 404

    filepath = record_filepath
    filename = os.path.basename(filepath)
    record_filepath = None

    from flask import send_file
    import threading

    def delete_after_send(path):
        import time
        time.sleep(5)
        try:
            os.remove(path)
        except Exception:
            pass

    threading.Thread(target=delete_after_send, args=(filepath,), daemon=True).start()

    return send_file(filepath, mimetype='audio/mpeg',
                     as_attachment=True, download_name=filename)

@app.route('/api/record/status')
@auth.login_required
def record_status():
    global record_process, record_filepath
    recording = record_process is not None and record_process.poll() is None
    size = 0
    if record_filepath and os.path.exists(record_filepath):
        size = os.path.getsize(record_filepath)
    # Arrêt automatique si 50 Mo atteints
    if recording and size >= RECORD_MAX_BYTES:
        record_process.terminate()
        try:
            record_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            record_process.kill()
        record_process = None
        recording = False
    return jsonify({'recording': recording, 'size': size, 'max': RECORD_MAX_BYTES})



@app.route('/api/scan-dongle', methods=['POST'])
@auth.login_required
def scan_dongle():
    """Détecte les dongles connectés : TEF (série) et/ou RTL-SDR."""
    import glob, serial as _serial
    result = {'tef': [], 'rtlsdr': False}

    # Détection TEF Headless FMDX.org par VID:PID (déterministe).
    # 1209:6687 = FMDX.org TEF668X Headless (firmware FM-DX-Tuner, port CDC-ACM).
    import subprocess as _sp
    fmdx_present = False
    try:
        _lsusb = _sp.run(['lsusb'], capture_output=True, text=True, timeout=5)
        if '1209:6687' in (_lsusb.stdout + _lsusb.stderr):
            fmdx_present = True
    except Exception:
        pass
    # Associe le VID:PID à son /dev/ttyACM* (le firmware s'énumère en CDC-ACM).
    acm_ports = sorted(glob.glob('/dev/ttyACM*'))
    if fmdx_present:
        result['tef'].extend(acm_ports if acm_ports else ['/dev/ttyACM0'])
    # Fallback : probe série PE5PVB pour un TEF6686 CH340 sur ttyUSB* (ancien montage).
    usb_ports = sorted(glob.glob('/dev/ttyUSB*'))
    for port in usb_ports:
        try:
            s = _serial.Serial(port, 115200, timeout=2)
            s.reset_input_buffer()
            s.write(b'x')
            import time; time.sleep(0.8)
            data = s.read(200).decode('ascii', errors='ignore')
            s.close()
            if 'OK' in data or 'T8' in data or 'Ss' in data:
                result['tef'].append(port)
        except Exception:
            pass
    # Dédoublonnage en préservant l'ordre.
    result['tef'] = list(dict.fromkeys(result['tef']))

    # Scan RTL-SDR
    try:
        r = subprocess.run(['rtl_test', '-t'], capture_output=True, text=True, timeout=5)
        output = r.stdout + r.stderr
        if 'Found' in output and ('R820' in output or 'RTL' in output):
            result['rtlsdr'] = True
    except Exception:
        pass

    return jsonify(result)


@app.route('/api/select-source', methods=['POST'])
@auth.login_required
def select_source():
    """Applique la source choisie (tef ou rtlsdr) et redémarre le service."""
    data = request.get_json()
    source = data.get('source')  # 'tef' ou 'rtlsdr'
    port = data.get('port', '/dev/ttyUSB0')

    try:
        with open('config.json', 'r') as f:
            config = json.load(f)

        if source == 'tef':
            if 'tef' not in config:
                config['tef'] = {}
            config['tef']['enabled'] = True
            config['tef']['serial_port'] = port
            config['tef'].setdefault('alsa_device', 'hw:Tuner')
            config['tef'].setdefault('signal_threshold_dbf', 20.0)
            config['tef'].setdefault('modulation_threshold_dbfs', -40.0)
            config['decoder'] = 'rtl_fm'
        elif source == 'gnuradio':
            if 'tef' in config:
                config['tef']['enabled'] = False
            config['decoder'] = 'gnuradio'
        else:
            if 'tef' in config:
                config['tef']['enabled'] = False
            config['decoder'] = 'rtl_fm' 

        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)

        # Redémarrage non-bloquant
        subprocess.Popen(['sudo', 'systemctl', 'restart', 'fm-monitor'])
        logger.info(f"Source sélectionnée : {source} ({port if source == 'tef' else 'RTL-SDR'})")
        return jsonify({'status': 'success', 'source': source})

    except Exception as e:
        logger.error(f"Erreur select-source : {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/wifi/status')
@auth.login_required
def wifi_status():
    """Retourne l'état du WiFi système"""
    try:
        result = subprocess.run(['nmcli', 'radio', 'wifi'],
            capture_output=True, text=True, timeout=5)
        enabled = result.stdout.strip() == 'enabled'
        return jsonify({'enabled': enabled})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/wifi/toggle', methods=['POST'])
@auth.login_required
@csrf.exempt
def wifi_toggle():
    """Active ou désactive le WiFi système"""
    try:
        data = request.get_json()
        enable = data.get('enable', True)
        action = 'on' if enable else 'off'
        result = subprocess.run(['sudo', 'nmcli', 'radio', 'wifi', action],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info(f"WiFi {'activé' if enable else 'désactivé'}")
            return jsonify({'status': 'success', 'enabled': enable})
        else:
            return jsonify({'status': 'error', 'message': result.stderr.strip()}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/email-alerts/status')
@auth.login_required
def email_alerts_status():
    """Retourne l'etat des alertes email"""
    try:
        with open('config.json', 'r') as f:
            enabled = json.load(f).get('email', {}).get('alerts_enabled', True)
        return jsonify({'enabled': bool(enabled)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/email-alerts/toggle', methods=['POST'])
@auth.login_required
@csrf.exempt
def email_alerts_toggle():
    """Active ou coupe l'envoi des alertes email (live, sans redemarrage)"""
    try:
        data = request.get_json()
        enable = bool(data.get('enable', True))
        with open('config.json', 'r') as f:
            config = json.load(f)
        config.setdefault('email', {})['alerts_enabled'] = enable
        with open('config.json', 'w') as f:
            json.dump(config, f, indent=2)
        # Application immediate en memoire (pas de redemarrage)
        if monitor and getattr(monitor, 'email_alert', None):
            monitor.email_alert.alerts_enabled = enable
        logger.info(f"Alertes email {'activees' if enable else 'coupees'}")
        return jsonify({'status': 'success', 'enabled': enable})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/mpx/spectrum')
@limiter.exempt
def mpx_spectrum():
    """Retourne le spectre FFT MPX pour affichage canvas"""
    if monitor and hasattr(monitor, 'mpx_analyzer') and monitor.mpx_analyzer:
        results = monitor.mpx_analyzer.get_results()
        spectrum = results.get('fft_spectrum', [])
        sample_rate = getattr(monitor.mpx_analyzer, 'sample_rate', 171000)
        fft_size = getattr(monitor.mpx_analyzer, '_fft_size', 2048)
        n_rfft = fft_size // 2 + 1
        decim_step = n_rfft // 512
        hz_per_bin = decim_step * sample_rate / fft_size
        return jsonify({
            'spectrum': spectrum,
            'sample_rate': int(sample_rate),
            'hz_per_bin': hz_per_bin,
            'fft_size': 512
        })
    return jsonify({'spectrum': [], 'sample_rate': 171000, 'fft_size': 512})


# =============================================
# PIGE AUDIO 24H
# =============================================
from pige import PigeStore, PigeError

pige_store = PigeStore()


@app.route('/pige')
@auth.login_required
def pige_page():
    """Page de la pige audio"""
    return render_template('pige.html')


@app.route('/api/pige/status')
@auth.login_required
@limiter.exempt
def pige_status():
    pige_store.reload()
    return jsonify(pige_store.status())


@app.route('/api/pige/segment/<name>')
@auth.login_required
@limiter.exempt
def pige_segment(name):
    """Sert un segment brut (écoute de contrôle, requêtes Range pour le seek)"""
    from flask import send_file, abort
    path = pige_store.segment_path(name)
    if not path:
        abort(404)
    return send_file(path, mimetype='audio/mpeg', conditional=True, max_age=0)


@app.route('/api/pige/export')
@auth.login_required
@limiter.exempt
def pige_export():
    """Exporte un créneau. ?start=&end= en secondes epoch. &check=1 : vérification seule."""
    from flask import send_file
    import threading
    try:
        start = float(request.args['start'])
        end = float(request.args['end'])
    except (KeyError, ValueError):
        return jsonify({'status': 'error', 'message': 'Paramètres start/end invalides'}), 400

    pige_store.reload()
    try:
        if request.args.get('check'):
            plan = pige_store.plan_export(start, end)
            return jsonify({'status': 'ok', 'duration': plan['duration'],
                            'recorded': plan['recorded'], 'gaps': plan['gaps']})

        station = 'radio'
        try:
            with open('config.json') as f:
                station = json.load(f).get('station', {}).get('name') or station
        except Exception:
            pass
        d0, d1 = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
        title = f"Pige {station} {d0:%d/%m/%Y %H:%M:%S} - {d1:%H:%M:%S}"
        safe = ''.join(c if c.isalnum() else '-' for c in station).strip('-').lower() or 'radio'
        download_name = f"pige_{safe}_{d0:%Y%m%d_%H%M%S}-{d1:%H%M%S}.mp3"

        path = pige_store.export(start, end, title=title)
    except PigeError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        logger.error(f"Erreur export pige : {e}")
        return jsonify({'status': 'error', 'message': 'Erreur interne pendant l\'export'}), 500

    logger.info(f"Export pige : {download_name}")

    def _cleanup(p):
        time.sleep(600)
        try:
            os.remove(p)
        except OSError:
            pass
    threading.Thread(target=_cleanup, args=(path,), daemon=True).start()

    return send_file(path, mimetype='audio/mpeg', as_attachment=True,
                     download_name=download_name)

# ══════════════════════════════════════════════════════════════════════════
#  Chaîne de diffusion (CHAIN_MONITOR_PATCH)
# ══════════════════════════════════════════════════════════════════════════
chain_monitor = None


def _chain_ready():
    if chain_monitor is None:
        return jsonify({'status': 'error', 'message': 'Supervision de la chaîne non démarrée'}), 503
    return None


@app.route('/chain')
@auth.login_required
def chain_page():
    # Page de supervision de la chaîne de diffusion
    return render_template('chain.html')


@app.route('/api/chain/status')
@auth.login_required
@limiter.exempt
def chain_status():
    err = _chain_ready()
    if err:
        return err
    from chain_probe import tools_available
    return jsonify({'devices': chain_monitor.get_status(), 'tools': tools_available()})


@app.route('/api/chain/devices', methods=['GET', 'POST'])
@auth.login_required
@limiter.exempt
def chain_devices():
    err = _chain_ready()
    if err:
        return err
    if request.method == 'GET':
        return jsonify({'devices': chain_monitor.list_devices()})
    try:
        new_id = chain_monitor.create_device(request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    logger.info(f"Chaîne : appareil {new_id} ajouté")
    return jsonify({'status': 'success', 'id': new_id})


@app.route('/api/chain/devices/<int:device_id>', methods=['GET', 'PUT', 'DELETE'])
@auth.login_required
@limiter.exempt
def chain_device(device_id):
    err = _chain_ready()
    if err:
        return err
    from chain_probe import public_device
    dev = chain_monitor.get_device(device_id)
    if not dev:
        return jsonify({'status': 'error', 'message': 'Appareil introuvable'}), 404
    if request.method == 'GET':
        return jsonify(public_device(dev))
    if request.method == 'DELETE':
        chain_monitor.delete_device(device_id)
        logger.info(f"Chaîne : appareil {device_id} ({dev['name']}) supprimé")
        return jsonify({'status': 'success'})
    try:
        chain_monitor.update_device(device_id, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    return jsonify({'status': 'success'})


@app.route('/api/chain/reorder', methods=['POST'])
@auth.login_required
@limiter.exempt
def chain_reorder():
    err = _chain_ready()
    if err:
        return err
    ids = (request.get_json(silent=True) or {}).get('ids') or []
    try:
        chain_monitor.reorder(ids)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Ordre invalide'}), 400
    return jsonify({'status': 'success'})


@app.route('/api/chain/test', methods=['POST'])
@auth.login_required
@limiter.exempt
def chain_test():
    err = _chain_ready()
    if err:
        return err
    try:
        return jsonify(chain_monitor.test(request.get_json(silent=True) or {}))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/chain/probe/<int:device_id>', methods=['POST'])
@auth.login_required
@limiter.exempt
def chain_probe_now(device_id):
    err = _chain_ready()
    if err:
        return err
    chain_monitor.trigger(device_id)
    return jsonify({'status': 'success'})


@app.route('/api/chain/events')
@auth.login_required
@limiter.exempt
def chain_events():
    err = _chain_ready()
    if err:
        return err
    limit = request.args.get('limit', 100, type=int)
    device_id = request.args.get('device_id', type=int)
    return jsonify({'events': chain_monitor.get_events(limit, device_id)})


# ── Événements externes (CHAIN_HOOK_PATCH) ───────────────────────────────
@app.route('/api/chain/hook', methods=['GET', 'POST'])
@csrf.exempt
@limiter.exempt
def chain_hook():
    # Reçoit un événement d'un équipement tiers (netwatch MikroTik...).
    # Pas de session : l'accès est protégé par le jeton.
    err = _chain_ready()
    if err:
        return err
    data = {}
    data.update(request.args.to_dict())
    data.update(request.form.to_dict())
    data.update(request.get_json(silent=True) or {})
    token = data.get('token') or request.headers.get('X-Chain-Token', '')
    if not chain_monitor.check_token(token):
        logger.warning(f"Chaîne : jeton invalide depuis {request.remote_addr}")
        return jsonify({'status': 'error', 'message': 'Jeton invalide'}), 403
    try:
        res = chain_monitor.record_external(data.get('source'), data.get('probe'), data.get('host'),
                                            data.get('state'), data.get('text', ''))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    return jsonify({'status': 'success', **res})


@app.route('/api/chain/hook/info')
@auth.login_required
def chain_hook_info():
    err = _chain_ready()
    if err:
        return err
    return jsonify({'path': '/api/chain/hook', 'token': chain_monitor.get_token()})


@app.route('/api/chain/hook/token', methods=['POST'])
@auth.login_required
def chain_hook_token():
    err = _chain_ready()
    if err:
        return err
    chain_monitor.regenerate_token()
    logger.info("Chaîne : jeton de remontée régénéré")
    return jsonify({'status': 'success'})


# ── Export des journaux de la chaîne (CHAIN_EXPORT_PATCH) ────────────────
@app.route('/api/chain/export')
@auth.login_required
def chain_export():
    err = _chain_ready()
    if err:
        return err
    from io import BytesIO
    from flask import send_file
    try:
        if request.args.get('start') and request.args.get('end'):
            start = datetime.fromisoformat(request.args['start'])
            end = datetime.fromisoformat(request.args['end'])
        else:
            hours = float(request.args.get('hours', 24))
            end = datetime.now()
            start = end - timedelta(hours=hours)
        data, counts = chain_monitor.export_zip(start, end)
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    logger.info(f"Export chaîne {start:%d/%m %H:%M} au {end:%d/%m %H:%M} : {counts}")
    name = f"blfmo-chaine_{start:%Y%m%d-%H%M}_{end:%Y%m%d-%H%M}.zip"
    return send_file(BytesIO(data), mimetype='application/zip', as_attachment=True, download_name=name)


# Démarrage du monitor (exécuté aussi bien par Gunicorn que par python app.py)
try:
    cleanup_orphan_records()
    start_cleanup_scheduler()
    monitor = FMMonitor('config.json')
    monitor.start()
except Exception as e:
    logger.error(f"Erreur démarrage monitor: {e}")

# Démarrage de la supervision de la chaîne (CHAIN_MONITOR_PATCH)
try:
    from chain_probe import ChainMonitor
    chain_monitor = ChainMonitor(
        'fm_monitor.db',
        fm_getter=lambda: monitor,
        alert_getter=lambda: getattr(monitor, 'email_alert', None),
    )
    chain_monitor.start()
except Exception as e:
    logger.error(f"Erreur démarrage supervision chaîne: {e}")


if __name__ == '__main__':
    try:
        # Détection automatique du SSL
        ssl_context = None
        cert_file = 'cert.pem'
        key_file = 'key.pem'

        if os.path.exists(cert_file) and os.path.exists(key_file):
            ssl_context = (cert_file, key_file)
            logger.info("✅ Certificats SSL détectés - HTTPS activé")
            logger.info(f"Démarrage du serveur Flask en HTTPS sur https://0.0.0.0:5000")
        else:
            logger.info("⚠️  Certificats SSL non trouvés - HTTP non sécurisé")
            logger.info("   Pour activer HTTPS, générez les certificats avec: ./generate_ssl.sh")
            logger.info(f"Démarrage du serveur Flask en HTTP sur http://0.0.0.0:5000")

        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        app.run(
            host='0.0.0.0',
            port=5000,
            debug=False,
            threaded=True,
            ssl_context=ssl_context
        )
    except KeyboardInterrupt:
        logger.info("Arrêt demandé par l'utilisateur")
        if monitor:
            monitor.stop()
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
        if monitor:
            monitor.stop()
