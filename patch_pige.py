#!/usr/bin/env python3
"""
Patch BL-FMO : ajout de la pige audio 24 h

- app.py      : routes /pige, /api/pige/status, /api/pige/segment/<nom>, /api/pige/export
- templates   : lien "Pige" dans la barre latérale (index, config, stats, about)
- .gitignore  : dossier pige/

Idempotent, copie .bak avant écriture, validation AST de app.py.
Usage : cd ~/fm-monitor && python3 patch_pige.py
"""
import ast
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
MARK = '# PIGE AUDIO 24H'

ROUTES = '''
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
        return jsonify({'status': 'error', 'message': 'Erreur interne pendant l\\'export'}), 500

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

'''

ANCHOR = '# Démarrage du monitor'

SIDEBAR_ANCHOR = '''    <a href="/about" class="sidebar-link'''
SIDEBAR_LINK = '''    <a href="/pige" class="sidebar-link">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"/></svg>
      Pige
    </a>
'''


def backup(path):
    shutil.copy2(path, path + '.bak')


def patch_app():
    path = os.path.join(BASE, 'app.py')
    src = open(path, encoding='utf-8').read()
    if MARK in src:
        print('app.py : déjà patché')
        return
    if src.count(ANCHOR) != 1:
        sys.exit(f"app.py : ancre '{ANCHOR}' introuvable ou multiple, abandon")
    new = src.replace(ANCHOR, ROUTES + ANCHOR)
    try:
        ast.parse(new)
    except SyntaxError as e:
        sys.exit(f"app.py : erreur de syntaxe après patch ({e}), rien n'a été écrit")
    backup(path)
    open(path, 'w', encoding='utf-8').write(new)
    print('app.py : routes pige ajoutées')


def patch_templates():
    for name in ('index.html', 'config.html', 'stats.html', 'about.html'):
        path = os.path.join(BASE, 'templates', name)
        if not os.path.exists(path):
            print(f'{name} : absent, ignoré')
            continue
        src = open(path, encoding='utf-8').read()
        if 'href="/pige"' in src:
            print(f'{name} : lien déjà présent')
            continue
        if src.count(SIDEBAR_ANCHOR) != 1:
            print(f'{name} : ancre sidebar introuvable, lien à ajouter à la main')
            continue
        backup(path)
        open(path, 'w', encoding='utf-8').write(src.replace(SIDEBAR_ANCHOR, SIDEBAR_LINK + SIDEBAR_ANCHOR))
        print(f'{name} : lien Pige ajouté')


def patch_gitignore():
    path = os.path.join(BASE, '.gitignore')
    src = open(path, encoding='utf-8').read() if os.path.exists(path) else ''
    if '\npige/' in '\n' + src:
        return
    with open(path, 'a', encoding='utf-8') as f:
        f.write('\n# Pige audio (enregistrements 24 h)\npige/\n')
    print('.gitignore : pige/ ajouté')


if __name__ == '__main__':
    if not os.path.exists(os.path.join(BASE, 'templates', 'pige.html')):
        sys.exit('templates/pige.html manquant : copier le fichier avant de lancer le patch')
    patch_app()
    patch_templates()
    patch_gitignore()
    print('\nTerminé. Étapes suivantes :')
    print('  sudo bash install/build/65-pige.sh')
    print('  sudo systemctl restart fm-monitor')
