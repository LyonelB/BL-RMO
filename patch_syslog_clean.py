#!/usr/bin/env python3
"""
Patch BL-FMO : journaux nettoyés et messages masqués (module syslog_clean.py)

À appliquer APRÈS patch_users.py (rôles).

- app.py : /api/syslog et l'export passent par syslog_clean (préfixe du BW retiré,
  relevé DTLG résumé, messages connus masqués sauf « Afficher les messages
  masqués ») ; routes /api/syslog/rules (lecture : tous ; modification : admin).
- chain_probe.py : l'onglet Journaux du synoptique montre les messages nettoyés,
  sans les messages masqués.
- templates/logs.html : case « Afficher les messages masqués (N) », messages sur
  plusieurs lignes, relevé du BW en une ligne avec le détail dépliable, action
  « Masquer les semblables » (admin), section « Messages masqués » (règles).
- templates/chain.html : panneau Journaux avec les messages nettoyés.

Idempotent, copie .bak avant écriture, validation AST des fichiers Python.
Usage : cd ~/fm-monitor && python3 patch_syslog_clean.py
"""
import ast
import os
import shutil
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
MARK = 'SYSLOG_CLEAN_PATCH'
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


def rep(s, old, new, rel):
    if old not in s:
        sys.exit(f'✗ {rel} : repère introuvable : {old.strip()[:80]!r}')
    return s.replace(old, new, 1)


def between(s, start, end, new, rel):
    i = s.find(start)
    j = s.find(end, i + len(start)) if i >= 0 else -1
    if i < 0 or j < 0:
        sys.exit(f'✗ {rel} : bloc introuvable : {start.strip()[:80]!r}')
    return s[:i] + new + s[j:]


# ══ app.py ═══════════════════════════════════════════════════════════════
SEARCH_OLD = '''    try:
        rows, total = _syslog().search(limit=request.args.get('limit', 200),
                                       offset=request.args.get('offset', 0), **_syslog_filters())
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Paramètres invalides'}), 400
    return jsonify({'rows': rows, 'total': total})'''
SEARCH_NEW = '''    try:   # SYSLOG_CLEAN_PATCH : messages nettoyés, messages connus masqués
        rows, total, hidden, capped = _syslog_cleaner().search(
            _syslog(), hide=request.args.get('hidden') != '1', limit=request.args.get('limit', 200),
            offset=request.args.get('offset', 0), **_syslog_filters())
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Paramètres invalides'}), 400
    return jsonify({'rows': rows, 'total': total, 'hidden': hidden, 'capped': capped})'''

EXPORT_OLD = '        for r in st.iter_rows(**f):\n'
EXPORT_NEW = "        for r in _syslog_cleaner().iter_rows(st, hide=hide, **f):\n"
EXPORT_HIDE_OLD = '''    f = _syslog_filters()
    names = _syslog_names()
    st = _syslog()
'''
EXPORT_HIDE_NEW = '''    f = _syslog_filters()
    hide = request.args.get('hidden') != '1'
    names = _syslog_names()
    st = _syslog()
'''

HELPERS_ANCHOR = 'def _syslog_names():\n'
HELPERS = '''syslog_cleaner = None


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


'''

ROLE_OLD = "ROLE_LECTEUR_GET = {'chain_devices', 'chain_device'}"
ROLE_NEW = "ROLE_LECTEUR_GET = {'chain_devices', 'chain_device', 'logs_rules'}"

# ══ chain_probe.py ═══════════════════════════════════════════════════════
CP_OLD = '        rows, _total = store.search(limit=limit, src=host)\n'
CP_NEW = '''        from syslog_clean import Cleaner          # SYSLOG_CLEAN_PATCH
        rows = Cleaner(sys_db).search(store, hide=True, limit=limit, src=host)[0]
'''

