#!/usr/bin/env python3
"""
patch_chain_synoptic.py : synoptique de la chaîne de diffusion.

  - /chain           devient le synoptique (templates/chain.html, nouvelle version)
  - /chain/manage    gestion des appareils et des sondes (templates/chain_manage.html,
                     l'ancienne page Chaîne)
  - /api/chain/devices/<id>/history   courbe d'une valeur (24 h par défaut)
  - /api/chain/devices/<id>/logs      derniers journaux syslog de l'appareil

Prérequis : patch_chain_monitor.py appliqué, chain_probe.py, templates/chain.html
et templates/chain_manage.html à jour. Idempotent, sauvegarde .bak, validation AST.

Usage (depuis le dossier de l'application) :
    python3 patch_chain_synoptic.py
"""
import ast
import os
import shutil
import sys
from datetime import datetime

MARK = 'CHAIN_SYNOPTIC_PATCH'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')

ROUTES = r'''
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

'''


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def main():
    for f in ('app.py', 'chain_probe.py', 'templates/chain.html', 'templates/chain_manage.html'):
        if not os.path.exists(f):
            fail(f"{f} absent : lancez le script depuis le dossier de l'application, après avoir copié les fichiers")
    if 'def metric_history' not in open('chain_probe.py', encoding='utf-8').read():
        fail("chain_probe.py n'est pas à jour (copiez la nouvelle version)")
    path = 'app.py'
    s = open(path, encoding='utf-8').read()
    if MARK in s:
        print("✓ app.py déjà patché")
    else:
        if 'CHAIN_MONITOR_PATCH' not in s:
            fail("app.py : appliquez d'abord patch_chain_monitor.py")
        anchor = '# Démarrage du monitor'
        if s.count(anchor) != 1:
            fail("app.py : repère « # Démarrage du monitor » introuvable")
        s = s.replace(anchor, ROUTES.lstrip('\n') + '\n' + anchor, 1)
        try:
            ast.parse(s)
        except SyntaxError as e:
            fail(f"app.py patché invalide ({e}), rien n'a été écrit")
        b = f"{path}.{STAMP}.bak"
        shutil.copy2(path, b)
        open(path, 'w', encoding='utf-8').write(s)
        print(f"✓ app.py patché (sauvegarde : {b})")
    print("\nTerminé. Redémarrez le service : sudo systemctl restart fm-monitor")


if __name__ == '__main__':
    main()
