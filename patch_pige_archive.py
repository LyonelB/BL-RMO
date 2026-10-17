#!/usr/bin/env python3
"""
Patch BL-FMO : extraits audio des 10 dernières alertes (module pige_archive.py)

- app.py              : routes /api/pige/archives et /api/pige/archives/<id>/audio,
                        démarrage de l'archiveur après la supervision de la chaîne
- templates/pige.html : section « Extraits des dernières alertes » (écoute, MP3)
- templates/stats.html: alerte sortie de la pige -> lien vers son extrait archivé

Idempotent, copie .bak avant écriture, validation AST de app.py.
Usage : cd ~/fm-monitor && python3 patch_pige_archive.py
"""
import ast
import os
import shutil
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
MARK = 'PIGE_ARCHIVE_PATCH'
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')


def rd(rel):
    with open(os.path.join(BASE, rel), encoding='utf-8') as f:
        return f.read()


def wr(rel, txt):
    p = os.path.join(BASE, rel)
    shutil.copy2(p, f'{p}.{STAMP}.bak')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(txt)
    print(f'  ✓ {rel}')


def must(s, needle, rel):
    if needle not in s:
        sys.exit(f'✗ {rel} : repère introuvable : {needle[:60]!r}')


# ── app.py ────────────────────────────────────────────────────────────────
ROUTES = '''
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

'''

START = '''
# Extraits des dernières alertes (PIGE_ARCHIVE_PATCH)
try:
    from pige_archive import PigeArchiver
    pige_archiver = PigeArchiver(pige_store, 'fm_monitor.db', pige_store.cfg)
    pige_archiver.start()
except Exception as e:
    logger.error(f"Erreur démarrage archivage des extraits d'alertes: {e}")

'''

# ── pige.html ─────────────────────────────────────────────────────────────
SECTION = '''
    <!-- ── Extraits des dernières alertes (PIGE_ARCHIVE_PATCH) ── -->
    <div class="section">
      <div class="section-title">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
        Extraits des dernières alertes
      </div>
      <p class="text-xs text-gray-400 mb-3" id="arch-help">De 30 s avant l'alerte à 30 s après le rétablissement. Ces extraits restent disponibles après la sortie de la pige.</p>
      <div id="arch-list" class="text-sm text-gray-400">Chargement...</div>
      <audio id="arch-audio" controls preload="none" class="w-full mt-3" style="display:none"></audio>
    </div>
'''

SCRIPT = '''  // PIGE_ARCHIVE_PATCH : extraits des dernières alertes
  async function loadArchives() {
    let d;
    try {
      const r = await fetch('/api/pige/archives', { credentials: 'include' });
      d = await r.json();
    } catch (e) { return; }
    const box = $('arch-list');
    if (d.keep) $('arch-help').textContent = `Les ${d.keep} derniers incidents, de 30 s avant l'alerte à 30 s après le rétablissement. Ces extraits restent disponibles après la sortie de la pige.`;
    if (!d.archives || !d.archives.length) { box.innerHTML = '<span class="text-xs">Aucun extrait pour le moment.</span>'; return; }
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    box.innerHTML = '<div class="divide-y divide-gray-100">' + d.archives.map(a => {
      const d0 = new Date(a.start * 1000);
      const day = `${pad(d0.getDate())}-${pad(d0.getMonth() + 1)}-${d0.getFullYear()}`;
      const causes = (a.causes && a.causes.length ? a.causes : [a.label]).map(esc).join('<br>');
      return `<div class="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5">
        <div class="font-mono text-xs text-gray-500" style="min-width:150px">${day} ${fmtTime(a.start)}</div>
        <div class="flex-1 text-sm text-gray-800" style="min-width:200px">${causes}
          <div class="text-xs text-gray-400">Durée de l'incident ${fmtDur(a.end - a.start)} · extrait ${fmtDur(a.clip_end - a.clip_start)} · ${fmtBytes(a.size)}</div></div>
        <div class="flex gap-3 whitespace-nowrap">
          <button class="text-xs font-medium text-blue-600 hover:text-blue-800" onclick="playArchive(${a.id})">Écouter</button>
          <a class="text-xs font-medium text-blue-600 hover:text-blue-800" href="/api/pige/archives/${a.id}/audio?dl=1">MP3</a>
        </div></div>`;
    }).join('') + '</div>';
  }
  function playArchive(id) {
    const p = $('arch-audio');
    audio.pause();
    p.style.display = '';
    p.src = `/api/pige/archives/${id}/audio`;
    p.play().catch(() => {});
  }
  loadArchives();
  setInterval(loadArchives, 60000);
'''