# ══ logs.html ════════════════════════════════════════════════════════════
L_CHECK_OLD = '''        <label class="flex items-center gap-1.5 text-xs text-gray-600 select-none"><input id="f-live" type="checkbox" checked class="rounded"> En direct</label>'''
L_CHECK_NEW = '''        <label class="flex items-center gap-1.5 text-xs text-gray-600 select-none" title="Messages connus sans importance (voir « Messages masqués » plus bas)"><input id="f-hidden" type="checkbox" class="rounded"> Afficher les messages masqués <span id="hidden-count" class="text-gray-400"></span></label>
        <label class="flex items-center gap-1.5 text-xs text-gray-600 select-none"><input id="f-live" type="checkbox" checked class="rounded"> En direct</label>'''

L_RULES_ANCHOR = '''    <!-- ── Aide à la configuration ────────────────────────────── -->'''
L_RULES = '''    <!-- ── Messages masqués (SYSLOG_CLEAN_PATCH) ─────────────────── -->
    <details id="rules-box" class="section">
      <summary class="section-title cursor-pointer select-none" style="margin-bottom:0">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"/></svg>
        Messages masqués <span id="rules-count" class="text-gray-400 normal-case font-normal tracking-normal"></span>
      </summary>
      <p class="mt-3 text-xs text-gray-500">Ces messages restent enregistrés : ils sont seulement cachés de la liste, de l'export et du synoptique. Cochez « Afficher les messages masqués » pour les revoir.
        <span class="need-admin">Pour en masquer d'autres, utilisez l'icône <b>œil barré</b> au bout d'une ligne.</span></p>
      <div id="rules-list" class="mt-3 divide-y divide-gray-50 text-xs"></div>
    </details>

'''

L_LOAD_OLD = '''    total = d.total;
    render(d.rows);'''
L_LOAD_NEW = '''    total = d.total;
    capped = d.capped;
    $('hidden-count').textContent = d.hidden ? `(${d.hidden >= 20000 ? '20 000+' : d.hidden.toLocaleString('fr-FR')})` : '';
    render(d.rows);'''

L_QUERY_OLD = "  if ($('f-q').value.trim()) p.set('q', $('f-q').value.trim());\n"
L_QUERY_NEW = "  if ($('f-q').value.trim()) p.set('q', $('f-q').value.trim());\n  if ($('f-hidden').checked) p.set('hidden', '1');\n"

