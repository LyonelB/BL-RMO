#!/usr/bin/env python3
"""
patch_chain_hook.py : remontée d'événements externes + lien vers la pige.

  - app.py : routes /api/chain/hook (réception, protégée par jeton),
             /api/chain/hook/info et /api/chain/hook/token (gestion)
  - templates/pige.html : ouverture positionnée avec /pige?t=<horodatage>

Prérequis : patch_chain_monitor.py déjà appliqué, chain_probe.py et
templates/chain.html à jour. Idempotent, sauvegarde .bak avant écriture.

Usage (depuis le dossier de l'application) :
    python3 patch_chain_hook.py
"""
import ast
import os
import shutil
import sys
from datetime import datetime

MARK = 'CHAIN_HOOK_PATCH'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')

ROUTES = r'''
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

'''

PIGE_OLD = "  loadStatus().then(() => quick(15));"
PIGE_NEW = """  // CHAIN_HOOK_PATCH : /pige?t=<horodatage> ouvre la pige 30 s avant l'événement
  loadStatus().then(() => {
    const t = parseInt(new URLSearchParams(location.search).get('t'), 10);
    if (!t || !status) { quick(15); return; }
    $('in-start').value = toInput(t - 30);
    $('in-end').value = toInput(t + 90);
    onRangeChange();
    const f = findSeg(t - 30);
    if (f) loadSeg(f.seg, f.offset, false);   // pas de lecture auto (navigateurs)
    else $('play-date').textContent = "Cet instant n'est plus dans la pige";
  });"""


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def backup(path):
    dst = f"{path}.{STAMP}.bak"
    shutil.copy2(path, dst)
    return dst


def patch_app():
    path = 'app.py'
    s = open(path, encoding='utf-8').read()
    if MARK in s:
        print("✓ app.py déjà patché")
        return
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
    b = backup(path)
    open(path, 'w', encoding='utf-8').write(s)
    print(f"✓ app.py patché (sauvegarde : {b})")


def patch_pige():
    path = 'templates/pige.html'
    if not os.path.exists(path):
        print("⚠ templates/pige.html absent : lien « Écouter » sans positionnement")
        return
    s = open(path, encoding='utf-8').read()
    if MARK in s:
        print("✓ pige.html déjà patché")
        return
    if s.count(PIGE_OLD) != 1:
        print("⚠ pige.html : initialisation non reconnue, positionnement non ajouté")
        return
    s = s.replace(PIGE_OLD, PIGE_NEW, 1)
    b = backup(path)
    open(path, 'w', encoding='utf-8').write(s)
    print(f"✓ pige.html : ouverture positionnée ajoutée (sauvegarde : {b})")


def main():
    for f in ('app.py', 'chain_probe.py', 'templates/chain.html'):
        if not os.path.exists(f):
            fail(f"{f} absent : lancez le script depuis le dossier de l'application")
    if 'record_external' not in open('chain_probe.py', encoding='utf-8').read():
        fail("chain_probe.py n'est pas à jour (copiez la nouvelle version)")
    patch_app()
    patch_pige()
    print("\nTerminé. Redémarrez le service : sudo systemctl restart fm-monitor")


if __name__ == '__main__':
    main()
