"""Sonde Dante de BL-RMO, pour un PC Windows qui voit le réseau Dante.

Toutes les 60 s, lit le réseau Dante avec netaudio, en lecture seule (commandes
« status » et « clock ») et envoie le résultat à BL-RMO, qui l'affiche dans sa page Dante.

- Aucune commande de modification : la sonde ne peut ni changer une route, ni
  redémarrer un appareil, ni toucher à l'horloge.
- Rien n'écoute sur le PC : pas de port ouvert, pas de service netaudio lancé.
  netaudio démarre un moteur temporaire le temps de chaque lecture.
- Seule la bibliothèque standard de Python est utilisée, plus netaudio.

Réglages dans sonde-dante.json, à côté de ce fichier (écrit par installer-sonde-dante.ps1) :
  {"url": "https://192.168.1.30", "token": "..."}

Lancement à la main, pour essayer :  python sonde-dante.py --once
"""
import gzip
import json
import logging
import logging.handlers
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

VERSION = '1'
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, 'sonde-dante.json')
LOG = os.path.join(HERE, 'sonde-dante.log')
INTERVAL = 60          # secondes entre deux relevés
CMD_TIMEOUT = 50       # une lecture prend une dizaine de secondes sans service netaudio

# Commandes autorisées : uniquement des lectures
READS = {
    'status': ['--output', 'json', 'status'],
    'clock': ['--output', 'json', 'clock'],
}

log = logging.getLogger('sonde-dante')


def setup_log():
    log.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(LOG, maxBytes=500_000, backupCount=2, encoding='utf-8')
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(h)
    log.addHandler(logging.StreamHandler(sys.stdout))


def load_config():
    with open(CONFIG, encoding='utf-8-sig') as f:
        cfg = json.load(f)
    if not cfg.get('url') or not cfg.get('token'):
        raise ValueError('url et token requis dans sonde-dante.json')
    return cfg


def netaudio_version():
    try:
        from importlib.metadata import version
        return version('netaudio')
    except Exception:
        return '?'


def read(name):
    """Lance une commande de lecture de netaudio et renvoie son JSON (ou l'erreur)."""
    cmd = [sys.executable, '-m', 'netaudio'] + READS[name]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=CMD_TIMEOUT, creationflags=flags)
    except subprocess.TimeoutExpired:
        return None, f'{name} : pas de réponse en {CMD_TIMEOUT} s'
    out = r.stdout.decode('utf-8', 'replace').strip()
    if r.returncode != 0 or not out:
        err = r.stderr.decode('utf-8', 'replace').strip().splitlines()
        return None, f'{name} : code {r.returncode} ' + (err[-1] if err else '')
    try:
        return json.loads(out), None
    except ValueError:
        return None, f'{name} : réponse illisible'


def send(cfg, payload):
    body = gzip.compress(json.dumps(payload).encode('utf-8'))
    url = cfg['url'].rstrip('/') + '/api/dante/report'
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Content-Encoding': 'gzip',
        'X-Dante-Token': cfg['token'], 'User-Agent': f'bl-rmo-sonde-dante/{VERSION}'})
    # Certificat local de BL-RMO (mkcert) : non reconnu par Windows, d'où la vérification
    # désactivée par défaut ; l'échange reste chiffré et limité au réseau du studio.
    ctx = ssl.create_default_context()
    if not cfg.get('verify_tls', False):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        return resp.status


def cycle(cfg):
    t0 = time.time()
    status, err1 = read('status')
    clock, err2 = read('clock')
    payload = {
        'probe': {'host': socket.gethostname(), 'version': VERSION, 'netaudio': netaudio_version(),
                  'python': sys.version.split()[0]},
        'ts': time.time(), 'duration': round(time.time() - t0, 1),
        'status': status, 'clock': clock, 'errors': [e for e in (err1, err2) if e],
    }
    try:
        code = send(cfg, payload)
        n = len((status or {}).get('dante') or {})
        log.info(f'Relevé envoyé ({n} appareil(s), {payload["duration"]} s, HTTP {code})'
                 + (f' ; erreurs : {payload["errors"]}' if payload['errors'] else ''))
    except urllib.error.HTTPError as e:
        log.warning(f'BL-RMO a refusé le relevé : HTTP {e.code}' + (' (jeton refusé)' if e.code == 403 else ''))
    except Exception as e:
        log.warning(f'BL-RMO injoignable : {e}')


def main():
    setup_log()
    once = '--once' in sys.argv
    log.info(f'Sonde Dante {VERSION} démarrée (netaudio {netaudio_version()}, Python {sys.version.split()[0]})')
    while True:
        start = time.time()
        try:
            cycle(load_config())
        except Exception as e:
            log.error(f'Relevé impossible : {e}')
        if once:
            return
        time.sleep(max(5, INTERVAL - (time.time() - start)))


if __name__ == '__main__':
    main()