# ── stats.html ────────────────────────────────────────────────────────────
STATS_OLD_RANGE = '''      pigeFrom = st.segments && st.segments.length ? st.segments[0].start : null;
    } catch (e) { pigeFrom = null; }'''
STATS_NEW_RANGE = '''      pigeFrom = st.segments && st.segments.length ? st.segments[0].start : null;
    } catch (e) { pigeFrom = null; }
    try {   // PIGE_ARCHIVE_PATCH : extraits archivés des dernières alertes
      const r = await fetch('/api/pige/archives', {credentials: 'include'});
      pigeArchives = (await r.json()).archives || [];
    } catch (e) { pigeArchives = []; }'''
STATS_OLD_LINK = '''    if (!pigeFrom || t0 < pigeFrom) return '<span class="text-xs text-gray-300">hors pige</span>';'''
STATS_NEW_LINK = '''    if (!pigeFrom || t0 < pigeFrom) {
      const arc = pigeArchives.find(a => t0 >= a.clip_start - 60 && t0 <= a.end + 60);
      return arc
        ? `<a href="/api/pige/archives/${arc.id}/audio" target="_blank" class="text-xs font-medium text-blue-600 hover:text-blue-800" style="margin-right:12px" title="Extrait archivé, de 30 s avant à 30 s après">Écouter</a>`
          + `<a href="/api/pige/archives/${arc.id}/audio?dl=1" class="text-xs font-medium text-blue-600 hover:text-blue-800" title="Extrait archivé (MP3)">MP3</a>`
        : '<span class="text-xs text-gray-300">hors pige</span>';
    }'''


def main():
    print('Patch extraits des dernières alertes')
    if not os.path.exists(os.path.join(BASE, 'pige_archive.py')):
        sys.exit('✗ pige_archive.py manquant : le copier dans ~/fm-monitor avant de lancer le patch')

    # app.py
    s = rd('app.py')
    if MARK in s:
        print('  = app.py déjà patché')
    else:
        anchor = '# ══════════════════════════════════════════════════════════════════════════\n#  Chaîne de diffusion (CHAIN_MONITOR_PATCH)'
        must(s, anchor, 'app.py')
        s = s.replace(anchor, ROUTES.lstrip('\n') + '\n' + anchor, 1)
        anchor2 = "\nif __name__ == '__main__':"
        must(s, anchor2, 'app.py')
        s = s.replace(anchor2, START + anchor2, 1)
        ast.parse(s)
        wr('app.py', s)

    # pige.html
    s = rd('templates/pige.html')
    if MARK in s:
        print('  = templates/pige.html déjà patché')
    else:
        anchor = '\n  </div>\n</main>'
        must(s, anchor, 'templates/pige.html')
        s = s.replace(anchor, '\n' + SECTION.rstrip('\n') + anchor, 1)
        anchor = '  updateSidebarFrequency();\n  setInterval(loadStatus, 15000);'
        must(s, anchor, 'templates/pige.html')
        s = s.replace(anchor, SCRIPT + anchor, 1)
        wr('templates/pige.html', s)

    # stats.html
    s = rd('templates/stats.html')
    if MARK in s:
        print('  = templates/stats.html déjà patché')
    else:
        for n in (STATS_OLD_RANGE, STATS_OLD_LINK, 'let pigeFrom'):
            must(s, n, 'templates/stats.html')
        s = s.replace(STATS_OLD_RANGE, STATS_NEW_RANGE, 1).replace(STATS_OLD_LINK, STATS_NEW_LINK, 1)
        s = s.replace('let pigeFrom', 'let pigeArchives = [];\n  let pigeFrom', 1)
        wr('templates/stats.html', s)

    print('Terminé. Redémarrer : sudo systemctl restart fm-monitor')


if __name__ == '__main__':
    main()
