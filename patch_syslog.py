#!/usr/bin/env python3
"""
patch_syslog.py : page « Journaux » (réception syslog des appareils).

  - app.py : routes /logs, /api/syslog, /api/syslog/sources, /api/syslog/export
             (/api/logs existe déjà : journal du service, page Statistiques)
  - templates/*.html : lien « Journaux » dans le menu latéral

Prérequis : syslog_server.py et templates/logs.html copiés dans le dossier,
patch_chain_monitor.py déjà appliqué (les noms des appareils viennent de la chaîne).
Le service de réception s'installe à part : sudo bash install/build/66-syslog.sh

Idempotent, sauvegarde .bak avant écriture, validation AST de app.py.
Usage (depuis le dossier de l'application) :
    python3 patch_syslog.py
"""
import ast
import glob
import os
import shutil
import sys
from datetime import datetime

MARK = 'SYSLOG_PATCH'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')

ROUTES = r'''
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
    try:
        rows, total = _syslog().search(limit=request.args.get('limit', 200),
                                       offset=request.args.get('offset', 0), **_syslog_filters())
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Paramètres invalides'}), 400
    return jsonify({'rows': rows, 'total': total})


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
    names = _syslog_names()
    st = _syslog()

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=';', lineterminator='\n')
        w.writerow(['date', 'heure', 'niveau', 'appareil', 'adresse', 'nom_hote', 'categorie', 'programme', 'message'])
        yield '﻿' + buf.getvalue()
        n = 0
        for r in st.iter_rows(**f):
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

    fname = f"blfmo-journaux_{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(generate(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})

'''

LINK = ('    <a href="/logs" class="sidebar-link">\n'
        '      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" '
        'stroke-width="2" d="M4 6h16M4 10h16M4 14h10M4 18h10"/></svg>\n'
        '      Journaux\n'
        '    </a>\n')


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def backup(path):
    dst = f"{path}.{STAMP}.bak"   # finit par .bak : ignoré par git
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
    # Le rafraîchissement automatique de la page ne doit pas prolonger la session
    anchor = 'POLLING_ENDPOINTS = ('
    if s.count(anchor) == 1:
        s = s.replace(anchor, anchor + "'logs_search', 'logs_sources', ", 1)
    try:
        ast.parse(s)
    except SyntaxError as e:
        fail(f"app.py patché invalide ({e}), rien n'a été écrit")
    b = backup(path)
    open(path, 'w', encoding='utf-8').write(s)
    print(f"✓ app.py patché (sauvegarde : {b})")


def patch_templates():
    for path in sorted(glob.glob('templates/*.html')):
        if os.path.basename(path) in ('logs.html', 'login.html', 'setup.html'):
            continue
        s = open(path, encoding='utf-8').read()
        if 'href="/logs"' in s:
            print(f"✓ {path} : lien déjà présent")
            continue
        anchor = '    <a href="/about" class="sidebar-link'
        if s.count(anchor) != 1:
            print(f"⚠ {path} : menu non reconnu, lien à ajouter à la main")
            continue
        s = s.replace(anchor, LINK + anchor, 1)
        b = backup(path)
        open(path, 'w', encoding='utf-8').write(s)
        print(f"✓ {path} : lien « Journaux » ajouté (sauvegarde : {b})")


def patch_gitignore():
    path = '.gitignore'
    s = open(path, encoding='utf-8').read() if os.path.exists(path) else ''
    if 'syslog.db' in s:
        return
    s = s.rstrip('\n') + '\n\n# Journaux reçus des appareils (syslog)\nsyslog.db*\n'
    open(path, 'w', encoding='utf-8').write(s)
    print("✓ .gitignore : syslog.db ajouté")


def main():
    for f in ('app.py', 'syslog_server.py', 'templates/logs.html'):
        if not os.path.exists(f):
            fail(f"{f} absent : lancez le script depuis le dossier de l'application, "
                 "après avoir copié syslog_server.py et templates/logs.html")
    patch_app()
    patch_templates()
    patch_gitignore()
    print("\nÉtapes suivantes :\n"
          "  sudo bash install/build/66-syslog.sh     # service de réception (UDP 514)\n"
          "  sudo systemctl restart fm-monitor")


if __name__ == '__main__':
    main()
