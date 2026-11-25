"""Agent BL-RMO : surveillance d'un PC Windows du parc.

Installé par setup-agent-bl-rmo.exe, lancé au démarrage du PC par la tâche planifiée
« BL-RMO Agent » (compte SYSTEM). Toutes les minutes, il relève ce que BL-RMO lui
demande et l'envoie à POST /api/agent/report avec le jeton de ce PC. La réponse
donne la liste des modules à relever : on les choisit dans BL-RMO (Parc › Appareils),
sans revenir sur le PC.

Modules :
  system  allumé depuis, processeur (moyenne sur la minute), mémoire, disques fixes
  dante   appareils, abonnements, horloge et niveaux du réseau Dante, lus par netaudio
          (« status », « clock » et « meter --snapshot »), sans rien modifier

Interface Dante : si BL-RMO en donne une (adresse du PC sur le réseau Dante), netaudio
n'écoute que celle-là ; les appareils apparaissent alors avec leur adresse Dante.

Mise à jour : quand un administrateur la demande dans BL-RMO, la réponse donne l'adresse
de l'installeur sur GitHub et son empreinte SHA-256. L'agent le télécharge, vérifie
l'empreinte et le lance en mode silencieux par une tâche planifiée à part (« BL-RMO Agent
Update »), qui survit à l'arrêt de l'agent pendant l'installation.

Rien n'écoute sur le PC : l'agent ne fait qu'envoyer. netaudio lance le temps d'une
lecture son moteur privé, qui s'arrête aussitôt après.

Commandes :
  agent-bl-rmo.exe run        boucle (lancée par la tâche planifiée)
  agent-bl-rmo.exe test       un envoi ; code de sortie 0 reçu, 2 BL-RMO injoignable,
                              3 jeton refusé, 4 réglages absents, 5 autre refus
  agent-bl-rmo.exe --version
  agent-bl-rmo.exe netaudio … usage interne : la commande netaudio, embarquée

Réglages : %ProgramData%\\BL-RMO\\agent\\agent.json  {"url": "https://…", "token": "…"}
Journal :  %ProgramData%\\BL-RMO\\agent\\agent.log
"""
import gzip
import hashlib
import json
import logging
import logging.handlers
import os
import platform
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

VERSION = '1.2.0'
DATA_DIR = os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'BL-RMO', 'agent')
CONFIG = os.path.join(DATA_DIR, 'agent.json')
LOG = os.path.join(DATA_DIR, 'agent.log')
INTERVAL = 60              # s entre deux envois (BL-RMO peut le changer)
NETAUDIO_TIMEOUT = 50      # une lecture prend une dizaine de secondes
READS = {'status': ['--output', 'json', 'status'], 'clock': ['--output', 'json', 'clock'],
         # niveaux : instantané, mesure détaillée demandée aux appareils (comme Dante Controller)
         'meter': ['--output', 'json', 'meter', '--snapshot', '--detailed', '-t', '8']}

EXIT_OK, EXIT_UNREACHABLE, EXIT_TOKEN, EXIT_CONFIG, EXIT_REFUSED = 0, 2, 3, 4, 5

log = logging.getLogger('agent-bl-rmo')


class Refused(Exception):
    def __init__(self, code, message=''):
        super().__init__(message or f'HTTP {code}')
        self.code = code


def setup_log(console=False):
    log.setLevel(logging.INFO)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOG, maxBytes=1_000_000, backupCount=2, encoding='utf-8')
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(h)
    except OSError:
        pass
    if console and sys.stdout:
        log.addHandler(logging.StreamHandler(sys.stdout))


def load_config():
    with open(CONFIG, encoding='utf-8-sig') as f:
        cfg = json.load(f)
    if not str(cfg.get('url', '')).startswith(('http://', 'https://')) or not cfg.get('token'):
        raise ValueError('url et token requis dans ' + CONFIG)
    return cfg


def own_command():
    """Commande pour relancer cet agent : l'exe (version installée) ou python + ce script."""
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]


