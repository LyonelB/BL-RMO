# 📻 FM Monitor

![Version](https://img.shields.io/badge/version-0.8.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20%7C%20Linux-lightgrey.svg)

**Système open source de supervision de radio FM** basé sur Linux (Raspberry Pi ou mini-PC). BL-FMO surveille deux choses :

- **la réception** : ce qui sort réellement de l'antenne, captée par un tuner [Headless TEF Lite SE](https://github.com/FMDX-org/Headless-TEF-lite-SE) (TEF668X), ou à défaut un dongle RTL-SDR (signal, modulation, RDS) ;
- **la diffusion** : chaque appareil de la chaîne, du studio à l'émetteur, par SNMP, sondes réseau, journaux syslog et traps.

S'y ajoutent une pige audio 24 h reliée aux alertes, l'écoute de l'antenne depuis le navigateur, des alertes mail et des comptes avec rôles.

<img width="1920" height="919" alt="image" src="https://github.com/user-attachments/assets/1eca93e9-b2fb-4998-87d2-c6e5bf16cc30" />

---

## ✨ Fonctionnalités

### 📡 Réception & Analyse
- **Réception FM** via dongle RTL-SDR (V3, V4, compatibles RTL2832U)
- **VU-mètre temps réel** avec historique audio 30 secondes
- **Détection de silence / perte de signal** configurable
- **Détection d'absence de modulation** (porteuse sans programme)

### 📊 Analyse MPX temps réel
- **Déviation FM peak + RMS** (kHz) — jauge colorée avec seuils configurables
- **Niveaux L/R séparés** — décodage stéréo DSB-SC depuis le signal MPX
- **Ton pilote 19 kHz** — détection et niveau
- **Signal stéréo 38 kHz** — niveau de la sous-porteuse L−R
- **Sous-porteuse RDS 57 kHz** — niveau RF indépendant du décodage
- **Puissance MPX totale** (dBFS)
- **SNR** — rapport signal/bruit (plancher mesuré en 60–75 kHz)
- **Alerte sur-déviation** — email automatique si déviation > seuil

### 📻 Décodage RDS
- **PS** (Programme Service) — nom de la station (8 car.)
- **RT** (RadioText) — titre/artiste en cours (64 car.) — RT complet uniquement
- **PI Code** — identifiant unique hexadécimal de la station
- **Logo station** — récupération automatique via [rds-station-db](https://github.com/LyonelB/rds-station-db) (codes PI RDS) et logos [tef.noobish.eu](https://tef.noobish.eu/logos/logo_preview.html)

### 🔊 Streaming Audio
- **Serveur Icecast2** : streaming stable 24/7
- **Qualité broadcast** : MP3 128 kbps
- **Multi-auditeurs** : jusqu'à 100 connexions simultanées
- **Proxy Flask HTTPS** : écoute directement depuis le dashboard

### 🔔 Alertes & Surveillance
- **Alertes email** automatiques (perte signal + rétablissement)
- **Watchdog** : relance automatique en cas de crash
- **Historique** des alertes et niveaux audio en base SQLite

### 🎙️ Pige audio
- **Enregistrement continu 24 h** de l'antenne en segments MP3 (service `fm-monitor-pige`, indépendant de l'application)
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
- **Serveur syslog** UDP 514 (service `fm-monitor-syslog`) : les appareils de la radio envoient leurs journaux à BL-FMO
- **Traps SNMP** UDP 162 traduits en français, avec relevé immédiat de l'appareil
- **Lecture nettoyée** et **messages masqués** (bruit connu), réaffichables d'une case à cocher

### 👥 Comptes et rôles
- **Lecteur** : consulte et écoute ; **Opérateur** : exporte et enregistre ; **Administrateur** : règle tout
- Contrôle d'accès vérifié côté serveur sur chaque route

### 🌐 Interface Web
- Dashboard temps réel (SSE)
- Configuration complète via interface web
- Statistiques et historique
- Page documentation (FM, MPX, RDS, dongles)
- **Responsive** — accessible depuis smartphone et tablette
- **Mode public** `/public` — dashboard sans authentification
- **Accès local** via `https://bl-fmo.local` (Nginx + mDNS)

### 📡 Push Webhook
- Envoi temps réel des données vers une URL externe (PHP, Node, etc.)
- Payload JSON : signal, RDS, alertes, horodatage
- Configurable depuis l'interface web

---

## 🐳 Installation Docker *(beta — non testé)*

> ⚠️ Le support Docker est en version **beta** et n'a pas encore été testé en production. Retours bienvenus via les [issues GitHub](https://github.com/LyonelB/fm-monitor/issues).

```bash
git clone https://github.com/LyonelB/fm-monitor.git
cd fm-monitor
docker compose up -d
```

Accès : `https://localhost:5000`

> Le dongle RTL-SDR doit être branché avant le démarrage du conteneur.

---

## 🔧 Matériel compatible

| Dongle | Puce | Notes |
|--------|------|-------|
| RTL-SDR Blog V3 | RTL2832U + R820T2 | Drivers standard |
| **RTL-SDR Blog V4** | RTL2832U + R828D | **Recommandé** — TCXO 1ppm, meilleure sensibilité. Nécessite le [fork officiel](https://github.com/rtlsdrblog/rtl-sdr-blog) |
| Nooelec NESDR SMArt | RTL2832U + R820T2 | Compatible standard |
| Autres RTL2832U | RTL2832U | Généralement compatibles |

### Installation drivers RTL-SDR Blog V4

```bash
# Bloquer le driver noyau générique
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf
sudo modprobe -r dvb_usb_rtl28xxu 2>/dev/null

# Compiler le fork officiel
sudo apt install -y git cmake libusb-1.0-0-dev build-essential pkg-config
git clone https://github.com/rtlsdrblog/rtl-sdr-blog
cd rtl-sdr-blog && mkdir build && cd build
cmake .. -DINSTALL_UDEV_RULES=ON
make -j$(nproc) && sudo make install && sudo ldconfig

# Règles udev
sudo cp ~/rtl-sdr-blog/rtl-sdr.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# Test
rtl_test -t
```

---

## 🚀 Installation

### ⚡ Installation automatique (recommandée)

> ⚠️ **Bonne pratique** : avant d'exécuter un script distant, inspectez-le :
> ```bash
> curl -sSL https://raw.githubusercontent.com/LyonelB/fm-monitor/main/install.sh -o install.sh
> less install.sh
> bash install.sh
> ```

Ou directement si vous faites confiance à la source :
```bash
curl -sSL https://raw.githubusercontent.com/LyonelB/fm-monitor/main/install.sh | bash
```

Le script installe automatiquement toutes les dépendances, compile redsea, configure Icecast2, génère les certificats SSL et démarre le service.

**Durée : ~10-15 minutes** (compilation de redsea incluse)

---

### 🎙️ Pige et journaux (services complémentaires)

`install.sh` les installe automatiquement. Sur une installation existante :

```bash
cd ~/fm-monitor
sudo bash install/build/65-pige.sh     # pige 24 h (fm-monitor-pige)
sudo bash install/build/66-syslog.sh   # journaux UDP 514 et traps UDP 162 (fm-monitor-syslog)
```

| Service | Rôle | Ports |
|---|---|---|
| `fm-monitor` | Application web, réception FM, supervision de la chaîne | 5000 (derrière Nginx 80/443) |
| `fm-monitor-pige` | Enregistrement de la pige | lit Icecast 8000 |
| `fm-monitor-syslog` | Réception des journaux et des traps | UDP 514, UDP 162 |
| `icecast2` | Flux audio de l'antenne | 8000 |

### 🔧 Installation manuelle

### Prérequis système

- Raspberry Pi 4/5 ou mini-PC Linux (**Raspberry Pi OS Lite 64-bit** / Debian 13 Trixie / Ubuntu 24.04+)
- Python 3.11+
- `rtl_fm`, `redsea`, `ffmpeg`, `icecast2`

### 1. Cloner le dépôt

```bash
git clone https://github.com/LyonelB/fm-monitor.git
cd fm-monitor
```

### 2. Installer les dépendances

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configurer

Copier et éditer le fichier de configuration :

```bash
cp config.json.example config.json
nano config.json
```

Paramètres clés :

```json
{
  "station": {
    "name": "Ma Radio FM",
    "frequency": "98.5M"
  },
  "rtl_sdr": {
    "frequency": "98.5M",
    "gain": "40"
  },
  "email": {
    "enabled": true,
    "sender_email": "votre@gmail.com",
    "sender_password": "mot_de_passe_application",
    "recipient_emails": ["alerte@exemple.com"]
  }
}
```

> **Gmail** : utilisez un [mot de passe d'application](https://myaccount.google.com/apppasswords), pas votre mot de passe habituel.

### 4. Générer les certificats SSL

```bash
bash generate_ssl.sh
```

### 5. Configurer Icecast2

```bash
sudo apt install -y icecast2
```

Éditer `/etc/icecast2/icecast.xml` et définir le mot de passe source : `fmmonitor2026`

### 6. Créer le service systemd

```bash
sudo nano /etc/systemd/system/fm-monitor.service
```

```ini
[Unit]
Description=FM Monitor
After=network.target icecast2.service

[Service]
Type=simple
User=votre_user
WorkingDirectory=/home/votre_user/fm-monitor
ExecStart=/home/votre_user/fm-monitor/venv/bin/python3 app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable fm-monitor
sudo systemctl start fm-monitor
```

### 7. Accéder au dashboard

```
https://[IP-de-votre-appareil]:5000
```

---

## 📂 Structure du projet

```
fm-monitor/
├── app.py              # Application Flask (routes, API, contrôle d'accès)
├── version.py          # Numéro de version
├── monitor.py          # Moteur de réception (RTL-SDR / TEF668X, RDS, alertes)
├── tef_driver.py       # Pilote du tuner TEF668X Headless
├── mpx_analyzer.py     # Analyse MPX temps réel (déviation, L/R, SNR...)
├── tef_audio_analyzer.py # Analyse audio TEF (spectre, S/B)
├── chain_probe.py      # Supervision de la chaîne (SNMP, sondes, événements)
├── pige.py             # Pige 24 h (enregistreur et export)
├── pige_archive.py     # Extraits audio des dernières alertes
├── syslog_server.py    # Service de réception syslog et traps
├── snmp_traps.py       # Décodage et traduction des traps SNMP
├── syslog_clean.py     # Lecture nettoyée et messages masqués
├── auth.py             # Comptes, rôles, authentification
├── email_alert.py      # Alertes email
├── database.py         # Base SQLite (historique, alertes)
├── install.sh          # Installation complète
├── install/build/      # Étapes d'installation (pige, syslog, Raspberry Pi...)
├── update.sh           # Mise à jour
├── templates/          # Pages : dashboard, pige, chaîne, journaux, statistiques...
└── static/             # Assets CSS/JS
```

---

## 📖 Documentation

La page **À propos** intégrée dans l'interface explique :
- Le fonctionnement de la diffusion FM et du signal MPX
- Le spectre MPX : déviation, pilote 19 kHz, stéréo 38 kHz, RDS 57 kHz, puissance, SNR
- Le système RDS (PS, RT, PI Code)
- Radio Browser et la résolution de logo
- Les dongles compatibles et leur installation
- Les services de BL-FMO et leurs ports

---

## 🛠️ Dépannage

### Le dongle n'est pas détecté

```bash
lsusb | grep -i realtek
rtl_test -t
```

Si `usb_claim_interface error -6` : le driver noyau a pris le contrôle. Appliquer le blacklist (voir section V4 ci-dessus).

### Pas de signal audio

Vérifier la fréquence et le gain dans `config.json`. Tester manuellement :

```bash
rtl_fm -f 98.5M -M wbfm -s 171k -r 44100 - | aplay -r 44100 -f S16_LE
```

### Les emails ne partent pas

1. Vérifier la config SMTP dans l'interface Configuration
2. Utiliser le bouton **Test Email** dans le dashboard
3. Consulter les logs : `journalctl -u fm-monitor -f`

### Le RDS n'apparaît pas

Le RT complet prend ~20-60 secondes à se stabiliser. Vérifier que `redsea` est installé :

```bash
which redsea
tail -f /tmp/rds_output.json
```

---

## 🗺️ Roadmap

- [x] v0.3 à v0.4 : Icecast2, SSL, installation automatique, alertes modulation et RDS, enregistrement
- [x] v0.5.0 : Analyse MPX temps réel (déviation, L/R, pilote, stéréo, RDS RF, SNR)
- [x] v0.6.0 : Décodeur GNU Radio WFM stéréo
- [x] v0.7.0 : Accès local bl-fmo.local, mode public, webhook, interface mobile
- [x] v0.8.0 : Tuner TEF668X, pige 24 h, chaîne de diffusion, journaux et traps, comptes et rôles
- [ ] Page Incidents : regroupement des alertes de réception et de diffusion, cause probable, frise
- [ ] Courbes du relevé de l'émetteur et alerte sur l'écart entre alimentations
- [ ] Sonde AES67 (sortie console Dante)
- [ ] Version anglaise de l'interface

---

## 📄 Licence

MIT — libre d'utilisation, modification et distribution.

---

## 🙏 Crédits

- [RTL-SDR Blog](https://www.rtl-sdr.com) — drivers et matériel
- [redsea](https://github.com/windytan/redsea) — décodeur RDS
- [Radio Browser](https://www.radio-browser.info) — base de données stations
- [Icecast2](https://icecast.org) — serveur de streaming
- [Headless TEF Lite SE](https://github.com/FMDX-org/Headless-TEF-lite-SE) (FMDX.org) : tuner USB TEF668X, firmware [FM-DX-Tuner](https://github.com/kkonradpl/FM-DX-Tuner)
- [Net-SNMP](http://www.net-snmp.org) : outils SNMP

## Changement de source : RTL-SDR ↔ TEF668X Headless

FM Monitor supporte deux sources matérielles, configurables dans `config.json` sans modifier le code.

### Passer en mode TEF668X Headless

1. Brancher le TEF668X Headless en USB-C (câble **données**, pas charge seule)
2. Vérifier la détection : `ls /dev/ttyACM0` et `aplay -l | grep Tuner`
3. Ajouter la section `tef` dans `config.json` :

```json
"tef": {
    "enabled": true,
    "serial_port": "/dev/ttyACM0",
    "alsa_device": "hw:Tuner",
    "signal_threshold_dbf": 20.0,
    "modulation_threshold_dbfs": -40.0
}
```

4. Installer la dépendance : `pip install pyserial --break-system-packages`
5. Ajouter l'utilisateur au groupe dialout : `sudo usermod -a -G dialout $USER`
6. Redémarrer : `sudo systemctl restart fm-monitor`

### Revenir en mode RTL-SDR

Mettre `"enabled": false` dans la section `tef` de `config.json` et redémarrer.

### Différences entre les deux modes

| Fonctionnalité | RTL-SDR | TEF668X Headless |
|---|---|---|
| Signal | dBFS (RMS PCM) | dBf (RF natif) |
| Déviation FM | ✅ (MPXAnalyzer) | ❌ |
| Pilote 19 kHz | ✅ | ❌ |
| Stéréo 38 kHz | ✅ | ❌ |
| RDS 57 kHz | ✅ | ❌ |
| Stéréo/Mono | Via MPX | Via bit MS RDS |
| Niveaux L/R | ✅ | ✅ |
| SNR audio | ✅ | ✅ |
| Qualité réception | Bonne | Excellente |
