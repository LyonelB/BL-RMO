#!/usr/bin/env python3
"""
snmp_traps.py : décodage et traduction des traps SNMP (v1 et v2c).

Utilisé par syslog_server.py, qui écoute aussi le port UDP 162 : chaque trap
reçu devient une ligne des journaux (programme « trap ») et déclenche un
relevé immédiat de l'appareil dans la chaîne de diffusion.

Les traps SNMPv3 (chiffrés) ne sont pas décodés : régler les appareils en v2c
pour l'envoi des traps (la lecture SNMP, elle, peut rester en v3).
"""
import re

# ════════════════════════════════════════════════════════════════════
#  Décodage BER (sous-ensemble utilisé par SNMP)
# ════════════════════════════════════════════════════════════════════

class TrapError(ValueError):
    pass


def _tlv(buf, i):
    """Lit un élément BER à la position i : (tag, valeur, position suivante)."""
    if i + 2 > len(buf):
        raise TrapError('paquet tronqué')
    tag = buf[i]
    n = buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7f
        if k == 0 or k > 4 or i + k > len(buf):
            raise TrapError('longueur invalide')
        n = int.from_bytes(buf[i:i + k], 'big')
        i += k
    if i + n > len(buf):
        raise TrapError('paquet tronqué')
    return tag, buf[i:i + n], i + n


def _children(buf):
    out, i = [], 0
    while i < len(buf):
        tag, val, i = _tlv(buf, i)
        out.append((tag, val))
    return out


def _int(b):
    return int.from_bytes(b, 'big', signed=True) if b else 0


def _uint(b):
    return int.from_bytes(b, 'big', signed=False) if b else 0


