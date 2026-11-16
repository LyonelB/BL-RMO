"""Récepteurs USB branchés : TEF (FM), dongle Si468x (DAB+), clés RTL-SDR.

Lecture seule de /sys : aucun port n'est ouvert, rien n'est dérangé (welle-cli
garde sa clé, le TEF son port série). Chaque appareil est identifié par son
VID:PID et désigné par un chemin stable (/dev/serial/by-id/...) plutôt que par
/dev/ttyACM0, dont l'ordre peut s'inverser au redémarrage quand le TEF et le
dongle DAB+ sont branchés ensemble.
"""
import glob
import os

TEF_IDS = {'1209:6687'}                       # FMDX.org TEF668X Headless (FM-DX-Tuner)
SI468X_IDS = {'1209:468f'}                    # Si468X Headless DAB (mc_popa)
RTLSDR_IDS = {'0bda:2838', '0bda:2832'}       # Realtek RTL2832U/RTL2838 (clés RTL-SDR)

SYS = '/sys'
DEV = '/dev'
PROC_ASOUND = '/proc/asound'


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _usb_device_dir(path):
    """Remonte depuis un nœud de /sys jusqu'au périphérique USB (celui qui a idVendor)."""
    p = os.path.realpath(path)
    while p and p != '/':
        if os.path.isfile(os.path.join(p, 'idVendor')):
            return p
        p = os.path.dirname(p)
    return None


def _usb_info(d):
    return {
        'vidpid': f"{_read(os.path.join(d, 'idVendor'))}:{_read(os.path.join(d, 'idProduct'))}".lower(),
        'manufacturer': _read(os.path.join(d, 'manufacturer')),
        'product': _read(os.path.join(d, 'product')),
        'serial': _read(os.path.join(d, 'serial')),
        'usb_path': os.path.basename(d),
    }


def _by_id_links():
    """{/dev/ttyACM0: /dev/serial/by-id/usb-...}"""
    out = {}
    for link in glob.glob(os.path.join(DEV, 'serial', 'by-id', '*')):
        out.setdefault(os.path.realpath(link), link)
    return out


def _alsa_cards():
    """{dossier USB: 'hw:DAB'} pour chaque carte son USB."""
    out = {}
    for card in glob.glob(os.path.join(SYS, 'class', 'sound', 'card*')):
        d = _usb_device_dir(os.path.join(card, 'device'))
        if not d:
            continue
        num = os.path.basename(card)[4:]
        cid = _read(os.path.join(PROC_ASOUND, f'card{num}', 'id'))
        out[d] = f'hw:{cid}' if cid else f'hw:{num}'
    return out


def _kind(vidpid):
    if vidpid in TEF_IDS:
        return 'tef'
    if vidpid in SI468X_IDS:
        return 'si468x'
    if vidpid in RTLSDR_IDS:
        return 'rtlsdr'
    return None


def list_receivers():
    """Liste des récepteurs reconnus, triée par type."""
    by_id, cards = _by_id_links(), _alsa_cards()
    found = {}
    # Ports série (TEF, dongle DAB+)
    for tty in sorted(glob.glob(os.path.join(SYS, 'class', 'tty', 'ttyACM*'))
                      + glob.glob(os.path.join(SYS, 'class', 'tty', 'ttyUSB*'))):
        d = _usb_device_dir(os.path.join(tty, 'device'))
        if not d:
            continue
        info = _usb_info(d)
        kind = _kind(info['vidpid'])
        if not kind:
            continue
        node = os.path.join(DEV, os.path.basename(tty))
        rec = found.setdefault(d, {**info, 'kind': kind, 'tty': node, 'port': by_id.get(node, node),
                                   'card': cards.get(d)})
        rec.setdefault('tty', node)
    # Clés RTL-SDR (pas de port série)
    for d in glob.glob(os.path.join(SYS, 'bus', 'usb', 'devices', '*')):
        if not os.path.isfile(os.path.join(d, 'idVendor')):
            continue
        d = os.path.realpath(d)
        info = _usb_info(d)
        if _kind(info['vidpid']) == 'rtlsdr' and d not in found:
            found[d] = {**info, 'kind': 'rtlsdr', 'tty': None, 'port': None, 'card': None}
    order = {'tef': 0, 'si468x': 1, 'rtlsdr': 2}
    return sorted(found.values(), key=lambda r: (order[r['kind']], r['usb_path']))


def resolve_tef_port(configured):
    """Port à ouvrir pour le TEF. Un /dev/ttyACMx configuré qui désigne en fait un autre
    appareil (dongle DAB+ branché à côté, ordre inversé au redémarrage) est remplacé par
    le port stable du TEF. Sans TEF reconnu, le port configuré est gardé tel quel, sauf
    s'il désigne le dongle DAB+ : None (pas de TEF à ouvrir)."""
    try:
        recs = list_receivers()
    except Exception:
        return configured
    tefs = [r for r in recs if r['kind'] == 'tef']
    real = os.path.realpath(configured or '')
    if not tefs:
        # TEF débranché : ne jamais envoyer de commandes FM au dongle DAB+
        if any(r['tty'] and real == os.path.realpath(r['tty']) for r in recs if r['kind'] == 'si468x'):
            return None
        return configured
    if any(real == os.path.realpath(t['tty']) for t in tefs):
        return configured
    return tefs[0]['port']
