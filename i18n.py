"""Traduction de l'interface et des mails de BL-RMO.

Le français est la langue source : chaque texte de l'interface est écrit en français
et sert de clé. i18n/en.json donne sa traduction anglaise ; un texte absent du
dictionnaire reste en français (rien ne casse quand une traduction manque).

La langue est un réglage de tout BL-RMO : config.json, "ui": {"language": "fr" | "en"},
réglée dans Système, onglet Général. Les mails suivent la même langue.

  Jinja      : {{ _('Tableau de bord') }}, {{ _('Il y a {n} min', n=5) }}
  JavaScript : _t('Tableau de bord'), _t('Il y a {n} min', { n: 5 })   (templates/_i18n.html)
  Python     : from i18n import t ; t('Signal FM non capté')
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, 'config.json')
DICT_DIR = os.path.join(BASE, 'i18n')
LANGUAGES = {'fr': 'Français', 'en': 'English'}
DEFAULT = 'fr'

_cache = {'cfg_mtime': None, 'lang': DEFAULT, 'dicts': {}}


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
            d = {k: v for k, v in json.load(f).items() if isinstance(v, str) and v and not k.startswith('_')}
    except Exception:
        d = cached[1] if cached else {}
    _cache['dicts'][lang] = (m, d)
    return d


def t(text, lang=None, **values):
    """Traduit un texte français ; {nom} est remplacé par values['nom']."""
    out = dictionary(lang).get(text, text)
    if values:
        try:
            out = out.format(**values)
        except (KeyError, IndexError, ValueError):
            pass
    return out