# ── Modules ──────────────────────────────────────────────────────────────────
class SystemModule:
    """Ce que BL-RMO relevait en SNMP (HOST-RESOURCES-MIB), lu directement."""

    def __init__(self):
        import psutil
        self.psutil = psutil
        psutil.cpu_percent(None)             # point de départ : la valeur suivante est la moyenne depuis
        self.cpu_since = time.time()

    def read(self):
        ps = self.psutil
        if time.time() - self.cpu_since < 2:
            time.sleep(1)                    # premier relevé : au moins une seconde de mesure
        cpu = ps.cpu_percent(None)
        self.cpu_since = time.time()
        disks = []
        for p in ps.disk_partitions(all=False):
            if os.name == 'nt' and 'fixed' not in p.opts.split(','):
                continue                     # lecteurs réseau, amovibles, CD
            if os.name != 'nt' and not p.device.startswith('/dev/'):
                continue
            try:
                u = ps.disk_usage(p.mountpoint)
            except OSError:
                continue
            if not u.total:
                continue
            label = p.mountpoint.rstrip('\\') if os.name == 'nt' else p.mountpoint
            disks.append({'label': label, 'size': u.total, 'used': u.used,
                          'pct': round(100.0 * u.used / u.total, 1)})
        disks.sort(key=lambda d: d['label'])
        return {'sysname': socket.gethostname(), 'uptime': round(time.time() - ps.boot_time()),
                'cpu': round(cpu, 1), 'cores': ps.cpu_count() or 0,
                'ram_pct': round(ps.virtual_memory().percent, 1), 'disks': disks}


