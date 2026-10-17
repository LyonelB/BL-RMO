#!/usr/bin/env python3
"""
Patch BL-FMO : comptes utilisateurs et rôles (Lecteur, Opérateur, Administrateur)

À copier avec : auth.py (nouvelle version), templates/account.html, templates/_roles.html

- app.py :
    * contrôle d'accès central (before_request) : chaque route a un rôle minimal,
      toute route non listée est réservée aux administrateurs ;
    * /api/config/full ne renvoie plus de secrets (hash du mot de passe, mots de
      passe mail et Wi-Fi) et seulement le strict nécessaire aux non-admins ;
    * page /account (« Utilisateurs » pour l'admin, « Mon compte » sinon) et API
      /api/users, /api/account/password ;
    * la section « Compte » de Configuration modifie le compte connecté.
- templates/*.html : inclusion de _roles.html (masque ce que le rôle ne permet pas,
  ajoute le lien Utilisateurs / Mon compte et le nom de la personne connectée).

Idempotent, copie .bak avant écriture, validation AST de app.py.
Usage : cd ~/fm-monitor && python3 patch_users.py
"""
import ast
import os
import shutil
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
MARK = 'USERS_ROLES_PATCH'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')
TEMPLATES = ('index', 'stats', 'pige', 'chain', 'chain_manage', 'logs', 'about', 'config', 'setup')


def rd(rel):
    with open(os.path.join(BASE, rel), encoding='utf-8') as f:
        return f.read()


def wr(rel, txt):
    p = os.path.join(BASE, rel)
    shutil.copy2(p, f'{p}.{STAMP}.bak')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(txt)
    print(f'  ✓ {rel}')


def replace(s, old, new, rel):
    if old not in s:
        sys.exit(f'✗ {rel} : repère introuvable : {old.strip()[:70]!r}')
    return s.replace(old, new, 1)


def replace_between(s, start, end, new, rel):
    i = s.find(start)
    j = s.find(end, i)
    if i < 0 or j < 0:
        sys.exit(f'✗ {rel} : bloc introuvable : {start.strip()[:70]!r}')
    return s[:i] + new + s[j:]


# ── app.py : connexion ────────────────────────────────────────────────────
LOGIN_OLD = '''        if auth.verify_credentials(username, password):
            session['logged_in'] = True
            session['username'] = username
'''
LOGIN_NEW = '''        _user = auth.verify_credentials(username, password)
        if _user:
            session['logged_in'] = True
            session['username'] = _user['username']
'''

# ── app.py : section « Compte » de Configuration ──────────────────────────
SAVE_AUTH_START = '        # Configuration des identifiants de connexion (avec Bcrypt)\n'
SAVE_AUTH_END = '        # Configuration réseau (nouveau)\n'
SAVE_AUTH_NEW = '''        # Identifiants : modifient le compte connecté (table users, USERS_ROLES_PATCH)
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

'''

# ── app.py : /api/config/full sans secrets ────────────────────────────────
CONFIG_START = "@app.route('/api/config/full')\n"
CONFIG_END = "@app.route('/api/config/save', methods=['POST'])\n"
CONFIG_NEW = '''@app.route('/api/config/full')
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

'''

# ── app.py : contrôle d'accès, page et API des comptes ────────────────────
ACCESS = '''
# ══════════════════════════════════════════════════════════════════════════
#  Comptes utilisateurs et rôles (USERS_ROLES_PATCH)
# ══════════════════════════════════════════════════════════════════════════
from flask import g

# Routes ouvertes à tous (avec ou sans session) : la route gère elle-même l'accès
ROLE_ALWAYS = {'login', 'logout', 'get_csrf_token', 'public_dashboard', 'static', 'chain_hook'}
# Lecteur : consulter et écouter
ROLE_LECTEUR = {
    'index', 'stats', 'about_page', 'pige_page', 'chain_page', 'logs_page', 'account_page',
    'get_stats', 'stream_stats', 'get_config_full', 'get_logs', 'get_signal_history', 'get_audio_history',
    'get_alerts_history', 'get_alerts_history_grouped', 'get_volume', 'proxy_stream', 'record_status',
    'wifi_status', 'email_alerts_status', 'mpx_spectrum',
    'pige_status', 'pige_segment', 'pige_archives', 'pige_archive_audio',
    'chain_status', 'chain_events', 'chain_device_history', 'chain_device_logs',
    'logs_search', 'logs_sources', 'account_password',
}
ROLE_LECTEUR_GET = {'chain_devices', 'chain_device'}      # lecture seule pour ces routes
# Opérateur : exporter, enregistrer, intervenir sans rien régler
ROLE_OPERATEUR = {
    'pige_export', 'chain_export', 'logs_export', 'record_start', 'record_stop', 'record_download',
    'email_alerts_toggle', 'chain_probe_now', 'set_volume', 'read_rds_ps', 'read_rds_rt',
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

'''

INCLUDE = "{% include '_roles.html' %}\n</head>"


def main():
    print('Patch comptes utilisateurs et rôles')
    for f in ('auth.py', 'templates/account.html', 'templates/_roles.html'):
        if not os.path.exists(os.path.join(BASE, f)):
            sys.exit(f'✗ {f} manquant : le copier dans ~/fm-monitor avant de lancer le patch')
    if 'class AuthError' not in rd('auth.py'):
        sys.exit("✗ auth.py est l'ancienne version : copier la nouvelle avant de lancer le patch")

    s = rd('app.py')
    if MARK in s:
        print('  = app.py déjà patché')
    else:
        s = replace(s, 'from auth import Auth\n', 'from auth import Auth, AuthError, ROLE_LEVEL, ROLE_LABELS\n', 'app.py')
        s = replace(s, LOGIN_OLD, LOGIN_NEW, 'app.py')
        s = replace_between(s, SAVE_AUTH_START, SAVE_AUTH_END, SAVE_AUTH_NEW, 'app.py')
        s = replace_between(s, CONFIG_START, CONFIG_END, CONFIG_NEW, 'app.py')
        anchor = '# Démarrage du monitor (exécuté aussi bien par Gunicorn que par python app.py)'
        s = replace(s, anchor, ACCESS.lstrip('\n') + '\n' + anchor, 'app.py')
        ast.parse(s)
        wr('app.py', s)

    for name in TEMPLATES:
        rel = f'templates/{name}.html'
        if not os.path.exists(os.path.join(BASE, rel)):
            print(f'  - {rel} absent, ignoré')
            continue
        t = rd(rel)
        if "_roles.html" in t:
            print(f'  = {rel} déjà patché')
            continue
        wr(rel, replace(t, '</head>', INCLUDE, rel))

    print('Terminé. Redémarrer : sudo systemctl restart fm-monitor')
    print('Le compte actuel de config.json devient administrateur au premier démarrage.')


if __name__ == '__main__':
    main()
