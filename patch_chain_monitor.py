#!/usr/bin/env python3
"""
patch_chain_monitor.py : ajoute la page « Chaîne de diffusion » à BL-FMO.

  - app.py : routes /chain et /api/chain/*, démarrage du ChainMonitor
  - templates/*.html : lien « Chaîne » dans le menu latéral

Prérequis : chain_probe.py et templates/chain.html déjà copiés dans le dossier.
Idempotent : relancer le script ne modifie rien si le patch est déjà appliqué.
Une copie .bak de chaque fichier modifié est faite avant écriture.

Usage (depuis le dossier de l'application) :
    python3 patch_chain_monitor.py
"""
import ast
import glob
import os
import shutil
import sys
from datetime import datetime

MARK = 'CHAIN_MONITOR_PATCH'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')

ROUTES = r"""
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

"""

STARTUP = r"""
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
"""

CHAIN_LINK = '    <a href="/chain" class="sidebar-link">\n      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>\n      Chaîne\n    </a>\n'


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def backup(path):
    dst = f"{path}.{STAMP}.bak"   # finit par .bak : ignoré par git
    shutil.copy2(path, dst)
    return dst


def patch_app():
    path = 'app.py'
    src = open(path, encoding='utf-8').read()
    if MARK in src:
        print("✓ app.py déjà patché")
        return
    s = src

    # 1. Le polling de /api/chain/status ne doit pas prolonger la session
    anchor = 'POLLING_ENDPOINTS = ('
    if s.count(anchor) != 1:
        fail("app.py : POLLING_ENDPOINTS introuvable")
    s = s.replace(anchor, anchor + "'chain_status', ", 1)

    # 2. Routes, juste avant le démarrage du monitor
    anchor = '# Démarrage du monitor'
    if s.count(anchor) != 1:
        fail("app.py : repère « # Démarrage du monitor » introuvable")
    s = s.replace(anchor, ROUTES.lstrip('\n') + '\n' + anchor, 1)

    # 3. Démarrage du ChainMonitor, après le bloc de démarrage du monitor
    anchor = 'logger.error(f"Erreur démarrage monitor: {e}")\n'
    if s.count(anchor) != 1:
        fail("app.py : bloc de démarrage du monitor introuvable")
    s = s.replace(anchor, anchor + STARTUP, 1)

    try:
        ast.parse(s)
    except SyntaxError as e:
        fail(f"app.py patché invalide ({e}), rien n'a été écrit")
    b = backup(path)
    open(path, 'w', encoding='utf-8').write(s)
    print(f"✓ app.py patché (sauvegarde : {b})")


def patch_templates():
    for path in sorted(glob.glob('templates/*.html')):
        if os.path.basename(path) in ('chain.html', 'login.html', 'setup.html'):
            continue
        s = open(path, encoding='utf-8').read()
        if 'href="/chain"' in s:
            print(f"✓ {path} : lien déjà présent")
            continue
        anchor = '    <a href="/about" class="sidebar-link'
        if s.count(anchor) != 1:
            print(f"⚠ {path} : menu non reconnu, lien à ajouter à la main")
            continue
        s = s.replace(anchor, CHAIN_LINK + anchor, 1)
        b = backup(path)
        open(path, 'w', encoding='utf-8').write(s)
        print(f"✓ {path} : lien « Chaîne » ajouté (sauvegarde : {b})")


def main():
    for f in ('app.py', 'chain_probe.py', 'templates/chain.html'):
        if not os.path.exists(f):
            fail(f"{f} absent : lancez le script depuis le dossier de l'application, "
                 "après avoir copié chain_probe.py et templates/chain.html")
    try:
        ast.parse(open('chain_probe.py', encoding='utf-8').read())
    except SyntaxError as e:
        fail(f"chain_probe.py invalide : {e}")
    patch_app()
    patch_templates()
    print("\nTerminé. Redémarrez le service : sudo systemctl restart fm-monitor")


if __name__ == '__main__':
    main()
