# 📻 BL-RMO

**Radio Monitoring**, anciennement FM-Monitor.

![Version](https://img.shields.io/badge/version-0.9.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/plateforme-mini--PC%20Linux-lightgrey.svg)
![Tuner](https://img.shields.io/badge/tuner-Headless%20TEF%20Lite%20SE-purple.svg)

**Système open source de supervision d'une radio FM.** Un mini-PC, un tuner [Headless TEF Lite SE](https://github.com/FMDX-org/Headless-TEF-lite-SE) et une antenne suffisent pour :

- **surveiller la réception FM** : ce qui sort réellement de l'antenne (signal, modulation, RDS) ;
- **surveiller la chaîne de diffusion** : chaque appareil, du studio à l'émetteur, par SNMP, sondes réseau, journaux syslog et traps ;
- **écouter l'antenne** depuis n'importe quel navigateur (retour FM) ;
- **garder une pige** de 30 jours, reliée aux alertes.

Une alerte mail part à chaque panne et à chaque rétablissement, et chaque alerte garde son extrait audio.

<img width="1920" height="919" alt="image" src="https://github.com/user-attachments/assets/1eca93e9-b2fb-4998-87d2-c6e5bf16cc30" />

---

## 🧭 Direction du projet

**Pourquoi BL-RMO ?** Le logiciel s'appelait FM-Monitor. Il surveille désormais la réception FM et DAB+ et toute la chaîne de diffusion : il devient BL-RMO, pour « Radio Monitoring ». Dossier `~/bl-rmo`, services `bl-rmo*`, adresse `bl-rmo.local` : une installation existante passe aux nouveaux noms avec le script de migration (voir [Passer de FM-Monitor à BL-RMO](#passer-de-fm-monitor-à-bl-rmo)).

BL-RMO suit désormais **une seule direction**, et c'est la seule configuration développée et testée :

> **un mini-PC Linux + un tuner Headless TEF Lite SE + une antenne FM**
> pour surveiller la réception FM, la chaîne de diffusion, offrir un retour FM et une pige.

Le Headless TEF Lite SE donne une réception de qualité professionnelle : le tuner démodule lui-même le signal et décode le RDS, et BL-RMO reçoit des mesures RF natives (dBf, rapport signal/bruit, trajets multiples).

### Fonctions en fin de vie

Les fonctions suivantes **ne sont plus développées**. Elles restent présentes dans la version 0.8 pour les installations existantes, mais seront retirées dans une prochaine version :

| Fonction | Pourquoi |
|---|---|
| Réception FM par dongle **RTL-SDR** (rtl_fm, GNU Radio) | Qualité de réception inférieure au TEF ; double chaîne de traitement à maintenir |
| **Analyse MPX** (déviation, pilote, sous-porteuses, SNR MPX) | Intérêt réel mais limité avec un dongle RTL-SDR ; ce n'est pas la direction retenue |
| Installation **Docker** | Jamais validée en production ; l'accès au tuner USB et aux ports syslog/traps s'y prête mal |

Si vous utilisez l'un de ces modes, la version 0.8 continue de fonctionner. Les instructions restent en [annexe](#annexe--mode-rtl-sdr-et-analyse-mpx-fin-de-vie).

### Et le RTL-SDR ?

Le dongle RTL-SDR revient dans un autre rôle : **second tuner dédié au DAB+**, en parallèle du TEF pour la FM. C'est la reprise de BL-DMO ([dabplus-monitor](https://github.com/LyonelB/dabplus-monitor)), désormais intégré à BL-RMO (en cours de validation).

---

## ✨ Fonctionnalités

### 📡 Réception FM (Headless TEF Lite SE)
- **Mesures RF natives** : niveau en dBf, rapport signal/bruit, trajets multiples, décalage de fréquence
- **Présence du signal** mesurée par la cadence des trames du tuner, indépendante du son
- **Absence de modulation** (porteuse présente, programme muet), alertes **RDS** et **RadioText**
- **Niveaux audio G/D** mesurés en continu, **stéréo ou mono** d'après l'indicateur de pilote du tuner
- **RDS** décodé par le tuner : PS, RadioText, code PI ; logo de la station via [rds-station-db](https://github.com/LyonelB/rds-station-db)

### 📶 Réception DAB+ (option, clé RTL-SDR)
- **Ensemble** : SNR, qualité FIC, gain, identifiant ; alerte quand l'ensemble se dégrade
- **Services** : présence dans le multiplex, niveaux audio, DLS, débit, disponibilité sur 24 h ; alertes **service absent** et **silence**, sur tous les services ou seulement ceux cochés
- **Écoute** d'un service depuis la page DAB+, diaporama (slideshow) affiché pendant l'écoute
- Alertes DAB+ par mail et dans un onglet **Réception DAB+** de la page Journaux (la pige n'enregistre que la FM)
- Service indépendant `bl-rmo-dab` : un plantage de welle-cli ou de la clé ne touche ni l'interface, ni la FM

### 🔊 Retour FM
- **Écoute de l'antenne** dans le navigateur, flux Icecast2 MP3 128 kbps
- **Mode public** `/public` : tableau de bord sans authentification

### 🎙️ Pige audio
- **Enregistrement continu sur 30 jours** de l'antenne en segments MP3 (service `bl-rmo-pige`, indépendant de l'application), environ 1,4 Go par jour ; durée réglable par `"pige": {"retention_hours": 720}` dans `config.json`
- **Frise, écoute et export** d'un créneau en MP3, sans réencodage
- **Extraits des 10 dernières alertes**, de 30 s avant l'alerte à 30 s après le rétablissement, conservés après la sortie de la pige
- Chaque alerte et chaque événement de la chaîne ouvre la pige au bon moment

### 🔗 Chaîne de diffusion
- **Synoptique** du studio à l'antenne : état de chaque appareil et de chaque lien, depuis quand, détail en un clic
- **SNMP v1, v2c, v3** (lecture seule), ping, TCP, page web, Icecast ; seuils et valeurs attendues
- **Préréglages** : BW Broadcast TX V3, Deva DB91-TX / DB91-RX, Sound4 PULSE / IMPACT
- **Sondes réseau** (rafales de pings : pertes, latence, gigue) rattachées aux liens
- **Historique en courbes**, alertes mail avec les valeurs en défaut, export ZIP

### 📜 Journaux et traps
- **Serveur syslog** UDP 514 (service `bl-rmo-syslog`) : les appareils de la radio envoient leurs journaux à BL-RMO
- **Traps SNMP** UDP 162 traduits en français, avec relevé immédiat de l'appareil
- **Lecture nettoyée** et **messages masqués** (bruit connu), réaffichables d'une case à cocher

### 🔔 Alertes
- **Mail** à la panne et au rétablissement, pour la réception comme pour la chaîne
- **Historique** des alertes en base SQLite, chacune reliée à la pige
- **Push webhook** : données temps réel en JSON vers une URL externe

### 👥 Comptes et rôles
- **Lecteur** : consulte et écoute ; **Opérateur** : exporte et enregistre ; **Administrateur** : règle tout
- Contrôle d'accès vérifié côté serveur sur chaque route

### 🌐 Interface web
- **Tableau de bord** d'un seul coup d'œil : réception FM et DAB+ (lecteur, VU-mètre, indicateurs, dernière alerte), chaîne de diffusion complète avec ses sondes, état de la pige
- Configuration complète depuis le navigateur
- **Responsive** : smartphone et tablette
- **Accès local** via `https://bl-rmo.local` (Nginx + mDNS)

---

## 🔧 Matériel

| Élément | Référence | Notes |
|---|---|---|
| Ordinateur | Mini-PC Linux | Référence : Debian 13 (Trixie) sur mini-PC x86 |
| Tuner | [Headless TEF Lite SE](https://github.com/FMDX-org/Headless-TEF-lite-SE) (FMDX.org) | TEF668X V205 (TEF6687 recommandé), entrées AM et FM séparées, audio numérique par USB, firmware [FM-DX-Tuner](https://github.com/kkonradpl/FM-DX-Tuner) |
| Antenne | Antenne FM extérieure | Bien dégagée : BL-RMO écoute comme un auditeur |
| Câble | USB de **données** | Un câble « charge seule » ne laisse pas passer le port série |
| DAB+ (option) | Clé RTL-SDR + antenne bande III | NooElec NESDR SmartEE conseillée pour un usage 24 h/24 (le RTL-SDR Blog V4 a montré des instabilités USB) |

Le tuner apparaît sous Linux comme un port série (`/dev/ttyACM0`, protocole XDR) et une carte son (`hw:Tuner`). Il est reconnu par son identifiant USB `1209:6687`.

---

## 🚀 Installation

### ⚡ Installation automatique

> ⚠️ **Bonne pratique** : avant d'exécuter un script distant, inspectez-le :
> ```bash
> curl -sSL https://raw.githubusercontent.com/LyonelB/BL-RMO/main/install.sh -o install.sh
> less install.sh
> bash install.sh
> ```

Le script installe les dépendances, configure Icecast2, Nginx et les certificats, puis les services de BL-RMO (application, pige, journaux).

### 📡 Mise en service du tuner

```bash
# Brancher le tuner (câble USB de données), puis vérifier :
ls /dev/ttyACM0
aplay -l | grep Tuner

# Accès au port série pour l'utilisateur du service
sudo usermod -a -G dialout $USER
```

Puis, dans l'interface : **Configuration**, bouton **« Scan dongle »**, source **TEF668X**. La section correspondante de `config.json` :

```json
"tef": {
    "enabled": true,
    "serial_port": "/dev/ttyACM0",
    "alsa_device": "hw:Tuner",
    "signal_threshold_dbf": 20.0,
    "modulation_threshold_dbfs": -40.0
}
```

### 🎙️ Services

`install.sh` les installe tous. Sur une installation existante, la pige et les journaux s'ajoutent ainsi :

```bash
cd ~/bl-rmo
sudo bash install/build/65-pige.sh     # pige 30 jours (bl-rmo-pige)
sudo bash install/build/66-syslog.sh   # journaux UDP 514 et traps UDP 162 (bl-rmo-syslog)
sudo BUILD_WELLE=1 bash install/build/67-dab.sh   # réception DAB+ (bl-rmo-dab) et compilation de welle-cli
```

La réception DAB+ reste en veille jusqu'à son activation dans la page **DAB+** (menu, administrateur) : canal, seuils et services surveillés s'y règlent.

| Service | Rôle | Ports |
|---|---|---|
| `bl-rmo` | Application web, réception FM, supervision de la chaîne | 5000 (derrière Nginx 80/443) |
| `bl-rmo-pige` | Enregistrement de la pige | lit Icecast 8000 |
| `bl-rmo-syslog` | Réception des journaux et des traps | UDP 514, UDP 162 |
| `bl-rmo-dab` | Réception DAB+ (pilote welle-cli) | welle-cli 7979, local |
| `icecast2` | Flux audio de l'antenne (retour FM et pige) | 8000 |

La supervision SNMP de la chaîne utilise `snmpget` : `sudo apt install snmp` s'il n'est pas déjà présent.

### Passer de FM-Monitor à BL-RMO

Une installation faite avant la version 0.10 utilise encore le dossier `~/fm-monitor` et les services `fm-monitor*`. Le script de migration passe tout aux nouveaux noms, sans toucher aux données (pige, extraits, bases, `config.json`) :

```bash
cd ~/fm-monitor
bash install/migrate-to-bl-rmo.sh            # inventaire, ne modifie rien
bash install/migrate-to-bl-rmo.sh --apply    # migration (environ 5 min de coupure)
bash ~/bl-rmo/install/migrate-to-bl-rmo.sh --rollback   # retour arrière si besoin
```

Il renomme le dossier et les services, met à jour Nginx, les certificats, sudoers, le nom de la machine (`bl-rmo.local`) et l'annonce NetBIOS. Les anciens noms restent utilisables pendant la transition : `~/fm-monitor` pointe vers `~/bl-rmo`, `bl-fmo.local` répond toujours et `systemctl restart fm-monitor` redémarre `bl-rmo`. L'adresse IP ne change pas.

### 🔄 Mise à jour

```bash
cd ~/bl-rmo
bash update.sh
```

### ✉️ Alertes mail

Dans **Configuration**, renseigner l'expéditeur et les destinataires. Avec Gmail, utiliser un [mot de passe d'application](https://myaccount.google.com/apppasswords), puis le bouton **Test Email**.

---

## 📂 Structure du projet

```
bl-rmo/
├── app.py              # Application Flask (routes, API, contrôle d'accès)
├── version.py          # Numéro de version
├── monitor.py          # Moteur de réception (tuner, RDS, alertes)
├── tef_driver.py       # Pilote du tuner TEF668X (protocole XDR)
├── tef_audio_analyzer.py # Analyse audio (niveaux, spectre, S/B)
├── chain_probe.py      # Supervision de la chaîne (SNMP, sondes, événements)
├── audio_defects.py    # Défauts audio : micro-coupures, clics, craquements
├── pige.py             # Pige 30 jours (enregistreur et export)
├── pige_archive.py     # Extraits audio des dernières alertes
├── syslog_server.py    # Service de réception syslog et traps
├── snmp_traps.py       # Décodage et traduction des traps SNMP
├── syslog_clean.py     # Lecture nettoyée et messages masqués
├── auth.py             # Comptes, rôles, authentification
├── email_alert.py      # Alertes mail
├── database.py         # Base SQLite (historique, alertes)
├── install.sh          # Installation complète
├── install/build/      # Étapes d'installation (pige, journaux...)
├── update.sh           # Mise à jour
├── templates/          # Pages : tableau de bord, pige, chaîne, journaux...
└── static/             # CSS et JS
```

En fin de vie (voir [Direction du projet](#-direction-du-projet)) : `mpx_analyzer.py`, `wfm_stereo.py`, la partie RTL-SDR de `monitor.py`, `Dockerfile`, `docker-compose.yml`, `docker/`.

---

## 📖 Documentation

La page **À propos** intégrée à l'interface explique le fonctionnement de BL-RMO, le tuner Headless TEF Lite SE, la diffusion FM et le RDS, ainsi que les services et leurs ports.

---

## 🛠️ Dépannage

### Le tuner n'est pas détecté

```bash
lsusb | grep 1209:6687        # le tuner est-il vu en USB ?
ls -l /dev/ttyACM0            # port série présent ?
groups | grep dialout         # l'utilisateur a-t-il accès au port série ?
aplay -l | grep Tuner         # carte son présente ?
```

Rien dans `lsusb` : changer de câble (câble de données) ou de port USB.

### Pas de son dans le retour FM ou la pige

La carte son du tuner ne peut être ouverte que par un seul programme. Vérifier qu'aucun autre logiciel n'utilise `hw:Tuner`, puis : `sudo systemctl restart bl-rmo`.

### Les mails ne partent pas

1. Vérifier la configuration dans la page Configuration
2. Utiliser le bouton **Test Email**
3. Consulter les logs : `sudo journalctl -u bl-rmo -n 50`

### Les journaux des appareils n'arrivent pas

```bash
sudo systemctl status bl-rmo-syslog
sudo journalctl -u bl-rmo-syslog -n 20
```

Sur l'appareil, le serveur syslog doit être l'**adresse IP** de BL-RMO (pas `bl-fmo.local`), port 514, en UDP.

---

## 🗺️ Roadmap

- [x] v0.3 à v0.7 : Icecast2, SSL, installation automatique, alertes, accès local, mode public, webhook, interface mobile
- [x] v0.8.0 : tuner TEF668X, pige 24 h, chaîne de diffusion, journaux et traps, comptes et rôles
- [ ] Courbes du relevé de l'émetteur et alerte sur l'écart entre alimentations
- [ ] Journaux par domaine (réception, diffusion, système) et page Bilan
- [ ] Page Incidents : regroupement des alertes de réception et de diffusion, cause probable, frise
- [ ] Retrait du mode RTL-SDR, de l'analyse MPX et de Docker
- [x] v0.9.0 : nouveau nom BL-RMO, journaux en trois onglets
- [x] Réception DAB+ (reprise de BL-DMO), validée sur site
- [x] Tableau de bord FM, DAB+, chaîne et pige
- [ ] Piges multiples : plusieurs enregistrements (FM, services DAB+), créés depuis la page Pige
- [ ] Alerte de passage en mono
- [ ] Fermeture de BL-DMO ([dabplus-monitor](https://github.com/LyonelB/dabplus-monitor)) : annonce, archivage, puis suppression du dépôt
- [ ] welle-monitor : reporter les changements de la prochaine version de welle.io
- [ ] Version anglaise de l'interface
- [ ] Piste : sonde AES67 (sortie console Dante)

---

## 📄 Licence

MIT : libre d'utilisation, de modification et de distribution.

---

## 🙏 Crédits

- [Headless TEF Lite SE](https://github.com/FMDX-org/Headless-TEF-lite-SE) (FMDX.org) : tuner USB TEF668X, firmware [FM-DX-Tuner](https://github.com/kkonradpl/FM-DX-Tuner)
- [welle.io](https://github.com/AlbrechtL/welle.io) (Albrecht Lohofener, Matthias P. Braendli) et son fork [welle-monitor](https://github.com/LyonelB/welle-monitor) : décodage DAB+
- [Icecast2](https://icecast.org) : serveur de streaming
- [Net-SNMP](http://www.net-snmp.org) : outils SNMP
- [Radio Browser](https://www.radio-browser.info) : base de données de stations
- [redsea](https://github.com/windytan/redsea) et [RTL-SDR Blog](https://www.rtl-sdr.com) : décodage RDS et pilotes du mode RTL-SDR (fin de vie)

---

## Annexe : mode RTL-SDR et analyse MPX (fin de vie)

<details>
<summary>Instructions conservées pour les installations existantes</summary>

Ces fonctions ne sont plus développées et seront retirées dans une prochaine version.

### Dongles RTL-SDR

| Dongle | Puce | Notes |
|--------|------|-------|
| RTL-SDR Blog V3 | RTL2832U + R820T2 | Pilotes standard |
| RTL-SDR Blog V4 | RTL2832U + R828D | Nécessite le [fork officiel](https://github.com/rtlsdrblog/rtl-sdr-blog) |
| Nooelec NESDR SMArt | RTL2832U + R820T2 | Pilotes standard |

### Pilotes RTL-SDR Blog V4

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo modprobe -r dvb_usb_rtl28xxu 2>/dev/null
sudo apt install -y git cmake libusb-1.0-0-dev build-essential pkg-config
git clone https://github.com/rtlsdrblog/rtl-sdr-blog
cd rtl-sdr-blog && mkdir build && cd build
cmake .. -DINSTALL_UDEV_RULES=ON
make -j$(nproc) && sudo make install && sudo ldconfig
sudo cp ~/rtl-sdr-blog/rtl-sdr.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
rtl_test -t
```

### Revenir en mode RTL-SDR

Mettre `"enabled": false` dans la section `tef` de `config.json`, puis `sudo systemctl restart bl-rmo`.

### Analyse MPX (mode RTL-SDR uniquement)

Déviation crête et RMS, niveaux G/D, pilote 19 kHz, sous-porteuse stéréo 38 kHz, sous-porteuse RDS 57 kHz, puissance MPX, SNR, alerte de sur-déviation.

### Différences entre les deux modes

| Fonctionnalité | RTL-SDR | Headless TEF Lite SE |
|---|---|---|
| Niveau du signal | dBFS, calculé sur l'audio | dBf, mesure RF native |
| RDS | redsea sur le MPX | Décodé par le tuner |
| Analyse MPX | Oui | Non |
| Stéréo / mono | Par le pilote | Par le bit MS du RDS |
| Niveaux G/D, S/B audio | Oui | Oui |
| Qualité de réception | Correcte | Excellente |

### Docker (jamais validé)

```bash
docker compose up -d
```

</details>
