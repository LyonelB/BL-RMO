#!/usr/bin/env python3
"""Outil de traduction : entoure les textes visibles d'un modèle Jinja de {{ _('...') }}.

    python3 tools/i18n_wrap.py templates/fm.html          # modifie le fichier
    python3 tools/i18n_wrap.py --keys templates/*.html    # liste les textes à traduire

Traite les textes entre balises et les attributs placeholder, title, aria-label et alt,
hors <script>, <style> et commentaires. Laisse de côté ce qui contient déjà du Jinja,
les nombres, les symboles et le code. Le JavaScript des pages se traduit à la main
avec _t('...') (templates/_i18n.html).
"""
import json
import re
import sys

SKIP_BLOCKS = re.compile(r'(<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->|\{#.*?#\}|<pre\b.*?</pre>|<code\b.*?</code>)', re.S | re.I)
TEXT = re.compile(r'>([^<>]+)<')
ATTR = re.compile(r'\b(placeholder|title|aria-label|alt)="([^"{}<>]*)"')
LETTERS = re.compile(r'[A-Za-zÀ-ÿ]{2,}')


def wanted(txt):
    t = txt.strip()
    if not t or '{{' in t or '{%' in t or '}}' in t or '%}' in t:
        return False
    if not LETTERS.search(t):
        return False
    if re.fullmatch(r'[\w.-]+\.(py|json|sh|html|db|mp3|local)', t):
        return False
    if t in ('BL-RMO', 'RMO', 'GitHub', 'FM', 'DAB+', 'RDS', 'SNMP', 'MHz', 'dBf', 'dBFS', 'kb/s', 'CSV', 'MP3', 'ZIP', 'OK', 'EId', 'PI', 'PS'):
        return False
    return True


def jinja(t):
    q = '"' if "'" in t and '"' not in t else "'"
    t2 = t.replace('\\', '\\\\')
    if q == "'":
        t2 = t2.replace("'", "\\'")
    return '{{ _(' + q + t2 + q + ') }}'


def wrap(src, keys):
    out, pos = [], 0
    for m in SKIP_BLOCKS.finditer(src):
        out.append(_wrap_part(src[pos:m.start()], keys))
        out.append(m.group(0))
        pos = m.end()
    out.append(_wrap_part(src[pos:], keys))
    return ''.join(out)


def _wrap_part(part, keys):
    # Ne touche pas aux balises Jinja de contrôle ({% ... %}) ni aux expressions
    def text(m):
        raw = m.group(1)
        if not wanted(raw):
            return m.group(0)
        lead = raw[:len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        t = ' '.join(raw.split())
        keys.add(t)
        return '>' + lead + jinja(t) + trail + '<'

    def attr(m):
        if not wanted(m.group(2)):
            return m.group(0)
        keys.add(m.group(2))
        return f'{m.group(1)}="{jinja(m.group(2))}"'

    return ATTR.sub(attr, TEXT.sub(text, part))


def main():
    args = sys.argv[1:]
    only_keys = '--keys' in args
    files = [a for a in args if not a.startswith('--')]
    keys = set()
    for f in files:
        src = open(f, encoding='utf-8').read()
        new = wrap(src, keys)
        if not only_keys and new != src:
            open(f, 'w', encoding='utf-8').write(new)
    if only_keys:
        print(json.dumps(sorted(keys), ensure_ascii=False, indent=1))
    else:
        print(f'{len(keys)} textes', file=sys.stderr)


if __name__ == '__main__':
    main()