L_RENDER_START = 'function render(rows) {\n'
L_RENDER_END = 'function page(dir) {\n'
L_RENDER = r'''function render(rows) {   // SYSLOG_CLEAN_PATCH
  $('log-empty').classList.toggle('hidden', rows.length > 0);
  if (!rows.length) $('log-empty').textContent = 'Aucun message pour ces critères.';
  lastRows = rows;
  $('log-body').innerHTML = rows.map((r, i) => {
    const sv = SEV[r.severity] || SEV[6];
    const t = new Date(r.ts);
    const epoch = Math.floor(t.getTime() / 1000);
    const msg = r.summary
      ? `<details><summary class="cursor-pointer">${esc(r.summary)}</summary><div class="mt-1 text-gray-500" style="white-space:pre-wrap">${esc(r.message)}</div></details>`
      : `<div style="white-space:pre-wrap">${esc(r.message)}</div>`;
    const masked = r.hidden_by ? `<span class="inline-block mt-0.5 rounded px-1.5 text-[10px] bg-slate-100 text-slate-500">masqué · ${esc(r.hidden_label || '')}</span>` : '';
    return `<tr class="border-b border-gray-50 hover:bg-slate-50" style="vertical-align:top;${r.hidden_by ? 'opacity:.55' : ''}">
      <td class="py-1.5 pr-3 font-mono text-gray-400 whitespace-nowrap" title="${r.device_ts ? 'Heure de l\'appareil : ' + esc(r.device_ts.replace('T', ' ')) : ''}">${pad(t.getDate())}/${pad(t.getMonth() + 1)} ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}</td>
      <td class="py-1.5 pr-3"><span class="inline-block rounded px-1.5 py-0.5 text-[11px] whitespace-nowrap ${sv.cls}">${sv.label}</span></td>
      <td class="py-1.5 pr-3 whitespace-nowrap text-gray-800" title="${esc(r.src)}${r.host ? ' · ' + esc(r.host) : ''}">${esc(displayName(r.src))}</td>
      <td class="py-1.5 pr-3 whitespace-nowrap text-gray-500 font-mono">${esc(r.program)}</td>
      <td class="py-1.5 pr-3 text-gray-700" style="overflow-wrap:anywhere;min-width:16rem">${msg}${masked}</td>
      <td class="py-1.5 whitespace-nowrap"><a href="/pige?t=${epoch}" title="Écouter l'antenne à ce moment" class="text-blue-600 hover:text-blue-800 opacity-60 hover:opacity-100">
        <svg class="w-3.5 h-3.5 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg></a>
        ${r.hidden_by ? '' : `<button class="need-admin ml-1 text-gray-400 hover:text-gray-700 opacity-60 hover:opacity-100" title="Masquer les messages semblables" onclick="hideSimilar(${i})">
        <svg class="w-3.5 h-3.5 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"/></svg></button>`}</td>
    </tr>`;
  }).join('');
  const first = total ? offset + 1 : 0, last = Math.min(offset + PAGE_SIZE, total);
  const fmtTotal = capped ? `${total.toLocaleString('fr-FR')}+` : total.toLocaleString('fr-FR');
  $('log-count').textContent = `${fmtTotal} message${total > 1 ? 's' : ''}`;
  $('pg-info').textContent = total ? `${first.toLocaleString('fr-FR')}-${last.toLocaleString('fr-FR')}` : '';
  $('pg-prev').disabled = offset === 0;
  $('pg-next').disabled = !capped && offset + PAGE_SIZE >= total;
}

// ── Messages masqués : règles ──────────────────────────────────────────
let lastRows = [], capped = false;
async function apiSend(url, method, body) {
  const r = await fetch(url, { method, credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(body || {}) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.message || `Erreur ${r.status}`);
  return d;
}
async function hideSimilar(i) {
  const r = lastRows[i];
  if (!r) return;
  const text = (r.message || '').split('\n')[0];
  const label = `${displayName(r.src)} : ${text.slice(0, 80)}`;
  if (!confirm(`Masquer les messages semblables à celui-ci ?\n\n${label}\n\nLes chiffres variables sont pris en compte. Les messages restent enregistrés, et la règle peut être retirée dans « Messages masqués ».`)) return;
  try { await apiSend('/api/syslog/rules', 'POST', { src: r.src, program: r.program, message: r.raw, label }); }
  catch (e) { alert(e.message); return; }
  loadRules(); loadLogs();
}
async function loadRules() {
  let d;
  try { d = await api('/api/syslog/rules'); } catch (e) { return; }
  const admin = document.documentElement.dataset.role === 'admin';
  const on = d.rules.filter(r => r.enabled).length;
  $('rules-count').textContent = `${on} règle${on > 1 ? 's' : ''} active${on > 1 ? 's' : ''}`;
  $('rules-list').innerHTML = d.rules.map(r => {
    const scope = [r.src ? displayName(r.src) : 'tous les appareils', r.program ? `programme ${r.program}` : ''].filter(Boolean).join(' · ');
    return `<div class="flex items-center gap-3 py-2">
      <input type="checkbox" ${r.enabled ? 'checked' : ''} ${admin ? '' : 'disabled'} onchange="toggleRule('${esc(r.id)}', this.checked)" title="Activer ou désactiver">
      <div class="flex-1 ${r.enabled ? 'text-gray-700' : 'text-gray-400'}">${esc(r.label)}
        <div class="text-[11px] text-gray-400">${esc(scope)}${r.builtin ? ' · intégrée' : (r.created_by ? ' · ajoutée par ' + esc(r.created_by) : '')}</div></div>
      ${r.builtin ? '' : `<button class="need-admin text-red-500 hover:text-red-700 font-medium" onclick="deleteRule('${esc(r.id)}')">Supprimer</button>`}
    </div>`;
  }).join('');
}
async function toggleRule(id, enabled) {
  try { await apiSend(`/api/syslog/rules/${encodeURIComponent(id)}`, 'PUT', { enabled }); } catch (e) { alert(e.message); }
  loadRules(); loadLogs();
}
async function deleteRule(id) {
  if (!confirm('Supprimer cette règle ? Les messages concernés réapparaîtront.')) return;
  try { await apiSend(`/api/syslog/rules/${encodeURIComponent(id)}`, 'DELETE'); } catch (e) { alert(e.message); }
  loadRules(); loadLogs();
}

'''