def netaudio_read(name, interface=''):
    """Une lecture netaudio, dans un processus à part : son moteur privé s'arrête avec lui."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    scope = ['--interface', interface] if interface else []
    try:
        r = subprocess.run(own_command() + ['netaudio'] + scope + READS[name], capture_output=True,
                           timeout=NETAUDIO_TIMEOUT, creationflags=flags, env=env)
    except subprocess.TimeoutExpired:
        return None, f'{name} : pas de réponse en {NETAUDIO_TIMEOUT} s'
    out = r.stdout.decode('utf-8', 'replace').strip()
    if r.returncode != 0 or not out:
        err = r.stderr.decode('utf-8', 'replace').strip().splitlines()
        return None, f'{name} : code {r.returncode} ' + (err[-1][:200] if err else '')
    try:
        return json.loads(out), None
    except ValueError:
        return None, f'{name} : réponse illisible'


def dante_read(interface=''):
    t0 = time.time()
    status, e1 = netaudio_read('status', interface)
    clock, e2 = netaudio_read('clock', interface)
    meter, e3 = netaudio_read('meter', interface)
    return {'status': status, 'clock': clock, 'meter': meter, 'errors': [e for e in (e1, e2, e3) if e],
            'duration': round(time.time() - t0, 1), 'netaudio': netaudio_version(), 'interface': interface}


def netaudio_version():
    try:
        from importlib.metadata import version
        return version('netaudio')
    except Exception:
        return '?'


# ── Envoi ────────────────────────────────────────────────────────────────────
def send(cfg, payload):
    body = gzip.compress(json.dumps(payload).encode('utf-8'))
    url = cfg['url'].rstrip('/') + '/api/agent/report'
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'Content-Encoding': 'gzip',
        'X-Agent-Token': cfg['token'], 'User-Agent': f'agent-bl-rmo/{VERSION}'})
    # Certificat local de BL-RMO (mkcert) : inconnu de Windows, d'où la vérification
    # désactivée par défaut ; l'échange reste chiffré et limité au réseau du studio.
    ctx = ssl.create_default_context()
    if not cfg.get('verify_tls', False):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode('utf-8')).get('message', '')
        except Exception:
            msg = ''
        raise Refused(e.code, msg) from None


def agent_info():
    return {'host': socket.gethostname(), 'version': VERSION, 'python': platform.python_version(),
            'os': f'{platform.system()} {platform.release()} ({platform.version()})'}


# ── Mise à jour demandée depuis BL-RMO ───────────────────────────────────────
UPDATE_DIR = os.path.join(DATA_DIR, 'update')
UPDATE_TASK = 'BL-RMO Agent Update'
UPDATE_RETRY = 3600          # s avant de retenter une mise à jour qui a échoué


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split('.'))
    except ValueError:
        return (0,)


def powershell(script, timeout=60):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-Command', script], capture_output=True, timeout=timeout, creationflags=flags)


def cleanup_update():
    """Au démarrage : retire la tâche et l'installeur d'une mise à jour terminée."""
    if os.name != 'nt':
        return
    try:
        powershell(f"$t = Get-ScheduledTask -TaskName '{UPDATE_TASK}' -ErrorAction SilentlyContinue; "
                   f"if ($t -and $t.State -ne 'Running') {{ Unregister-ScheduledTask -TaskName '{UPDATE_TASK}' -Confirm:$false }}")
        for f in os.listdir(UPDATE_DIR) if os.path.isdir(UPDATE_DIR) else []:
            if f.endswith('.exe'):
                os.remove(os.path.join(UPDATE_DIR, f))
    except Exception as e:
        log.warning(f'Nettoyage de la mise à jour : {e}')


def download(url, dest, timeout=300):
    ctx = ssl.create_default_context()          # GitHub : certificat vérifié
    req = urllib.request.Request(url, headers={'User-Agent': f'agent-bl-rmo/{VERSION}'})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp, open(dest + '.part', 'wb') as out:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    os.replace(dest + '.part', dest)
    return h.hexdigest()


def install_update(update):
    """Télécharge, vérifie et lance l'installeur. Renvoie un texte d'état pour BL-RMO."""
    version, url, sha = update.get('version'), update.get('url', ''), (update.get('sha256') or '').lower()
    if not url.startswith('https://github.com/') or len(sha) != 64:
        return f'mise à jour {version} refusée : adresse ou empreinte invalide'
    os.makedirs(UPDATE_DIR, exist_ok=True)
    dest = os.path.join(UPDATE_DIR, f'setup-agent-bl-rmo-{version}.exe')
    log.info(f'Mise à jour {version} : téléchargement de {url}')
    got = download(url, dest)
    if got != sha:
        os.remove(dest)
        return f'mise à jour {version} refusée : empreinte différente'
    if os.name != 'nt':
        return f'mise à jour {version} téléchargée (installation possible sous Windows seulement)'
    logfile = os.path.join(UPDATE_DIR, f'setup-{version}.log')
    args = f'/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG={logfile}'      # chemin sans espace (ProgramData)
    r = powershell(f"$a = New-ScheduledTaskAction -Execute '{dest}' -Argument '{args}'; "
                   f"Register-ScheduledTask -TaskName '{UPDATE_TASK}' -Action $a -User 'SYSTEM' -RunLevel Highest -Force | Out-Null; "
                   f"Start-ScheduledTask -TaskName '{UPDATE_TASK}'")
    if r.returncode != 0:
        return f"mise à jour {version} : lancement impossible ({r.stderr.decode('utf-8', 'replace').strip()[-200:]})"
    log.info(f'Mise à jour {version} : installeur lancé, l\'agent va redémarrer')
    return f'installation de la version {version} lancée'



class Agent:
    def __init__(self):
        self.modules = ['system']
        self.interval = INTERVAL
        self.system = None
        self.dante_interface = ''
        self.update_state = ''         # dernier état de mise à jour, renvoyé à BL-RMO
        self.update_tried = {}         # version -> heure de la dernière tentative

    def collect(self):
        data, errors = {}, []
        if 'system' in self.modules:
            try:
                self.system = self.system or SystemModule()
                data['system'] = self.system.read()
            except Exception as e:
                errors.append(f'system : {e}')
        if 'dante' in self.modules:
            try:
                data['dante'] = dante_read(self.dante_interface)
            except Exception as e:
                errors.append(f'dante : {e}')
        info = agent_info()
        if self.update_state:
            info['update'] = self.update_state
        return {'agent': info, 'ts': time.time(), 'modules': data, 'errors': errors}

    def apply(self, resp):
        """Réglages renvoyés par BL-RMO. Vrai si un module vient d'être ajouté."""
        conf = (resp or {}).get('config') or {}
        mods = [m for m in conf.get('modules') or [] if m in ('system', 'dante')]
        added = set(mods) - set(self.modules)
        if mods and mods != self.modules:
            log.info(f"Modules demandés par BL-RMO : {', '.join(mods)}")
            self.modules = mods
        try:
            self.interval = max(30, min(600, int(conf.get('interval') or INTERVAL)))
        except (TypeError, ValueError):
            pass
        iface = str(conf.get('dante_interface') or '').strip()
        if iface != self.dante_interface:
            log.info(f"Interface Dante : {iface or 'toutes les interfaces'}")
            self.dante_interface = iface
            added = added or 'dante' in self.modules
        update = conf.get('update')
        if isinstance(update, dict) and version_tuple(update.get('version')) > version_tuple(VERSION):
            v = update.get('version')
            if time.time() - self.update_tried.get(v, 0) > UPDATE_RETRY:
                self.update_tried[v] = time.time()
                try:
                    self.update_state = install_update(update)
                except Exception as e:
                    self.update_state = f'mise à jour {v} : échec ({e})'
                log.info(self.update_state)
        return bool(added)

    def cycle(self, cfg):
        payload = self.collect()
        resp = send(cfg, payload)
        mods = payload['modules']
        n = len(((mods.get('dante') or {}).get('status') or {}).get('dante') or {})
        log.info(f"Relevé envoyé à {resp.get('device', 'BL-RMO')} ({', '.join(mods) or 'aucun module'}"
                 + (f', {n} appareil(s) Dante' if 'dante' in mods else '') + ')'
                 + (f" ; erreurs : {payload['errors'] + (mods.get('dante') or {}).get('errors', [])}"
                    if payload['errors'] or (mods.get('dante') or {}).get('errors') else ''))
        return self.apply(resp)

    def run(self):
        log.info(f'Agent BL-RMO {VERSION} démarré (Python {platform.python_version()})')
        cleanup_update()
        while True:
            start, again = time.time(), False
            try:
                again = self.cycle(load_config())
            except Refused as e:
                log.warning(f'BL-RMO a refusé le relevé : HTTP {e.code}'
                            + (' (jeton refusé : relancer l\'installation avec le bon jeton)' if e.code == 403 else '')
                            + (f' ; {e}' if str(e) and str(e) != f'HTTP {e.code}' else ''))
            except (urllib.error.URLError, OSError) as e:
                log.warning(f'BL-RMO injoignable : {getattr(e, "reason", e)}')
            except Exception as e:
                log.error(f'Relevé impossible : {e}')
            if not again:
                time.sleep(max(5, self.interval - (time.time() - start)))


