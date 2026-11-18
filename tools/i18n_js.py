#!/usr/bin/env python3
"""Outil de traduction du JavaScript des modèles : entoure les textes français entre
apostrophes ou guillemets de _t('...') (templates/_i18n.html).

    python3 tools/i18n_js.py --dry templates/fm.html   # liste les textes retenus
    python3 tools/i18n_js.py templates/fm.html         # modifie le fichier

Ne traite que les blocs <script> sans src. Retient un texte qui contient un mot d'au moins
trois lettres et une espace ou une lettre accentuée ; laisse de côté le HTML, les classes
CSS, les sélecteurs, les adresses, les comparaisons, les journaux de la console et ce qui
est déjà traduit. Les gabarits `...${x}...` se traduisent à la main avec _t('... {x}', {x}).
"""
import re
import sys

SCRIPT = re.compile(r'(<script>)(.*?)(</script>)', re.S)
LIT = re.compile(r"""(?<![\w$])('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")""")
WORD = re.compile(r'[a-zà-ÿ]{3,}', re.I)
ACCENT = re.compile(r'[À-ÿ]')
CSS = re.compile(r'(^|\s)(text|bg|px|py|mt|mb|ml|mr|w|h|rounded|border|font|flex|grid|gap|hidden|inline|block|truncate|hover|cursor|min|max|space|divide|items|justify|tabular|whitespace|leading|tracking|uppercase|opacity|pulse|underline|shrink|order|sm|md|lg)[-:\s]')
BEFORE_SKIP = re.compile(r"""(\$\(|getElementById\(|querySelector(All)?\(|classList\.\w+\(|fetch\(|addEventListener\(|setAttribute\(|removeAttribute\(|console\.\w+\(|===?\s*|!==?\s*|case\s+|_t\(|\[\s*|\.(?:get|has|includes|startsWith|endsWith|indexOf|split|join|replace|toLocale\w+)\(\s*)$""")


def wanted(body):
    if not WORD.search(body):
        return False
    if '<' in body or '${' in body or '/api/' in body or body.startswith(('/', '#', '.', 'http')):
        return False
    if CSS.search(' ' + body):
        return False
    if re.search(r'\{\w+\}', body) or re.fullmatch(r'[\w.-]+\.(py|json|sh|html|db|mp3|local)', body):
        return False      # déjà une clé à valeurs ({n}), ou un nom de fichier
    if ' ' in body.strip() or ACCENT.search(body):
        return True
    return bool(re.fullmatch(r'[A-Z][a-z]{3,}…?', body))      # un mot : Pause, Lecture, Connexion… (pas POST, SNR)


REGEX_PREV = set('(,=:[!&|?{};+-*%<>~^')


def literals(js):
    """Positions (début, fin) des textes '...' et "..." du code, hors commentaires,
    expressions régulières et texte des gabarits `...` (mais dans leurs ${...})."""
    i, n, out = 0, len(js), []
    tpls, tpl_start = [], None
    stack = []          # 'tpl' : dans le texte d'un gabarit ; 'expr' : dans ${ }, avec profondeur
    depth = [0]
    last = ''           # dernier caractère significatif du code
    while i < n:
        c = js[i]
        if stack and stack[-1] == 'tpl':
            if tpl_start is None:
                tpl_start = i
            if c in '`$' and (c == '`' or js.startswith('${', i)):
                if i > tpl_start:
                    tpls.append((tpl_start, i))
                tpl_start = None
            if c == '\\':
                i += 2; continue
            if c == '`':
                stack.pop(); i += 1; last = '`'; continue
            if js.startswith('${', i):
                stack.append('expr'); depth.append(0); i += 2; last = '{'; continue
            i += 1; continue
        if js.startswith('//', i):
            i = js.find('\n', i); i = n if i < 0 else i; continue
        if js.startswith('/*', i):
            i = js.find('*/', i); i = n if i < 0 else i + 2; continue
        if c in '\'"':
            j = i + 1
            while j < n and js[j] != c and js[j] != '\n':
                j += 2 if js[j] == '\\' else 1
            out.append((i, j + 1)); i = j + 1; last = c; continue
        if c == '`':
            stack.append('tpl'); i += 1; continue
        if c == '/' and (last in REGEX_PREV or last == ''):
            j, cls = i + 1, False
            while j < n and js[j] != '\n':
                if js[j] == '\\': j += 2; continue
                if js[j] == '[': cls = True
                elif js[j] == ']': cls = False
                elif js[j] == '/' and not cls: break
                j += 1
            i = j + 1; last = '/'; continue
        if stack and stack[-1] == 'expr':
            if c == '{': depth[-1] += 1
            elif c == '}':
                if depth[-1] == 0:
                    stack.pop(); depth.pop(); i += 1; last = '`'; continue
                depth[-1] -= 1
        if not c.isspace():
            last = c if not (c.isalnum() or c in '_$') else 'w'
            if c.isalnum() or c in '_$':
                # mot-clé suivi d'une expression régulière : return /x/
                k = i
                while k < n and (js[k].isalnum() or js[k] in '_$'): k += 1
                last = '(' if js[i:k] in ('return', 'typeof', 'case') else 'w'
                i = k; continue
        i += 1
    return out, tpls


