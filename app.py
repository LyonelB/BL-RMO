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
import re
import shutil
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from monitor import FMMonitor
from auth import Auth, AuthError, ROLE_LEVEL, ROLE_LABELS
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

from version import __version__ as APP_VERSION


@app.context_processor
def inject_app_version():
    return {'app_version': APP_VERSION}

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
    POLLING_ENDPOINTS = ('logs_search', 'logs_sources', 'chain_status', 'stream_stats', 'proxy_stream', 'static',
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

        _user = auth.verify_credentials(username, password)
        if _user:
            session['logged_in'] = True
            session['username'] = _user['username']
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
    """Ancienne page Statistiques : son contenu est dans Journaux, onglet Réception FM"""
    return redirect('/logs#reception')

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
    """Configuration, sans aucun secret. Complète pour un administrateur,
    réduite à l'affichage (station, fréquence) pour les autres (USERS_ROLES_PATCH)."""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Erreur lors de la lecture de la config: {e}")
        return jsonify({'error': 'Configuration illisible'}), 500
    user = auth.current_user()
    if user and user['role'] == 'admin':
        if config.get('email', {}).get('sender_password'):
            config['email']['sender_password'] = '********'
        if config.get('network', {}).get('wifi_password'):
            config['network']['wifi_password'] = '********'
        config['auth'] = {'username': user['username']}
        return jsonify(config)
    safe = {'station': config.get('station', {}),
            'rtl_sdr': {'frequency': config.get('rtl_sdr', {}).get('frequency')}}
    if user:
        safe['email'] = {'recipient_emails': config.get('email', {}).get('recipient_emails', [])}
    return jsonify(safe)

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

        # Tuner TEF : seuil d'absence de modulation (niveau audio, dBFS)
        if isinstance(data.get('tef'), dict) and 'modulation_threshold_dbfs' in data['tef']:
            thr = max(-90.0, min(-10.0, float(data['tef']['modulation_threshold_dbfs'])))
            config.setdefault('tef', {})['modulation_threshold_dbfs'] = thr
            if monitor:
                monitor.tef_modulation_threshold = thr

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

        # Identifiants : modifient le compte connecté (table users, USERS_ROLES_PATCH)
        if 'auth' in data:
            auth_data = data.get('auth') or {}
            _me = auth.current_user()
            _new_name = (auth_data.get('username') or '').strip() or None
            if _me and (_new_name or auth_data.get('password')):
                try:
                    auth.update_user(_me['id'], username=_new_name, password=auth_data.get('password') or None)
                except AuthError as e:
                    return jsonify({'status': 'error', 'message': str(e)}), 400
                if _new_name:
                    session['username'] = _new_name
                logger.info(f"Compte {_me['username']} : identifiants modifiés depuis Configuration")

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
        # valeurs tant que le service bl-rmo n'est pas redemarre.
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
        unit = request.args.get('unit', 'bl-rmo')
        if unit not in ('bl-rmo', 'bl-rmo-pige', 'bl-rmo-syslog', 'bl-rmo-dab'):
            return jsonify({'status': 'error', 'message': 'Service inconnu'}), 400
        # Ancien nom (avant migration vers BL-RMO) : son historique reste sous fm-monitor*
        old_unit = unit.replace('bl-rmo', 'fm-monitor', 1)
        try:
            lines = max(10, min(int(request.args.get('lines', 100)), 2000))
        except ValueError:
            lines = 100
        result = subprocess.run(
            ['journalctl', '-u', unit, '-u', old_unit, '-n', str(lines), '--no-pager', '-o', 'short-iso'],
            capture_output=True,
            text=True,
            timeout=15
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
                details="Ceci est un email de test depuis BL-RMO.",
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
            try:
                limit = max(1, min(int(request.args.get('limit', 50)), 500))
            except ValueError:
                limit = 50
            alerts = monitor.db.get_alerts_history_grouped(limit=limit)
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

@app.route('/api/rds/rt_history')
@limiter.exempt
@auth.login_required
def rt_history():
    """Derniers RadioText diffusés (RT_HISTORY_PATCH)"""
    try:
        limit = max(1, min(int(request.args.get('limit', 10)), 5000))
    except ValueError:
        limit = 10
    if monitor and hasattr(monitor, 'db'):
        return jsonify({'status': 'success', 'data': monitor.db.get_rt_history(limit=limit),
                        'current': monitor.stats.get('rt', '-')})
    return jsonify({'status': 'error', 'message': 'Database not available'}), 503

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
    pattern = os.path.join(RECORD_DIR, 'bl-rmo_*.mp3')
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
    record_filepath = os.path.join(RECORD_DIR, f'bl-rmo_{timestamp}.mp3')

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
        subprocess.Popen(['sudo', 'systemctl', 'restart', 'bl-rmo'])
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
from pige import PigeStore, PigeError, load_piges, pige_config, PIGE_ID_RE

pige_store = PigeStore()


@app.route('/pige')
@auth.login_required
def pige_page():
    """Page de la pige audio"""
    return render_template('pige.html')


def _pige(pid=None):
    """Magasin de la pige demandée (?p=), la FM par défaut. None si inconnue."""
    pid = (pid or request.args.get('p') or 'fm').lower()
    if pid == 'fm':
        pige_store.reload()
        return pige_store
    cfg = pige_config(pid)
    return PigeStore(cfg) if cfg else None


@app.route('/api/pige/status')
@auth.login_required
@limiter.exempt
def pige_status():
    store = _pige()
    if store is None:
        return jsonify({'status': 'error', 'message': 'Pige inconnue'}), 404
    try:
        seg_from = float(request.args['from']) if request.args.get('from') else None
        seg_to = float(request.args['to']) if request.args.get('to') else None
    except ValueError:
        seg_from = seg_to = None
    return jsonify(store.status(seg_from, seg_to))


@app.route('/api/pige/list')
@auth.login_required
@limiter.exempt
def pige_list():
    """Toutes les piges, avec leur état (sans la liste des segments)."""
    out = []
    for cfg in load_piges():
        st = pige_store if cfg['id'] == 'fm' else PigeStore(cfg)
        if cfg['id'] == 'fm':
            st.reload()
        segs = st.segments()
        newest = max((os.path.getmtime(x['path']) for x in segs[-1:]), default=0)
        out.append({'id': cfg['id'], 'name': cfg['name'], 'kind': cfg['kind'], 'sid': cfg.get('sid'),
                    'enabled': bool(cfg['enabled']), 'retention_hours': cfg['retention_hours'],
                    'bitrate_kbps': cfg['bitrate_kbps'], 'recording': time.time() - newest < 30,
                    'oldest': segs[0]['start'] if segs else None,
                    'used_bytes': sum(x['size'] for x in segs)})
    try:
        free = shutil.disk_usage(pige_store.dir).free
    except OSError:
        free = None
    return jsonify({'status': 'ok', 'piges': out, 'free_bytes': free})


@app.route('/api/pige/config', methods=['POST'])
@auth.login_required
def pige_config_save():
    """Créer, modifier ou supprimer une pige (administrateur).
    {action: create|update|delete, id, name, sid, retention_days, enabled}"""
    data = request.get_json(silent=True) or {}
    action = data.get('action')
    with open('config.json') as f:
        config = json.load(f)
    pige = config.setdefault('pige', {})
    extra = pige.setdefault('extra', [])
    pid = str(data.get('id') or '').strip().lower()
    try:
        days = float(data['retention_days']) if data.get('retention_days') not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Durée de conservation invalide'}), 400
    if days is not None and not (1 <= days <= 90):
        return jsonify({'status': 'error', 'message': 'Conservation : de 1 à 90 jours'}), 400
    name = str(data.get('name') or '').strip()[:60]
    cfg = None

    if action == 'create':
        sid = str(data.get('sid') or '').strip().lower()
        if not re.match(r'^[0-9a-fx]{1,8}$', sid):
            return jsonify({'status': 'error', 'message': 'Service DAB+ invalide'}), 400
        base = re.sub(r'[^a-z0-9]+', '-', (name or f'dab-{sid}').lower()).strip('-')[:24] or f'dab-{sid}'
        if base == 'fm':
            base = f'dab-{sid}'
        pid, n = base, 2
        while pid in {e.get('id') for e in extra}:
            pid, n = f'{base}-{n}', n + 1
        extra.append({'id': pid, 'name': name or f'DAB+ {sid}', 'sid': sid,
                      'retention_hours': int((days or 7) * 24), 'enabled': True})
    elif action == 'update':
        if pid == 'fm':
            if days is not None:
                pige['retention_hours'] = int(days * 24)
            if name:
                pige['name'] = name
        else:
            e = next((e for e in extra if e.get('id') == pid), None)
            if e is None:
                return jsonify({'status': 'error', 'message': 'Pige inconnue'}), 404
            if days is not None:
                e['retention_hours'] = int(days * 24)
            if name:
                e['name'] = name
            if 'enabled' in data:
                e['enabled'] = bool(data['enabled'])
    elif action == 'delete':
        if pid == 'fm':
            return jsonify({'status': 'error', 'message': 'La pige FM ne se supprime pas'}), 400
        cfg = pige_config(pid)
        if cfg is None:
            return jsonify({'status': 'error', 'message': 'Pige inconnue'}), 404
        pige['extra'] = [e for e in extra if e.get('id') != pid]
    else:
        return jsonify({'status': 'error', 'message': 'Action inconnue'}), 400

    tmp = 'config.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, 'config.json')
    if action == 'delete' and cfg and PIGE_ID_RE.match(pid):
        # Les enregistrements partent avec la pige (dossier toujours sous pige/extra/)
        target = os.path.realpath(cfg['directory'])
        root = os.path.realpath(os.path.join(pige_store.dir, 'extra'))
        if target.startswith(root + os.sep):
            shutil.rmtree(target, ignore_errors=True)
    logger.info(f"Pige {action} {pid} par {session.get('username', '?')}")
    return jsonify({'status': 'ok', 'id': pid})


@app.route('/api/pige/segment/<name>')
@auth.login_required
@limiter.exempt
def pige_segment(name):
    """Sert un segment brut (écoute de contrôle, requêtes Range pour le seek)"""
    from flask import send_file, abort
    store = _pige()
    path = store.segment_path(name) if store else None
    if not path:
        abort(404)
    return send_file(path, mimetype='audio/mpeg', conditional=True, max_age=0)


# ── Défauts audio (micro-coupures, clics, craquements) ───────────────

def _defect_store():
    if monitor is not None and getattr(monitor, 'defect_store', None):
        return monitor.defect_store
    from audio_defects import AudioDefectStore
    return AudioDefectStore()


@app.route('/api/audio-defects')
@auth.login_required
@limiter.exempt
def audio_defects_list():
    """Défauts audio relevés sur la réception FM, et décompte 24 h / 7 jours."""
    try:
        hours = max(1, min(int(request.args.get('hours', 24)), 24 * 30))
    except ValueError:
        hours = 24
    rf = request.args.get('rf', '1') != '0'
    local = request.args.get('local', '0') != '0'
    store = _defect_store()
    return jsonify({'status': 'success',
                    'active': bool(monitor is not None and getattr(monitor, 'defect_detector', None)),
                    'summary': store.summary(),
                    'data': store.events(hours=hours, include_suspect=rf, include_local=local)})


@app.route('/api/audio-defects/export')
@auth.login_required
def audio_defects_export():
    """ZIP pour analyse : CSV des défauts, réglages du détecteur, extraits WAV."""
    import tempfile
    from flask import send_file
    try:
        hours = max(1, min(int(request.args.get('hours', 24)), 24 * 30))
    except ValueError:
        hours = 24
    with_clips = request.args.get('clips', '1') != '0'
    params = (monitor.config.get('audio_defects', {}) if monitor is not None else {}) or {}
    ctx = {}
    if monitor is not None:
        ctx = {'frequence': monitor.rtl_config.get('frequency'),
               'seuil_perte_signal_dbf': monitor.tef_config.get('signal_threshold_dbf'),
               'detection_active': bool(getattr(monitor, 'defect_detector', None))}
        det = getattr(monitor, 'defect_detector', None)
        if det:
            ctx['compteurs_depuis_demarrage'] = dict(det.counters)
    fd, path = tempfile.mkstemp(prefix='blrmo-defauts-', suffix='.zip')
    os.close(fd)
    try:
        n, nc = _defect_store().export_zip(path, hours=hours, with_clips=with_clips, params=params, context=ctx)
    except Exception as e:
        os.unlink(path)
        return jsonify({'status': 'error', 'message': str(e)}), 500
    logger.info(f"Export défauts audio : {hours} h, {n} défauts, {nc} extraits")
    end = datetime.now()
    name = f"blrmo-defauts-audio_{end - timedelta(hours=hours):%Y%m%d-%H%M}_{end:%Y%m%d-%H%M}.zip"
    # Fichier ouvert puis effacé : Linux le garde lisible jusqu'à la fin de l'envoi
    f = open(path, 'rb')
    os.unlink(path)
    return send_file(f, mimetype='application/zip', as_attachment=True, download_name=name, max_age=0)


@app.route('/api/audio-defects/<int:event_id>/clip')
@auth.login_required
@limiter.exempt
def audio_defect_clip(event_id):
    """Extrait WAV du signal brut du tuner autour du défaut."""
    from flask import send_file, abort
    path = _defect_store().clip_path(event_id)
    if not path:
        abort(404)
    return send_file(path, mimetype='audio/wav', conditional=True, max_age=0,
                     download_name=f'blrmo-defaut-{event_id}.wav')


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

    store = _pige()
    if store is None:
        return jsonify({'status': 'error', 'message': 'Pige inconnue'}), 404
    try:
        if request.args.get('check'):
            plan = store.plan_export(start, end)
            return jsonify({'status': 'ok', 'duration': plan['duration'],
                            'recorded': plan['recorded'], 'gaps': plan['gaps']})

        station = 'radio'
        try:
            with open('config.json') as f:
                station = json.load(f).get('station', {}).get('name') or station
        except Exception:
            pass
        if store.cfg.get('kind') == 'dab':
            station = store.cfg.get('name') or station
        d0, d1 = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
        title = f"Pige {station} {d0:%d/%m/%Y %H:%M:%S} - {d1:%H:%M:%S}"
        safe = ''.join(c if c.isalnum() else '-' for c in station).strip('-').lower() or 'radio'
        download_name = f"pige_{safe}_{d0:%Y%m%d_%H%M%S}-{d1:%H%M%S}.mp3"

        path = store.export(start, end, title=title)
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
#  Extraits des dernières alertes (PIGE_ARCHIVE_PATCH)
# ══════════════════════════════════════════════════════════════════════════
pige_archiver = None


@app.route('/api/pige/archives')
@auth.login_required
@limiter.exempt
def pige_archives():
    if pige_archiver is None:
        return jsonify({'status': 'ok', 'keep': 0, 'archives': []})
    return jsonify({'status': 'ok', 'keep': pige_archiver.keep, 'archives': pige_archiver.list()})


@app.route('/api/pige/archives/<int:archive_id>/audio')
@auth.login_required
@limiter.exempt
def pige_archive_audio(archive_id):
    from flask import send_file, abort
    path = pige_archiver.path(archive_id) if pige_archiver else None
    if not path:
        abort(404)
    return send_file(path, mimetype='audio/mpeg', conditional=True, max_age=0,
                     as_attachment=bool(request.args.get('dl')), download_name=os.path.basename(path))


# ══════════════════════════════════════════════════════════════════════════
#  Réception DAB+ (DAB_PATCH) : service bl-rmo-dab + lecture ici
# ══════════════════════════════════════════════════════════════════════════
from dab_monitor import DabReader, BAND_III, sid_decimal
import threading as _dab_threading

dab_reader = DabReader()
# Une seule écoute à la fois (welle-cli et la clé) : la plus récente remplace
# la précédente. Refuser la nouvelle tant que l'ancienne n'était pas refermée
# donnait « Écoute impossible » en changeant de radio : le serveur met
# quelques secondes à voir que le navigateur a coupé l'ancien flux.
_dab_listen_lock = _dab_threading.Lock()
_dab_listen_cur = {'gen': 0, 'up': None}


@app.context_processor
def inject_dab():
    try:
        return {'dab_enabled': bool(dab_reader.config().get('enabled'))}
    except Exception:
        return {'dab_enabled': False}


@app.route('/dab')
@auth.login_required
def dab_page():
    return render_template('dab.html')


@app.route('/api/dab/status')
@auth.login_required
@limiter.exempt
def dab_status():
    return jsonify({'status': 'ok', **dab_reader.status()})


@app.route('/api/dab/events')
@auth.login_required
@limiter.exempt
def dab_events():
    limit = max(1, min(int(request.args.get('limit', 50) or 50), 500))
    return jsonify({'status': 'ok', 'events': dab_reader.events(limit)})


@app.route('/api/dab/uptime')
@auth.login_required
@limiter.exempt
def dab_uptime():
    hours = max(1, min(int(request.args.get('hours', 24) or 24), 24 * 30))
    return jsonify({'status': 'ok', 'hours': hours, 'services': dab_reader.uptime(hours)})


def _dab_welle(path, **kw):
    import requests as _rq
    cfg = dab_reader.config()
    return _rq.get(f"http://127.0.0.1:{int(cfg['port'])}{path}", **kw)


@app.route('/api/dab/slide/<sid>')
@auth.login_required
@limiter.exempt
def dab_slide(sid):
    from flask import abort
    if not sid.replace('x', '').isalnum():
        abort(404)
    try:
        r = _dab_welle(f'/slide/{sid_decimal(sid)}', timeout=3)
        if r.status_code == 200 and r.content:
            return app.response_class(r.content, mimetype=r.headers.get('Content-Type', 'image/jpeg'),
                                      headers={'Cache-Control': 'max-age=30'})
    except Exception:
        pass
    abort(404)


@app.route('/api/dab/listen/<sid>')
@auth.login_required
@limiter.exempt
def dab_listen(sid):
    """Écoute d'un service, servie par welle-cli. Une écoute à la fois, durée limitée."""
    import requests as _rq
    if not sid.replace('x', '').isalnum():
        return jsonify({'status': 'error', 'message': 'Service inconnu'}), 404
    cfg = dab_reader.config()
    if not cfg.get('enabled'):
        return jsonify({'status': 'error', 'message': 'Réception DAB+ désactivée'}), 503
    with _dab_listen_lock:                     # couper l'écoute en cours, s'il y en a une
        _dab_listen_cur['gen'] += 1
        gen = _dab_listen_cur['gen']
        old, _dab_listen_cur['up'] = _dab_listen_cur['up'], None
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    base = f"http://127.0.0.1:{int(cfg['port'])}"
    try:
        # Fork welle-monitor : décoder ce service tout de suite, sans attendre le carrousel
        _rq.post(f"{base}/carousel/pin/{sid}", timeout=2)
    except Exception:
        pass
    up = None
    for attempt in range(3):                   # le service épinglé peut mettre un instant à démarrer
        if _dab_listen_cur['gen'] != gen:
            return jsonify({'status': 'error', 'message': 'Remplacée par une autre écoute'}), 409
        try:
            up = _rq.get(f"{base}/mp3/{sid}", stream=True, timeout=(5, 30))
            if up.status_code == 200:
                break
            up.close()
            up = None
        except Exception:
            up = None
        time.sleep(1.5)
    if up is None:
        return jsonify({'status': 'error', 'message': 'Service non disponible'}), 503
    with _dab_listen_lock:
        if _dab_listen_cur['gen'] != gen:      # une autre écoute est arrivée entre-temps
            up.close()
            return jsonify({'status': 'error', 'message': 'Remplacée par une autre écoute'}), 409
        _dab_listen_cur['up'] = up
    deadline = time.time() + 60 * float(cfg.get('listen_max_minutes', 15))

    def generate():
        try:
            for chunk in up.iter_content(chunk_size=4096):
                if _dab_listen_cur['gen'] != gen or time.time() > deadline:
                    break
                if chunk:
                    yield chunk
        except Exception:
            pass
        finally:
            up.close()
            with _dab_listen_lock:
                if _dab_listen_cur['up'] is up:
                    _dab_listen_cur['up'] = None

    return app.response_class(generate(), mimetype='audio/mpeg',
                              headers={'Cache-Control': 'no-cache, no-store', 'X-Accel-Buffering': 'no'})


DAB_EDITABLE = {
    'enabled': bool, 'channel': str, 'ensemble_name': str, 'gain': int, 'carousel_size': int,
    'snr_threshold': float, 'fic_quality_threshold': float, 'silence_threshold': float,
    'silence_duration': int, 'service_loss_duration': int, 'ensemble_loss_duration': int,
    'receiver_loss_duration': int, 'listen_max_minutes': int,
    'dashboard_sid': lambda v: str(v or '').strip().lower(),
    # Mails par type d'alerte DAB+ : récepteur, ensemble, service absent, silence
    'email_kinds': lambda v: {k: bool(v.get(k, True)) for k in ('receiver', 'ensemble', 'absent', 'silence')},
}


@app.route('/api/dab/settings', methods=['GET', 'POST'])
@auth.login_required
def dab_settings():
    if request.method == 'GET':
        cfg = dab_reader.config()
        return jsonify({'status': 'ok', 'settings': {k: cfg.get(k) for k in list(DAB_EDITABLE) + ['watched_sids', 'frequency_mhz']},
                        'channels': list(BAND_III)})
    data = request.get_json(silent=True) or {}
    with open('config.json') as f:
        config = json.load(f)
    dab = config.setdefault('dab', {})
    try:
        for k, typ in DAB_EDITABLE.items():
            if k in data:
                dab[k] = typ(data[k])
        if 'watched_sids' in data:
            dab['watched_sids'] = [str(x).lower() for x in (data['watched_sids'] or []) if str(x).strip()]
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Valeur invalide'}), 400
    if str(dab.get('channel', '9A')).upper() not in BAND_III:
        return jsonify({'status': 'error', 'message': 'Canal inconnu (bande III : 5A à 13F)'}), 400
    dab['channel'] = str(dab.get('channel', '9A')).upper()
    dab['frequency_mhz'] = BAND_III[dab['channel']] / 1000
    tmp = 'config.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, 'config.json')
    logger.info(f"Réglages DAB+ enregistrés par {session.get('username', '?')}")
    return jsonify({'status': 'ok', 'settings': dab})


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
    name = f"blrmo-chaine_{start:%Y%m%d-%H%M}_{end:%Y%m%d-%H%M}.zip"
    return send_file(BytesIO(data), mimetype='application/zip', as_attachment=True, download_name=name)


# ══════════════════════════════════════════════════════════════════════════
#  Journaux des appareils, réception syslog (SYSLOG_PATCH)
# ══════════════════════════════════════════════════════════════════════════
from syslog_server import SyslogStore, SEVERITIES, FACILITIES

syslog_store = None


def _syslog():
    global syslog_store
    if syslog_store is None:
        syslog_store = SyslogStore()
    return syslog_store


syslog_cleaner = None


def _syslog_cleaner():
    """Nettoyage et règles de masquage des journaux (SYSLOG_CLEAN_PATCH)."""
    global syslog_cleaner
    if syslog_cleaner is None:
        from syslog_clean import Cleaner
        syslog_cleaner = Cleaner(_syslog().db_path)
    return syslog_cleaner


@app.route('/api/syslog/rules', methods=['GET', 'POST'])
@auth.login_required
@limiter.exempt
def logs_rules():
    cl = _syslog_cleaner()
    if request.method == 'GET':
        return jsonify({'rules': cl.rules()})
    d = request.get_json(silent=True) or {}
    if not d.get('message') and not d.get('program'):
        return jsonify({'status': 'error', 'message': 'Message manquant'}), 400
    rid = cl.add_rule(d.get('src'), d.get('program'), d.get('message', ''), d.get('label', ''),
                      session.get('username', ''))
    logger.info(f"Journaux : règle de masquage ajoutée par {session.get('username')} : {d.get('label', '')[:80]}")
    return jsonify({'status': 'success', 'id': rid})


@app.route('/api/syslog/rules/<rule_id>', methods=['PUT', 'DELETE'])
@auth.login_required
def logs_rule(rule_id):
    cl = _syslog_cleaner()
    if request.method == 'DELETE':
        if not cl.delete_rule(rule_id):
            return jsonify({'status': 'error', 'message': 'Règle introuvable ou intégrée (désactivez-la plutôt)'}), 400
        return jsonify({'status': 'success'})
    d = request.get_json(silent=True) or {}
    if not cl.set_enabled(rule_id, bool(d.get('enabled'))):
        return jsonify({'status': 'error', 'message': 'Règle introuvable'}), 404
    return jsonify({'status': 'success'})


def _syslog_names():
    """Adresse IP → nom de l'appareil dans la chaîne (les appareils avant les sondes)."""
    names = {}
    try:
        devs = sorted(chain_monitor.list_devices(), key=lambda d: d.get('kind') == 'probe')
        for d in devs:
            h = chain_monitor._device_host(d)
            if h and h not in names:
                names[h] = d['name']
    except Exception:
        pass
    return names


def _syslog_filters():
    a = request.args
    sev = a.get('sev', '')
    return {
        'src': [s for s in a.get('src', '').split(',') if s] or None,
        'max_severity': int(sev) if sev.isdigit() else None,
        'q': a.get('q', '').strip() or None,
        'program': a.get('program', '').strip() or None,
        'start': a.get('start') or None,
        'end': a.get('end') or None,
    }


@app.route('/logs')
@auth.login_required
def logs_page():
    return render_template('logs.html')


@app.route('/api/syslog')
@auth.login_required
@limiter.exempt
def logs_search():
    try:   # SYSLOG_CLEAN_PATCH : messages nettoyés, messages connus masqués
        rows, total, hidden, capped = _syslog_cleaner().search(
            _syslog(), hide=request.args.get('hidden') != '1', limit=request.args.get('limit', 200),
            offset=request.args.get('offset', 0), **_syslog_filters())
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Paramètres invalides'}), 400
    return jsonify({'rows': rows, 'total': total, 'hidden': hidden, 'capped': capped})


@app.route('/api/syslog/sources')
@auth.login_required
@limiter.exempt
def logs_sources():
    st = _syslog()
    return jsonify({'sources': st.sources(), 'names': _syslog_names(), 'meta': st.meta(),
                    'now': datetime.now().isoformat(timespec='seconds'),
                    'severities': SEVERITIES, 'facilities': FACILITIES})


@app.route('/api/syslog/export')
@auth.login_required
@limiter.exempt
def logs_export():
    import csv
    import io
    f = _syslog_filters()
    hide = request.args.get('hidden') != '1'
    names = _syslog_names()
    st = _syslog()

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=';', lineterminator='\n')
        w.writerow(['date', 'heure', 'niveau', 'appareil', 'adresse', 'nom_hote', 'categorie', 'programme', 'message'])
        yield '﻿' + buf.getvalue()
        n = 0
        for r in _syslog_cleaner().iter_rows(st, hide=hide, **f):
            buf.seek(0)
            buf.truncate()
            w.writerow([r['ts'][:10], r['ts'][11:19], SEVERITIES[r['severity']] if 0 <= (r['severity'] or 0) < 8 else r['severity'],
                        names.get(r['src'], r['host'] or r['src']), r['src'], r['host'],
                        FACILITIES[r['facility']] if 0 <= (r['facility'] or 0) < len(FACILITIES) else r['facility'],
                        r['program'], r['message']])
            yield buf.getvalue()
            n += 1
            if n >= 500000:
                break

    fname = f"blrmo-journaux_{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(generate(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


# ── Synoptique de la chaîne (CHAIN_SYNOPTIC_PATCH) ───────────────────────
@app.route('/chain/manage')
@auth.login_required
def chain_manage_page():
    # Ajout, ordre et réglages des appareils et des sondes
    return render_template('chain_manage.html')


@app.route('/api/chain/devices/<int:device_id>/history')
@auth.login_required
@limiter.exempt
def chain_device_history(device_id):
    err = _chain_ready()
    if err:
        return err
    labels = chain_monitor.numeric_labels(device_id)
    metric = request.args.get('metric') or (labels[0] if labels else '')
    hours = request.args.get('hours', 24, type=float)
    points = chain_monitor.metric_history(device_id, metric, hours) if metric else []
    return jsonify({'labels': labels, 'metric': metric, 'hours': hours, 'points': points})


@app.route('/api/chain/devices/<int:device_id>/logs')
@auth.login_required
@limiter.exempt
def chain_device_logs(device_id):
    err = _chain_ready()
    if err:
        return err
    return jsonify(chain_monitor.device_logs(device_id, request.args.get('limit', 50, type=int)))


# ══════════════════════════════════════════════════════════════════════════
#  Comptes utilisateurs et rôles (USERS_ROLES_PATCH)
# ══════════════════════════════════════════════════════════════════════════
from flask import g

# Routes ouvertes à tous (avec ou sans session) : la route gère elle-même l'accès
# ══════════════════════════════════════════════════════════════════════════
#  Tableau de bord : résumé FM, DAB+, chaîne et pige (DASHBOARD_PATCH)
#  Public comme /public : ni adresse, ni réglage, ni identifiant.
# ══════════════════════════════════════════════════════════════════════════
def _dash_fm(now):
    out = {'last_alert': None, 'alerts_24h': 0}
    if monitor is None or not hasattr(monitor, 'db'):
        return out
    try:
        grouped = monitor.db.get_alerts_history_grouped(limit=100)
    except Exception:
        grouped = []
    if grouped:
        a = grouped[0]
        out['last_alert'] = {'label': a.get('alert_label'), 'start': a.get('start_time'),
                             'end': a.get('end_time'), 'duration': a.get('duration'), 'status': a.get('status')}
    since = (now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    out['alerts_24h'] = sum(1 for a in grouped if str(a.get('start_time') or '') >= since)
    return out


def _dash_dab(now):
    cfg = dab_reader.config()
    out = {'enabled': bool(cfg.get('enabled')), 'service': None, 'last_alert': None, 'alerts_24h': 0}
    if not out['enabled']:
        return out
    st = dab_reader.status()
    rf = st.get('rf') or {}
    out.update({'receiver_up': bool(st.get('receiver_up')), 'ensemble_ok': bool(st.get('ensemble_ok')),
                'channel': st.get('channel'), 'ensemble': rf.get('ensemble_label') or cfg.get('ensemble_name', ''),
                'snr': rf.get('snr'), 'fic': rf.get('fic_quality')})
    services = st.get('services') or []
    sid = (cfg.get('dashboard_sid') or '').lower()
    if not sid:
        watched = cfg.get('watched_sids') or []
        sid = watched[0] if watched else ''
    svc = next((x for x in services if x.get('sid') == sid), None)
    out['selected_sid'] = sid
    if svc:
        keep = ('sid', 'label', 'dls', 'pty', 'mode', 'bitrate', 'channels', 'samplerate', 'present',
                'silence', 'audio_l', 'audio_r', 'level_age', 'mot_time')
        out['service'] = {k: svc.get(k) for k in keep}
        try:
            up = next((u for u in dab_reader.uptime(24) if u['sid'] == sid), None)
            out['service']['uptime_pct'] = up['uptime_pct'] if up else None
        except Exception:
            out['service']['uptime_pct'] = None
    try:
        evs = [e for e in dab_reader.events(limit=100) if not e.get('sid') or e.get('sid') == sid]
    except Exception:
        evs = []
    if evs:
        e = evs[0]
        out['last_alert'] = {'label': e['label'] + (f" : {e['service']}" if e.get('service') else ''),
                             'start': e['start'], 'end': e['end'], 'duration': e['duration'], 'status': e['status']}
    since = (now - timedelta(hours=24)).isoformat()
    out['alerts_24h'] = sum(1 for e in evs if str(e.get('start') or '') >= since)
    return out


def _dash_chain(now):
    if chain_monitor is None:
        return {'available': False}
    devs = chain_monitor.get_status()

    def brief(d):
        val = ''
        if d['kind'] == 'sensor':
            # Capteur de site : ses mesures chiffrées (ex. « 19,9 °C · 43 %RH »)
            nums = [f"{m['display']} {m['unit']}".strip() for m in d.get('metrics') or []
                    if m.get('unit') and isinstance(m.get('value'), (int, float)) and not isinstance(m.get('value'), bool)]
            val = ' · '.join(nums[:2])
        for m in [] if val else d.get('metrics') or []:
            if m.get('display') not in (None, ''):
                val = f"{m.get('label', '')} {m['display']}{(' ' + m['unit']) if m.get('unit') else ''}".strip()
                break
        if not val and d.get('latency_ms') is not None:
            val = f"{d['latency_ms']:.0f} ms"
        return {'id': d['id'], 'kind': d['kind'], 'name': d['name'], 'site': d.get('site', ''),
                'role': d.get('role', ''), 'probe': d.get('probe', ''), 'state': d['state'], 'value': val}

    chain = [d for d in devs if d['kind'] not in ('probe', 'sensor')]
    probes = [d for d in devs if d['kind'] == 'probe']
    sensors = [d for d in devs if d['kind'] == 'sensor']
    used, items = set(), []
    for i, d in enumerate(chain):
        items.append(brief(d))
        if i + 1 >= len(chain) or chain[i + 1].get('site', '') != d.get('site', ''):
            for c in sensors:   # capteurs de site après le dernier appareil de leur site
                if c['id'] not in used and c.get('site', '') == d.get('site', ''):
                    items.append(brief(c))
                    used.add(c['id'])
        if i + 1 < len(chain):
            nxt = chain[i + 1]
            for p in probes:   # sondes posées sur la liaison entre deux appareils voisins
                if p['id'] not in used and {p.get('link_from'), p.get('link_to')} == {d['id'], nxt['id']}:
                    items.append(brief(p))
                    used.add(p['id'])
    items += [brief(c) for c in sensors if c['id'] not in used]
    items += [brief(p) for p in probes if p['id'] not in used]
    active = [x for x in items if x['state'] != 'disabled']
    last = None
    since = (now - timedelta(hours=24)).isoformat(sep=' ')
    alerts = 0
    try:
        evs = chain_monitor.get_events(200)
        evs = [e for e in evs if not (e.get('from_state') == 'unknown' and e.get('to_state') == 'up')]
        if evs:
            e = evs[0]
            last = {'timestamp': e.get('timestamp'), 'device': e.get('device_name') or '',
                    'to_state': e.get('to_state'), 'message': e.get('message') or ''}
        alerts = sum(1 for e in evs if e.get('to_state') in ('down', 'warn')
                     and str(e.get('timestamp') or '').replace('T', ' ') >= since)
    except Exception:
        pass
    return {'available': True, 'items': items, 'total': len(active),
            'ok': sum(1 for x in active if x['state'] == 'up'),
            'bad': sum(1 for x in active if x['state'] in ('down', 'warn', 'unstable')),
            'last_event': last, 'alerts_24h': alerts}


def _dash_pige():
    try:
        pige_store.reload()
        ps = pige_store.status(seg_from=time.time())
    except Exception:
        return {'available': False}
    return {'available': True, 'enabled': ps['enabled'], 'recording': ps['recording'],
            'retention_hours': ps['retention_hours'], 'oldest': ps.get('oldest'),
            'used_bytes': ps['used_bytes'], 'free_bytes': ps['free_bytes'], 'now': ps['now']}


@app.route('/api/dashboard')
@limiter.exempt
def dashboard_data():
    """Résumé pour le tableau de bord (rafraîchi toutes les 5 s)."""
    now = datetime.now()
    data = {'fm': _dash_fm(now), 'dab': _dash_dab(now), 'chain': _dash_chain(now), 'pige': _dash_pige()}
    data['alerts_24h'] = data['fm']['alerts_24h'] + data['dab'].get('alerts_24h', 0) \
        + data['chain'].get('alerts_24h', 0)
    return jsonify(data)


ROLE_ALWAYS = {'login', 'logout', 'get_csrf_token', 'public_dashboard', 'static', 'chain_hook', 'dashboard_data'}
# Lecteur : consulter et écouter
ROLE_LECTEUR = {
    'index', 'stats', 'about_page', 'pige_page', 'chain_page', 'logs_page', 'account_page',
    'get_stats', 'stream_stats', 'get_config_full', 'get_logs', 'get_signal_history', 'get_audio_history',
    'get_alerts_history', 'get_alerts_history_grouped', 'get_volume', 'proxy_stream', 'record_status',
    'wifi_status', 'email_alerts_status', 'mpx_spectrum', 'rt_history',
    'pige_status', 'pige_segment', 'pige_archives', 'pige_archive_audio', 'pige_list',
    'chain_status', 'chain_events', 'chain_device_history', 'chain_device_logs',
    'logs_search', 'logs_sources', 'account_password',
    'dab_page', 'dab_status', 'dab_events', 'dab_uptime', 'dab_slide', 'dab_listen',
    'audio_defects_list', 'audio_defect_clip',
}
ROLE_LECTEUR_GET = {'chain_devices', 'chain_device', 'logs_rules', 'dab_settings'}      # lecture seule pour ces routes
# Opérateur : exporter, enregistrer, intervenir sans rien régler
ROLE_OPERATEUR = {
    'pige_export', 'chain_export', 'logs_export', 'record_start', 'record_stop', 'record_download',
    'email_alerts_toggle', 'chain_probe_now', 'set_volume', 'read_rds_ps', 'read_rds_rt',
    'audio_defects_export',
}
# Tout le reste (Configuration, Gérer la chaîne, comptes...) : administrateur


def _required_role(endpoint):
    if endpoint == 'pige_archive_audio' and request.args.get('dl'):
        return 'operateur'                        # télécharger un extrait = exporter
    if endpoint in ROLE_LECTEUR or (endpoint in ROLE_LECTEUR_GET and request.method == 'GET'):
        return 'lecteur'
    if endpoint in ROLE_OPERATEUR:
        return 'operateur'
    return 'admin'


@app.before_request
def enforce_role():
    ep = request.endpoint
    if not ep or ep in ROLE_ALWAYS or not session.get('logged_in'):
        return
    user = auth.current_user()
    if not user:                                  # compte supprimé ou désactivé entre-temps
        session.clear()
        if request.path.startswith('/api/') or request.is_json:
            return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        return redirect(url_for('login'))
    g.user = user
    need = _required_role(ep)
    if ROLE_LEVEL[user['role']] >= ROLE_LEVEL[need]:
        return
    logger.warning(f"Accès refusé : {user['username']} ({user['role']}) sur {request.method} {request.path}")
    msg = f"Accès réservé au rôle {ROLE_LABELS[need]} ou supérieur"
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'status': 'error', 'message': msg}), 403
    return (f'<!doctype html><meta charset="utf-8"><title>Accès refusé</title>'
            f'<body style="font-family:system-ui;padding:40px;color:#334155"><h2>Accès refusé</h2>'
            f'<p>{msg}. Vous êtes connecté en tant que <b>{user["username"]}</b> ({user["role_label"]}).</p>'
            f'<p><a href="/">Retour au tableau de bord</a></p></body>', 403)


