"""Traduction de l'interface et des mails de BL-RMO.

Le français est la langue source : chaque texte de l'interface est écrit en français
et sert de clé. i18n/en.json donne sa traduction anglaise ; un texte absent du
dictionnaire reste en français (rien ne casse quand une traduction manque).

La langue est un réglage de tout BL-RMO : config.json, "ui": {"language": "fr" | "en"},
réglée dans Système, onglet Général. Les mails suivent la même langue.

  Jinja      : {{ _('Tableau de bord') }}, {{ _('Il y a {n} min', n=5) }}
  JavaScript : _t('Tableau de bord'), _t('Il y a {n} min', { n: 5 })   (templates/_i18n.html)
  Python     : from i18n import t ; t('Signal FM non capté')

Textes composés ailleurs (mails, messages des sondes) : tr() les traduit ligne par ligne,
par clé exacte ou par modèle. Les modèles sont dans la section "_patterns" de i18n/en.json :
« RDS absent depuis {n}s » traduit « RDS absent depuis 120s », et chaque valeur reprise
est à son tour traduite si elle est connue (« {a} : {b} » traduit « Service : GRAFFITI »).
"""
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, 'config.json')
DICT_DIR = os.path.join(BASE, 'i18n')
LANGUAGES = {'fr': 'Français', 'en': 'English'}
DEFAULT = 'fr'

_cache = {'cfg_mtime': None, 'lang': DEFAULT, 'dicts': {}, 'patterns': {}}


def language():
    """Langue réglée dans config.json, relue seulement quand le fichier change."""
    try:
        m = os.path.getmtime(CONFIG_FILE)
    except OSError:
        return _cache['lang']
    if m != _cache['cfg_mtime']:
        _cache['cfg_mtime'] = m
        try:
            with open(CONFIG_FILE) as f:
                lang = ((json.load(f).get('ui') or {}).get('language') or DEFAULT).lower()
        except Exception:
            lang = _cache['lang']
        _cache['lang'] = lang if lang in LANGUAGES else DEFAULT
    return _cache['lang']


def dictionary(lang=None):
    """{texte français: traduction} de la langue (vide pour le français)."""
    lang = lang or language()
    if lang == DEFAULT:
        return {}
    path = os.path.join(DICT_DIR, f'{lang}.json')
    try:
        m = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _cache['dicts'].get(lang)
    if cached and cached[0] == m:
        return cached[1]
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        d = {k: v for k, v in raw.items() if isinstance(v, str) and v and not k.startswith('_')}
        pats = {k: v for k, v in (raw.get('_patterns') or {}).items() if isinstance(v, str) and v}
    except Exception:
        d, pats = (cached[1], cached[2]) if cached else ({}, {})
    _cache['dicts'][lang] = (m, d, pats)
    return d


def pattern_dict(lang=None):
    """Section "_patterns" de la langue : {modèle français: modèle traduit}."""
    lang = lang or language()
    if lang == DEFAULT:
        return {}
    dictionary(lang)
    cached = _cache['dicts'].get(lang)
    return cached[2] if cached else {}


def t(text, lang=None, **values):
    """Traduit un texte français ; {nom} est remplacé par values['nom']."""
    out = dictionary(lang).get(text, text)
    if values:
        try:
            out = out.format(**values)
        except (KeyError, IndexError, ValueError):
            pass
    return out


def _patterns(lang):
    """Modèles tirés des clés à valeurs, les plus précis d'abord (plus de texte fixe)."""
    d = pattern_dict(lang)
    cached = _cache['patterns'].get(lang)
    if cached and cached[0] is d:
        return cached[1]
    out = []
    for k, v in d.items():
        names = re.findall(r'\{(\w+)\}', k)
        if not names or len(set(names)) != len(names):
            continue
        rx = '^' + re.sub(r'\\\{(\w+)\\\}', r'(?P<\1>.+?)', re.escape(k)) + '$'
        try:
            fixed = re.sub(r'\{\w+\}', '', k)
            # Ordre : modèles qui commencent par du texte fixe (« DNS invalide : {d} »), puis
            # la coupure à l'étiquette (« {a} : {b} »), puis les autres, les plus longs d'abord
            rank = 1000 if fixed.strip() in (':', '·') else len(fixed) + (2000 if not k.startswith('{') else 0)
            out.append((rank, re.compile(rx, re.S), v))
        except re.error:
            continue
    out.sort(key=lambda x: -x[0])
    _cache['patterns'][lang] = (d, out)
    return out


def tr_line(text, lang=None, depth=0):
    """Traduit une ligne : clé exacte, sinon premier modèle qui correspond."""
    lang = lang or language()
    d = dictionary(lang)
    if not d or not text:
        return text
    core = text.strip()
    if core in d:
        return text.replace(core, d[core])
    if depth > 2:
        return text
    for _, rx, v in _patterns(lang):
        m = rx.match(core)
        if m:
            vals = {k: tr_line(x, lang, depth + 1) for k, x in m.groupdict().items()}
            try:
                return text.replace(core, v.format(**vals))
            except (KeyError, IndexError, ValueError):
                continue
    return text


def tr(text, lang=None):
    """Traduit un texte de plusieurs lignes (mails, messages des sondes)."""
    lang = lang or language()
    if lang == DEFAULT or not text:
        return text
    return '\n'.join(tr_line(l, lang) for l in str(text).split('\n'))
