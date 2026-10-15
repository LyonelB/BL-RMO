#!/usr/bin/env python3
"""
patch_chain_export.py : export ZIP des journaux et mesures de la chaîne.

  - app.py : route /api/chain/export (?hours=24 ou ?start=...&end=... en ISO local)

Prérequis : patch_chain_monitor.py appliqué, chain_probe.py à jour (export_zip).
Idempotent, sauvegarde .bak avant écriture.

Usage (depuis le dossier de l'application) :
    python3 patch_chain_export.py
"""
import ast
import os
import shutil
import sys
from datetime import datetime

MARK = 'CHAIN_EXPORT_PATCH'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')

ROUTE = r'''
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

'''


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def main():
    for f in ('app.py', 'chain_probe.py'):
        if not os.path.exists(f):
            fail(f"{f} absent : lancez le script depuis le dossier de l'application")
    if 'def export_zip' not in open('chain_probe.py', encoding='utf-8').read():
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
        s = s.replace(anchor, ROUTE.lstrip('\n') + '\n' + anchor, 1)
        try:
            ast.parse(s)
        except SyntaxError as e:
            fail(f"app.py patché invalide ({e}), rien n'a été écrit")
        dst = f"{path}.{STAMP}.bak"
        shutil.copy2(path, dst)
        open(path, 'w', encoding='utf-8').write(s)
        print(f"✓ app.py patché (sauvegarde : {dst})")
    print("\nTerminé. Redémarrez le service : sudo systemctl restart fm-monitor")


if __name__ == '__main__':
    main()