L_INIT_OLD = 'loadSources().then(loadLogs);'
L_INIT_NEW = 'loadSources().then(() => { loadLogs(); loadRules(); });'

L_FILTERS_OLD = "['f-src', 'f-sev', 'f-period'].forEach("
L_FILTERS_NEW = "['f-src', 'f-sev', 'f-period', 'f-hidden'].forEach("

# ══ chain.html (panneau Journaux) ════════════════════════════════════════
C_OLD = '''<span class="text-gray-700">${esc(l.message)}</span></span>'''
C_NEW = '''<span class="text-gray-700" style="white-space:pre-wrap">${esc(l.summary || l.message)}</span></span>'''


def main():
    print('Patch journaux nettoyés et messages masqués')
    if not os.path.exists(os.path.join(BASE, 'syslog_clean.py')):
        sys.exit('✗ syslog_clean.py manquant : le copier dans ~/fm-monitor avant de lancer le patch')

    s = rd('app.py')
    if MARK in s:
        print('  = app.py déjà patché')
    else:
        if 'USERS_ROLES_PATCH' not in s:
            sys.exit('✗ appliquer d\'abord patch_users.py (comptes et rôles)')
        s = rep(s, SEARCH_OLD, SEARCH_NEW, 'app.py')
        s = rep(s, EXPORT_HIDE_OLD, EXPORT_HIDE_NEW, 'app.py')
        s = rep(s, EXPORT_OLD, EXPORT_NEW, 'app.py')
        s = rep(s, HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 'app.py')
        s = rep(s, ROLE_OLD, ROLE_NEW, 'app.py')
        ast.parse(s)
        wr('app.py', s)

    s = rd('chain_probe.py')
    if MARK in s:
        print('  = chain_probe.py déjà patché')
    else:
        s = rep(s, CP_OLD, CP_NEW, 'chain_probe.py')
        ast.parse(s)
        wr('chain_probe.py', s)

    s = rd('templates/logs.html')
    if MARK in s:
        print('  = templates/logs.html déjà patché')
    else:
        s = rep(s, L_CHECK_OLD, L_CHECK_NEW, 'logs.html')
        s = rep(s, L_RULES_ANCHOR, L_RULES + L_RULES_ANCHOR, 'logs.html')
        s = rep(s, L_LOAD_OLD, L_LOAD_NEW, 'logs.html')
        s = rep(s, L_QUERY_OLD, L_QUERY_NEW, 'logs.html')
        s = between(s, L_RENDER_START, L_RENDER_END, L_RENDER, 'logs.html')
        s = rep(s, L_FILTERS_OLD, L_FILTERS_NEW, 'logs.html')
        s = rep(s, L_INIT_OLD, L_INIT_NEW, 'logs.html')
        wr('templates/logs.html', s)

    s = rd('templates/chain.html')
    if 'l.summary || l.message' in s:
        print('  = templates/chain.html déjà patché')
    else:
        wr('templates/chain.html', rep(s, C_OLD, C_NEW, 'chain.html'))

    print('Terminé. Redémarrer : sudo systemctl restart fm-monitor')


if __name__ == '__main__':
    main()