@app.context_processor
def inject_current_user():
    user = g.get('user') if session.get('logged_in') else None
    if user is None and session.get('logged_in'):
        user = auth.current_user()
    return {'current_user': user}


@app.route('/account')
@auth.login_required
def account_page():
    return render_template('account.html')


@app.route('/api/account/password', methods=['POST'])
@auth.login_required
@limiter.limit("10 per minute")
def account_password():
    data = request.get_json(silent=True) or {}
    try:
        auth.change_own_password(session.get('username'), data.get('current'), data.get('new'))
    except AuthError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    logger.info(f"Compte {session.get('username')} : mot de passe changé")
    return jsonify({'status': 'success'})


@app.route('/api/users', methods=['GET', 'POST'])
@auth.login_required
def users_api():
    if request.method == 'GET':
        return jsonify({'status': 'ok', 'users': auth.list_users()})
    d = request.get_json(silent=True) or {}
    try:
        uid = auth.create_user(d.get('username'), d.get('password'), d.get('role', 'lecteur'), d.get('full_name', ''))
    except AuthError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    logger.info(f"Compte créé par {session.get('username')} : {d.get('username')} ({d.get('role')})")
    return jsonify({'status': 'success', 'id': uid})


@app.route('/api/users/<int:user_id>', methods=['PUT', 'DELETE'])
@auth.login_required
def user_api(user_id):
    me = g.user
    if request.method == 'DELETE':
        if user_id == me['id']:
            return jsonify({'status': 'error', 'message': 'Vous ne pouvez pas supprimer votre propre compte'}), 400
        try:
            name = auth.delete_user(user_id)
        except AuthError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400
        logger.info(f"Compte supprimé par {me['username']} : {name}")
        return jsonify({'status': 'success'})
    d = request.get_json(silent=True) or {}
    if user_id == me['id'] and (('role' in d and d['role'] != me['role']) or d.get('enabled') is False):
        return jsonify({'status': 'error', 'message': 'Un autre administrateur doit modifier votre rôle ou votre accès'}), 400
    try:
        u = auth.update_user(user_id, username=d.get('username'), role=d.get('role'),
                             enabled=d.get('enabled'), password=d.get('password') or None,
                             full_name=d.get('full_name'))
    except AuthError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    changes = ', '.join(k if k != 'password' else 'mot de passe' for k in d)
    logger.info(f"Compte {u['username']} modifié par {me['username']} : {changes}")
    return jsonify({'status': 'success', 'user': u})


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


# Extraits des dernières alertes (PIGE_ARCHIVE_PATCH)
try:
    from pige_archive import PigeArchiver
    pige_archiver = PigeArchiver(pige_store, 'fm_monitor.db', pige_store.cfg)
    pige_archiver.start()
except Exception as e:
    logger.error(f"Erreur démarrage archivage des extraits d'alertes: {e}")


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