def _oid(b):
    if not b:
        return ''
    parts = [b[0] // 40, b[0] % 40]
    v = 0
    for c in b[1:]:
        v = (v << 7) | (c & 0x7f)
        if not c & 0x80:
            parts.append(v)
            v = 0
    return '.' + '.'.join(map(str, parts))


def _value(tag, b):
    """Valeur d'un varbind en texte lisible."""
    if tag == 0x02:
        return _int(b)
    if tag == 0x04:
        try:
            s = b.decode('utf-8')
            if s.isprintable() or not s:
                return s
        except UnicodeDecodeError:
            pass
        return b.hex(':')
    if tag == 0x05:
        return ''
    if tag == 0x06:
        return _oid(b)
    if tag == 0x40 and len(b) == 4:
        return '.'.join(str(x) for x in b)
    if tag in (0x41, 0x42, 0x43, 0x46):       # Counter32, Gauge32, TimeTicks, Counter64
        return _uint(b)
    if tag in (0x80, 0x81, 0x82):
        return '(absent)'
    return b.hex()


SYSUPTIME = '.1.3.6.1.2.1.1.3.0'
SNMPTRAPOID = '.1.3.6.1.6.3.1.1.4.1.0'
GENERIC_V1 = ['coldStart', 'warmStart', 'linkDown', 'linkUp', 'authenticationFailure', 'egpNeighborLoss']


def decode(data):
    """Décode un trap v1 ou v2c (ou un inform v2c).

    Renvoie {'version', 'community', 'trap_oid', 'varbinds': [(oid, valeur)], 'inform', 'request_id'}.
    """
    tag, msg, _ = _tlv(data, 0)
    if tag != 0x30:
        raise TrapError('pas un message SNMP')
    parts = _children(msg)
    if len(parts) < 3 or parts[0][0] != 0x02:
        raise TrapError('message SNMP incomplet')
    version = _int(parts[0][1])
    if version == 3:
        raise TrapError('trap SNMPv3 (non décodé : régler l\'envoi des traps en v2c)')
    community = parts[1][1].decode('latin-1')
    pdu_tag, pdu = parts[2]
    fields = _children(pdu)
    varbinds = []

    if pdu_tag == 0xA4 and version == 0:
        # SNMPv1 : entreprise, adresse, trap générique, trap spécifique, horodatage, varbinds
        enterprise = _oid(fields[0][1])
        generic = _int(fields[2][1])
        specific = _int(fields[3][1])
        if generic == 6:
            trap_oid = f"{enterprise}.0.{specific}"
        else:
            trap_oid = f".1.3.6.1.6.3.1.1.5.{generic + 1}"
        vb_seq = fields[5][1] if len(fields) > 5 else b''
        request_id = None
    elif pdu_tag in (0xA7, 0xA6):
        request_id = _int(fields[0][1])
        vb_seq = fields[3][1] if len(fields) > 3 else b''
        trap_oid = ''
    else:
        raise TrapError(f'type de message SNMP non géré (0x{pdu_tag:02x})')

    for _t, vb in _children(vb_seq):
        kv = _children(vb)
        if len(kv) != 2 or kv[0][0] != 0x06:
            continue
        oid = _oid(kv[0][1])
        val = _value(kv[1][0], kv[1][1])
        if oid == SNMPTRAPOID:
            trap_oid = val
        elif oid != SYSUPTIME:
            varbinds.append((oid, val))
    return {'version': 'v1' if version == 0 else 'v2c', 'community': community, 'trap_oid': trap_oid,
            'varbinds': varbinds, 'inform': pdu_tag == 0xA6, 'request_id': request_id, 'raw_pdu': (version, community)}


def inform_response(data):
    """Réponse à un inform v2c (accusé de réception) : même message, PDU de type Response."""
    tag, msg, _ = _tlv(data, 0)
    parts = _children(msg)
    pdu_tag, pdu = parts[2]
    if pdu_tag != 0xA6:
        return None

    def enc(tag, payload):
        n = len(payload)
        if n < 0x80:
            ln = bytes([n])
        else:
            nb = n.to_bytes((n.bit_length() + 7) // 8, 'big')
            ln = bytes([0x80 | len(nb)]) + nb
        return bytes([tag]) + ln + payload

    body = b''.join(enc(t, v) for t, v in parts[:2]) + enc(0xA2, pdu)
    return enc(0x30, body)


# ════════════════════════════════════════════════════════════════════
#  Traduction (MIB des appareils de la radio)
# ════════════════════════════════════════════════════════════════════

DEVA_RX_SOURCE = {'3': 'Lecteur MP3', '4': 'RTP', '5': 'IP Audio 1', '6': 'IP Audio 2', '7': 'IP Audio 3'}
DEVA_TX_SOURCE = {'11': 'analogique', '12': 'numérique'}
DEVA_APPLIED = {'0': 'auto', '1': 'forcé (web)', '2': 'forcé (horaire)', '3': 'forcé (SNMP)'}
S4_VALID = {'0': 'absente', '1': 'OK'}
S4_STATE = {'0': 'inconnu', '1': 'nouveau', '2': 'démarrage', '3': 'chargement', '4': 'OK', '5': 'en panne',
            '6': 'pas de données', '7': 'mise à jour'}
S4_STREAM = {'0': 'arrêté', '1': 'erreur audio', '2': 'erreur de configuration', '3': 'erreur', '4': 'connexion',
             '5': 'en ligne', '6': 'surcharge réseau', '7': 'attente synchro', '8': 'resynchro', '9': 'pertes audio',
             '10': 'sans audio (arrêté)', '11': 'sans audio', '12': 'indisponible'}
S4_PSU = {'0': 'défaut', '1': 'OK'}

# OID du trap → (gravité syslog, texte, dictionnaire de valeurs pour la dernière variable ou None)
# Gravité : 3 erreur, 4 avertissement, 5 notice, 6 info
TRAPS = {
    # Deva DB91-RX (.35833.23.254.0.n)
    '.1.3.6.1.4.1.35833.23.254.0.1': (6, 'Trap de test', None),
    '.1.3.6.1.4.1.35833.23.254.0.2': (5, 'Changement de source', 'deva_rx_source'),
    '.1.3.6.1.4.1.35833.23.254.0.3': (3, 'Perte audio', 'deva_rx_source'),
    '.1.3.6.1.4.1.35833.23.254.0.4': (5, 'Retour audio', 'deva_rx_source'),
    # Deva DB91-TX (.35833.24.254.0.n)
    '.1.3.6.1.4.1.35833.24.254.0.1': (6, 'Trap de test', None),
    '.1.3.6.1.4.1.35833.24.254.0.2': (5, 'Changement de source', 'deva_tx_source'),
    '.1.3.6.1.4.1.35833.24.254.0.3': (3, "Perte de synchro de l'entrée numérique", 'deva_tx_source'),
    '.1.3.6.1.4.1.35833.24.254.0.4': (5, "Retour de synchro de l'entrée numérique", 'deva_tx_source'),
    '.1.3.6.1.4.1.35833.24.254.0.5': (3, 'Perte audio', 'deva_tx_source'),
    '.1.3.6.1.4.1.35833.24.254.0.6': (5, 'Retour audio', 'deva_tx_source'),
    # Sound4 (.29932.3.0.n)
    '.1.3.6.1.4.1.29932.3.0.1': (4, 'État du serveur', S4_STATE),
    '.1.3.6.1.4.1.29932.3.0.2': (4, "État d'une carte", S4_STATE),
    '.1.3.6.1.4.1.29932.3.0.3': (4, 'État Ethernet', None),
    '.1.3.6.1.4.1.29932.3.0.4': (4, 'État du traitement', S4_STATE),
    '.1.3.6.1.4.1.29932.3.0.5': (4, 'Alimentation 1', S4_PSU),
    '.1.3.6.1.4.1.29932.3.0.6': (4, 'Alimentation 2', S4_PSU),
    '.1.3.6.1.4.1.29932.3.0.7': (4, 'Alimentation interne', S4_PSU),
    '.1.3.6.1.4.1.29932.3.0.8': (5, 'État NTP', None),
    '.1.3.6.1.4.1.29932.3.0.9': (5, 'Source NTP', None),
    '.1.3.6.1.4.1.29932.3.0.10': (4, 'Entrée analogique', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.11': (4, 'Entrée AES', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.12': (4, 'Entrée PCI', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.13': (4, 'Entrée IP', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.14': (4, 'Changement d\'entrée', None),
    '.1.3.6.1.4.1.29932.3.0.15': (4, 'Entrée IP 2', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.16': (4, 'Entrée analogique auxiliaire', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.17': (4, 'Entrée AES auxiliaire', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.19': (4, 'Entrée LAN', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.20': (4, 'Entrée LAN 2', S4_VALID),
    '.1.3.6.1.4.1.29932.3.0.100': (5, 'Adresse IP Ethernet modifiée', None),
    '.1.3.6.1.4.1.29932.3.0.102': (4, 'État IP Ethernet', None),
    '.1.3.6.1.4.1.29932.3.0.110': (4, "État d'un encodeur IP", None),
    '.1.3.6.1.4.1.29932.3.0.111': (4, "État d'un décodeur IP", None),
    '.1.3.6.1.4.1.29932.3.0.112': (4, "État d'un lien IP", None),
    '.1.3.6.1.4.1.29932.3.0.200': (4, "État d'un canal de flux", S4_STREAM),
    '.1.3.6.1.4.1.29932.3.0.201': (5, 'Métadonnées de flux', None),
    '.1.3.6.1.4.1.29932.3.0.202': (4, "État d'un flux", S4_STREAM),
    '.1.3.6.1.4.1.29932.3.0.203': (5, 'Watermarking de flux', None),
    '.1.3.6.1.4.1.29932.3.0.204': (4, "Détection d'audio sur un flux", None),
    '.1.3.6.1.4.1.29932.3.0.220': (5, 'Canal de watermarking', None),
    # Traps génériques (RFC 1907)
    '.1.3.6.1.6.3.1.1.5.1': (5, 'Démarrage à froid (coldStart)', None),
    '.1.3.6.1.6.3.1.1.5.2': (5, 'Redémarrage (warmStart)', None),
    '.1.3.6.1.6.3.1.1.5.3': (4, 'Lien réseau coupé (linkDown)', None),
    '.1.3.6.1.6.3.1.1.5.4': (5, 'Lien réseau rétabli (linkUp)', None),
    '.1.3.6.1.6.3.1.1.5.5': (4, "Échec d'authentification SNMP", None),
}

# Variables connues des Deva (alias, source active, choix de source)
DEVA_VARS = {
    '.1.3.6.1.4.1.35833.23.2.6.1.0': 'alias', '.1.3.6.1.4.1.35833.24.2.6.1.0': 'alias',
    '.1.3.6.1.4.1.35833.23.3.1.0': 'source', '.1.3.6.1.4.1.35833.24.3.1.0': 'source',
    '.1.3.6.1.4.1.35833.23.3.2.0': 'applied', '.1.3.6.1.4.1.35833.24.3.2.0': 'applied',
}


def translate(trap):
    """(gravité, nom d'hôte annoncé, message en français) pour un trap décodé."""
    oid = trap['trap_oid']
    sev, text, values = TRAPS.get(oid, (5, None, None))
    vb = trap['varbinds']
    alias = ''
    details = []

    if oid.startswith('.1.3.6.1.4.1.35833.'):
        info = {}
        for o, v in vb:
            k = DEVA_VARS.get(o)
            if k:
                info[k] = str(v)
        alias = info.get('alias', '')
        names = DEVA_RX_SOURCE if values == 'deva_rx_source' else DEVA_TX_SOURCE
        if 'source' in info:
            details.append(f"source : {names.get(info['source'], info['source'])}")
        if 'applied' in info:
            details.append(DEVA_APPLIED.get(info['applied'], info['applied']))
    elif oid.startswith('.1.3.6.1.4.1.29932.') and vb:
        # Sound4 : index (carte, canal, flux) puis valeur d'état en dernier
        idx = [str(v) for _o, v in vb[:-1]]
        last = str(vb[-1][1])
        state = values.get(last, last) if isinstance(values, dict) else last
        details.append(state)
        if idx:
            details.append('index ' + '.'.join(idx))
    elif text is None:
        # Trap inconnu : on garde l'OID et les valeurs brutes
        text = f'Trap {oid or "(sans OID)"}'
        for o, v in vb[:6]:
            details.append(f'{o} = {v}')

    msg = text + (f" ({', '.join(d for d in details if d)})" if details else '')
    return sev, alias, msg[:500]