def test():
    """Un envoi (module système seul, rapide) : utilisé par l'installeur."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f'Réglages absents ou invalides : {e}')
        return EXIT_CONFIG
    try:
        resp = send(cfg, Agent().collect())
    except Refused as e:
        print(f'BL-RMO a refusé le relevé : HTTP {e.code} {e}')
        return EXIT_TOKEN if e.code == 403 else EXIT_REFUSED
    except Exception as e:
        print(f'BL-RMO injoignable : {getattr(e, "reason", e)}')
        return EXIT_UNREACHABLE
    print(f"Relevé reçu par BL-RMO ({resp.get('device', '')})")
    return EXIT_OK


def run_netaudio(args):
    """Commande netaudio embarquée : agent-bl-rmo.exe netaudio --output json status."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except Exception:
            pass
    sys.argv = ['netaudio'] + args
    from netaudio.cli import main as netaudio_main
    netaudio_main()


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ''
    if cmd == 'netaudio':
        return run_netaudio(args[1:])
    if cmd in ('--version', 'version'):
        print(f'Agent BL-RMO {VERSION}')
        return 0
    if cmd == 'test':
        setup_log()
        code = test()
        log.info(f'Test d\'envoi : code {code}')
        return code
    if cmd == 'run':
        setup_log(console=False)
        Agent().run()
        return 0
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