HTML_TEXT = re.compile(r'>([^<>]*[A-Za-zÀ-ÿ]{3,}[^<>]*)<')
HTML_TITLE = re.compile(r'\b(title|placeholder|aria-label)="([^"]*[A-Za-zÀ-ÿ]{3,}[^"]*)"')


def html_parts(body, open_, close_, found):
    """Traduit les textes et titres d'un bout de HTML : open_ + _t('x') + close_."""
    def q(t):
        # Dans un gabarit, l'apostrophe du texte doit être échappée dans _t('...')
        return re.sub(r"(?<!\\)'", "\\\\'", t) if open_.startswith('$') else t

    def ok(t):
        t = t.strip()
        return t and '${' not in t and "'" + "+" not in t and wanted_html(t)

    def rep_text(m):
        raw = m.group(1)
        if not ok(raw):
            return m.group(0)
        lead = raw[:len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        found.append(raw.strip())
        return '>' + lead + open_ + q(raw.strip()) + close_ + trail + '<'

    def rep_attr(m):
        if not ok(m.group(2)):
            return m.group(0)
        found.append(m.group(2))
        return f'{m.group(1)}="' + open_ + q(m.group(2)) + close_ + '"'
    return HTML_TITLE.sub(rep_attr, HTML_TEXT.sub(rep_text, body))


def wanted_html(t):
    if t.startswith(('sudo ', 'install/')) or re.fullmatch(r'[-+]?[\d.,]+\s*\w{0,4}', t) or t in ('MP3', 'USB', 'OK', 'PI', 'PS', 'DLS', 'SNR'):
        return False
    return bool(WORD.search(t))


def wrap_js(js, found):
    out, pos = [], 0
    lits, tpls = literals(js)
    spans = [(a, b, 'lit') for a, b in lits] + [(a, b, 'tpl') for a, b in tpls]
    for a, b, kind in sorted(spans):
        if a < pos:
            continue
        if kind == 'tpl':
            seg = js[a:b]
            if '<' in seg or 'title="' in seg:
                new = html_parts(seg, "${_t('", "')}", found)
                if new != seg:
                    out.append(js[pos:a]); out.append(new); pos = b
            continue
        lit = js[a:b]
        body = lit[1:-1]
        if lit[0] == "'" and '<' in body and not BEFORE_SKIP.search(js[max(0, a - 40):a]):
            new = html_parts(body, "' + _t('", "') + '", found)
            if new != body:
                out.append(js[pos:a]); out.append("'" + new + "'"); pos = b
            continue
        before = js[max(0, a - 40):a]
        after = js[b:b + 3]
        if BEFORE_SKIP.search(before) or (re.match(r'\s*:', after) and re.search(r'[{,]\s*$', before)):
            continue      # clé d'objet, sélecteur, comparaison...
        if not wanted(body):
            continue
        found.append(body)
        out.append(js[pos:a])
        out.append('_t(' + lit + ')')
        pos = b
    out.append(js[pos:])
    return ''.join(out)


def main():
    args = sys.argv[1:]
    dry = '--dry' in args
    for f in [a for a in args if not a.startswith('--')]:
        src = open(f, encoding='utf-8').read()
        found = []
        new = SCRIPT.sub(lambda m: m.group(1) + wrap_js(m.group(2), found) + m.group(3), src)
        if dry:
            for x in found:
                print(f'{f}: {x}')
        elif new != src:
            open(f, 'w', encoding='utf-8').write(new)
        print(f'{f}: {len(found)} textes', file=sys.stderr)


if __name__ == '__main__':
    main()
