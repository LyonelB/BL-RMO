#!/usr/bin/env python3
"""
Patch BL-FMO : fenêtre Pige sur la page d'accueil (suite de patch_pige.py)

- templates/index.html : bloc cliquable (état, disponible depuis, espace pige/libre)
                         placé dans la colonne de gauche, sous la carte "Signal RF"
                         (même largeur), lien vers /pige. Remplace la version
                         précédente si elle a déjà été appliquée.
- app.py               : pige_status ajouté aux requêtes automatiques (polling) pour
                         qu'elles ne prolongent pas la session de 60 min
- templates/pige.html  : redirection vers la connexion si la session a expiré

Idempotent, copie .bak avant écriture, validation AST de app.py.
Usage : cd ~/fm-monitor && python3 patch_pige_home.py
"""
import ast
import os
import shutil
import textwrap
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

WIDGET_RAW = '''<!-- ── Pige audio (résumé, clic = page Pige) ──────────────── -->
<a id="pige-widget" href="/pige" class="card block hover:border-blue-300 hover:shadow-sm transition" style="text-decoration:none" title="Ouvrir la pige audio">
  <div class="flex items-center justify-between mb-2">
    <span class="section-label flex items-center gap-1.5">
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"/></svg>
      Pige audio
    </span>
    <span class="text-xs font-medium text-blue-600">Ouvrir la pige &rarr;</span>
  </div>
  <div class="grid grid-cols-1 sm:grid-cols-3 md:grid-cols-1 xl:grid-cols-3 gap-3">
    <div class="rounded-lg bg-slate-50 px-3 py-2">
      <div class="section-label mb-1">État</div>
      <div class="flex items-center gap-2">
        <span id="pige-w-dot" class="w-2 h-2 rounded-full bg-gray-300"></span>
        <span class="text-sm font-semibold text-gray-900" id="pige-w-state">...</span>
      </div>
    </div>
    <div class="rounded-lg bg-slate-50 px-3 py-2">
      <div class="section-label mb-1">Disponible depuis</div>
      <div class="text-sm font-semibold text-gray-900" id="pige-w-since">-</div>
    </div>
    <div class="rounded-lg bg-slate-50 px-3 py-2">
      <div class="section-label mb-1">Espace pige / libre</div>
      <div class="text-sm font-semibold text-gray-900 font-mono" id="pige-w-disk">-</div>
    </div>
  </div>
</a>
<script>
(function () {
  const $ = id => document.getElementById(id);
  const pad = n => String(n).padStart(2, '0');
  const fmtBytes = b => b == null ? '?' : b >= 1e9 ? (b / 1e9).toFixed(1) + ' Go' : (b / 1e6).toFixed(0) + ' Mo';
  function fmtSince(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const day0 = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day0(now) - day0(d)) / 86400000);
    const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (diff === 0) return "Aujourd'hui " + hm;
    if (diff === 1) return 'Hier ' + hm;
    return d.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' }) + ' ' + hm;
  }
  function show(dotClass, state, since, disk) {
    $('pige-w-dot').className = 'w-2 h-2 rounded-full ' + dotClass;
    $('pige-w-state').textContent = state;
    $('pige-w-since').textContent = since;
    $('pige-w-disk').textContent = disk;
  }
  async function refresh() {
    try {
      const r = await fetch('/api/pige/status', { credentials: 'include' });
      if (!r.ok || r.redirected) throw new Error('indisponible');
      const s = await r.json();
      const on = s.enabled && s.recording;
      const dot = on ? 'bg-red-500 animate-pulse' : (s.enabled ? 'bg-amber-500' : 'bg-gray-300');
      const state = !s.enabled ? 'Désactivée' : on ? 'Enregistrement' : 'Arrêtée';
      const since = s.segments.length ? fmtSince(s.segments[0].start) : 'Aucun enregistrement';
      show(dot, state, since, fmtBytes(s.used_bytes) + ' / ' + fmtBytes(s.free_bytes));
    } catch (e) {
      show('bg-gray-300', 'Indisponible', '-', '-');
    }
  }
  refresh();
  setInterval(refresh, 30000);
})();
</script>

'''
WIDGET = textwrap.indent(WIDGET_RAW.rstrip('\n') + '\n', ' ' * 8)

POLL_OLD = "'get_stats', 'get_signal_history', 'get_record_status')"
POLL_NEW = "'get_stats', 'get_signal_history', 'get_record_status',\n                         'pige_status')"

PIGE_OLD = "if (r.status === 401) { location.href = '/login?timeout=1'; return; }"
PIGE_NEW = "if (r.status === 401 || r.redirected) { location.href = '/login?timeout=1'; return; }"


def backup(path):
    shutil.copy2(path, path + '.bak')


def patch_index():
    path = os.path.join(BASE, 'templates', 'index.html')
    src = open(path, encoding='utf-8').read()
    col_end = '<!-- /col gauche -->'
    if src.count(col_end) != 1:
        sys.exit("index.html : fin de la colonne gauche introuvable, abandon")

    idx = src.find('id="pige-widget"')
    if idx != -1:
        c = src.rfind('<!--', 0, idx)
        start = src.rfind('\n', 0, c) + 1
        end = src.find('</script>', idx) + len('</script>')
        end = end + 1 if src[end:end + 1] == '\n' else end
        in_left = src.find('Col gauche') < idx < src.find(col_end)
        if in_left and src[start:end] == WIDGET:
            print('index.html : fenêtre pige déjà à jour')
            return
        # ancienne version (autre emplacement ou contenu) : on la retire
        while src[end:end + 1] == '\n':
            end += 1
        src = src[:start] + src[end:]
        print('index.html : ancienne fenêtre pige retirée')

    pos = src.rfind('\n', 0, src.index(col_end)) + 1
    new = src[:pos] + '\n' + WIDGET + src[pos:]
    backup(path)
    open(path, 'w', encoding='utf-8').write(new)
    print('index.html : fenêtre pige placée sous la carte Signal RF')


def patch_app():
    path = os.path.join(BASE, 'app.py')
    src = open(path, encoding='utf-8').read()
    if "'pige_status'" in src.split('def check_session_timeout')[1].split('def ')[0]:
        print('app.py : pige_status déjà dans les requêtes automatiques')
        return
    if src.count(POLL_OLD) != 1:
        sys.exit("app.py : liste POLLING_ENDPOINTS introuvable, abandon")
    new = src.replace(POLL_OLD, POLL_NEW)
    try:
        ast.parse(new)
    except SyntaxError as e:
        sys.exit(f"app.py : erreur de syntaxe après patch ({e}), rien n'a été écrit")
    backup(path)
    open(path, 'w', encoding='utf-8').write(new)
    print('app.py : pige_status ajouté aux requêtes automatiques')


def patch_pige_page():
    path = os.path.join(BASE, 'templates', 'pige.html')
    src = open(path, encoding='utf-8').read()
    if PIGE_NEW in src:
        print('pige.html : redirection session déjà en place')
        return
    if src.count(PIGE_OLD) != 1:
        print('pige.html : ligne à corriger introuvable, ignoré')
        return
    backup(path)
    open(path, 'w', encoding='utf-8').write(src.replace(PIGE_OLD, PIGE_NEW))
    print('pige.html : redirection vers la connexion si session expirée')


if __name__ == '__main__':
    if not os.path.exists(os.path.join(BASE, 'templates', 'pige.html')):
        sys.exit('templates/pige.html absent : lancer d\'abord patch_pige.py')
    patch_index()
    patch_app()
    patch_pige_page()
    print('\nTerminé. Pour appliquer : sudo systemctl restart fm-monitor')
