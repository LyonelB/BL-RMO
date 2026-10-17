# Changelog

Toutes les modifications notables de ce projet seront documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).
## [Non publié]

### Journaux
- **Une seule page Journaux, trois onglets**, sans mélange :
  - **Réception FM** : alertes du tuner (signal, modulation, RDS, RadioText) avec durée, état, écoute et MP3 ; nombre d'alertes et durée cumulée sur 7 et 30 jours, calculés sur l'historique (plus de compteur remis à zéro au redémarrage) ;
  - **Chaîne de diffusion** : journaux syslog et traps des appareils (contenu de l'ancienne page Journaux) ;
  - **BL-FMO** : journal système de l'application, de la pige ou de la réception des journaux, 100 à 2 000 lignes, filtre avertissements et erreurs.
- La page **Statistiques** disparaît du menu ; `/stats` redirige vers l'onglet Réception FM.
- L'alerte « Perte émetteur » s'appelle désormais « Signal perdu » (le tuner mesure la réception, pas l'émetteur).

### Installation
- `install.sh` suit la configuration de référence (mini-PC + Headless TEF Lite SE) : installe `snmp` (supervision de la chaîne), ajoute l'utilisateur aux groupes `dialout` et `audio`, crée une configuration avec le tuner TEF activé, vérifie le tuner en fin d'installation ; n'installe plus rtl-sdr, redsea ni GNU Radio.

---

## [0.8.0] - 2026-10-17

BL-FMO ne surveille plus seulement la réception FM : il supervise aussi toute la chaîne de diffusion, du studio à l'antenne, et garde la trace audio de ce qui est parti à l'antenne.

### Pige audio
- **Pige 24 h** : enregistrement continu de l'antenne en segments MP3 de 5 min, par un service indépendant (`fm-monitor-pige`) qui continue pendant un redémarrage de l'application.
- **Page Pige** : frise des dernières 24 h, écoute de contrôle, export MP3 d'un créneau au choix (sans réencodage).
- **Extraits des 10 dernières alertes** : à chaque incident, l'extrait de 30 s avant l'alerte à 30 s après le rétablissement est archivé et reste disponible après la sortie de la pige.
- **Lien alertes et pige** : depuis Statistiques et la Chaîne, « Écouter » et « MP3 » ouvrent l'antenne au moment de l'alerte, ou son extrait archivé.

### Chaîne de diffusion
- **Supervision des appareils** : SNMP v1, v2c et v3 (lecture seule), ping, TCP, page web, Icecast, avec seuils et valeurs attendues.
- **Préréglages SNMP** : BW Broadcast TX V3, Deva DB91-TX et DB91-RX, Sound4 PULSE/IMPACT.
- **Sondes réseau** façon netwatch : rafales de pings, pertes, latence et gigue, rattachées au lien qu'elles surveillent.
- **Synoptique cliquable** : sites, appareils et liens dans l'ordre de la chaîne, état et « depuis » sur chaque tuile, panneau de détail élargissable (état, journaux, historique en courbes, réglages, lien vers l'interface web de l'appareil).
- **Gérer la chaîne** : ajout, ordre et réglage des appareils et des sondes, test immédiat.
- **Alertes mail** de la chaîne, avec les valeurs en défaut ; événements reliés à la pige.
- **Export ZIP** de l'historique (relevés, événements, journaux) pour analyse.

### Journaux et traps
- **Réception syslog** (UDP 514) des appareils de la radio par un service dédié (`fm-monitor-syslog`), conservation 90 jours.
- **Page Journaux** : appareils émetteurs, filtres, recherche, export CSV, aide à la configuration des appareils.
- **Traps SNMP** (UDP 162, v1 et v2c, réponse aux informs) traduits en français (Deva, Sound4, génériques), avec relevé immédiat de l'appareil concerné.
- **Lecture nettoyée** : préfixe du BW retiré, messages sur plusieurs lignes, relevé horaire de l'émetteur résumé en une ligne.
- **Messages masqués** : règles intégrées pour le bruit connu, règles ajoutées depuis la page (« masquer les semblables »), case « Afficher les messages masqués ».

### Comptes et rôles
- **Plusieurs comptes** avec trois rôles : Lecteur (consulte et écoute), Opérateur (exporte, enregistre), Administrateur (règle tout).
- **Contrôle d'accès côté serveur** sur chaque route ; une route non listée est réservée à l'administrateur.
- **Page Utilisateurs / Mon compte** : création, rôle, activation, mot de passe.
- Le compte existant de `config.json` devient automatiquement le premier administrateur.

### Réception TEF668X
- Détection du Headless TEF Lite SE (FMDX.org) par son identifiant USB, lecture des trames de signal du firmware FM-DX-Tuner.
- Présence du signal mesurée par la cadence des trames, alertes indépendantes et rétablissement fiabilisé.
- Panneau d'analyse audio : spectre 0 à 20 kHz, qualité S/B.

### Divers
- Configuration réseau (IP fixe ou DHCP) appliquée par NetworkManager avec retour automatique en cas d'échec.
- Rechargement à chaud de la configuration mail et station.
- Jauge Signal RF étendue à 90 dBf.
- Appliance Raspberry Pi : scripts de construction, premier démarrage et assistant de configuration (en cours).
- Numéro de version affiché dans la page À propos.

### Sécurité
- `/api/config/full` ne renvoie plus aucun secret (hash du mot de passe, mots de passe mail et Wi-Fi) et seulement l'affichage aux non-administrateurs.

### Installation
- `install.sh` installe aussi les services de pige et de journaux (`install/build/65-pige.sh`, `66-syslog.sh`).
- `update.sh` redémarre tous les services BL-FMO.
- Scripts `patch_*.py` retirés du dépôt : leurs modifications sont intégrées au code (ils restent dans l'historique git).

---

## [0.7.1] - 2026-08-06

### Ajouts
- Interrupteur des alertes mail dans la page Configuration, pris en compte sans redémarrage.

---

## [0.7.0] - 2026-05-03
### Accessibilite et Distribution

#### Acces reseau local
- Nginx reverse proxy : acces via https://bl-fmo.local
- mDNS/Avahi : resolution bl-fmo.local sur tout le reseau local
- mkcert : certificat SSL local valide
- Redirection HTTP vers HTTPS automatique via Nginx
- ProxyFix : middleware Werkzeug pour la gestion des redirections via proxy

#### Mode public
- Route /public : dashboard accessible sans authentification
- Sidebar reduite a un lien Se connecter, sans bouton REC ni Configuration
- Badge Vue publique visible dans le header

#### Push webhook temps reel
- Thread webhook : envoi automatique des donnees vers une URL externe
- Payload JSON : station, frequency, signal_db, signal_ok, ps, rt, pi, stereo, alert, timestamp
- Configuration UI : section dans la page Configuration
- fmmonitor-receiver.php : script PHP pour afficher les donnees sur un site web

#### Interface responsive mobile
- Sidebar drawer : menu hamburger sur mobile avec overlay
- Grilles adaptatives : colonnes empilees sur mobile, cote a cote sur desktop
- 4 templates mis a jour : index, config, stats, about

### Installation
- install.sh : ajout de Avahi, Nginx, mkcert lors de l'installation
- Hostname interactif lors de l'installation (defaut : bl-fmo)

---

## [0.6.0] - 2026-04-27

### 🎉 Mode GNU Radio WFM stéréo

#### Nouveau décodeur GNU Radio
- **`wfm_stereo.py`** : démodulation WFM stéréo complète via GNU Radio + gr-osmosdr
  - Branche A : audio stéréo S16LE 48 kHz → ffmpeg → Icecast (vraie stéréo L/R)
  - Branche B : MPX démodulé mono 240 kHz → FIFO → redsea → RDS JSON
  - Déemphasis 50 µs Europe intégré (`wfm_rcv_pll`)
- **Sélection du décodeur** dans `config.json` : `"decoder": "rtl_fm"` ou `"gnuradio"`
- **Toggle UI** dans la page Configuration → Scan dongle : boutons `rtl_fm (mono)` et `GNU Radio (stéréo)`
- **VU-mètre L/R séparés** : calcul RMS indépendant sur canaux gauche et droit (`level_left`, `level_right`)

#### Dépendances ajoutées
- `gnuradio` + `gr-osmosdr` : `sudo apt install gnuradio gr-osmosdr`

### 🛠️ Corrections

- **Alertes ouvertes au redémarrage** : clôture automatique des alertes `signal_lost`, `no_modulation`, `rds_lost` sans rétablissement lors du redémarrage du service (`database.py` → `close_open_alerts()`)
- **Auto-stop player** : arrêt automatique du player audio après 15 minutes
- **Session timeout** : ajout de `credentials: 'include'` sur tous les appels `fetch()` des templates — le timeout 60 min est maintenant opérationnel
- **datetime.utcnow() deprecation** : migration vers `datetime.now(timezone.utc)` dans `app.py`
- **smtp_server corrompu** : valeur `config.json` nettoyée (`smtp.gmail.com`)

### ⚙️ Configuration

Nouveau paramètre dans `config.json` :
```json
"decoder": "gnuradio"
```
Valeurs : `"rtl_fm"` (défaut, mono) ou `"gnuradio"` (stéréo WFM)

[0.7.0]: https://github.com/LyonelB/fm-monitor/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/LyonelB/fm-monitor/compare/v0.5.2...v0.6.0

## [0.5.2] - 2026-04-10
### 🎉 Support TEF668X Headless USB Tuner
#### Nouveau matériel supporté
- **TEF668X Headless Lite SE** (FMDX-org, firmware FM-DX-Tuner / kkonradpl) via USB-C
- Audio numérique USB natif (USB Audio Class, S16_LE stéréo 48 kHz)
- Protocole série XDR-GTK sur `/dev/ttyACM0`

#### Nouveaux modules
- **`tef_driver.py`** : driver série TEF668X — tuning, métriques signal (dBf/SNR/multipath), décodage RDS natif (PI/PS/RT/MS)
- **`tef_audio_analyzer.py`** : analyse audio stéréo 48 kHz via numpy — niveaux L/R, puissance, SNR audio
- **`config.example.json`** : exemple de configuration avec section `tef`

#### Modifications monitor.py
- Mode TEF activé via `config.json` → `tef.enabled: true`
- Basculement automatique TEF / RTL-SDR selon configuration
- Pipeline audio : `ffmpeg asplit` → Icecast + analyse simultanée
- Signal RF en dBf (0–60) au lieu de dBFS
- Détection stéréo/mono depuis bit MS du groupe RDS 0A
- Analyse MPX partielle (L/R, puissance audio, SNR audio)

#### Dashboard
- VU-mètre renommé **Signal RF** avec jauge 0–60 dBf
- Barres L/R déplacées dans la carte Signal RF
- Graphique temps réel adaptatif ±1 dBf autour du signal
- Badge MPX : **STÉRÉO / MONO** depuis RDS (mode TEF) ou MPX (mode RTL-SDR)
- Logo station agrandi (150 px), RT pleine largeur sous PS+PI

### Alertes en mode TEF
- Surveillance modulation audio via `mpx_power` (TEFAudioAnalyzer)
- Seuil configurable `tef.modulation_threshold_dbfs` (défaut : -40.0 dBFS)
- Alerte email si audio < seuil pendant `modulation_alert_delay` secondes

### Compatibilité
- Mode RTL-SDR inchangé (`tef.enabled: false`)
- Interface identique `MPXAnalyzer` / `TEFAudioAnalyzer`


## [0.5.1] - 2026-03-17

### Ajouté
- Export/import de la configuration (ZIP horodaté avec config.json, .env, certificats SSL)
- Lookup logo station via rds-station-db (PI + PS) avec validation croisée
- Watcher rds-station-db : rafraîchissement automatique toutes les 24h
- Réinitialisation automatique du logo lors d'un changement de PI

### Supprimé
- Suppression du fallback radio-browser.info (trop peu fiable)

## [0.5.0] - 2026-03-16

### 🎉 Fonctionnalités

#### Analyse MPX temps réel
- **Nouveau module `mpx_analyzer.py`** : analyse complète du signal multiplex FM en temps réel, optimisé Raspberry Pi 3B+
- **Déviation FM peak + RMS** : mesure de la déviation instantanée en kHz avec jauge colorée (vert < 60 kHz / jaune < 70 kHz / rouge > 70 kHz)
- **Ton pilote 19 kHz** : détection et niveau de la sous-porteuse pilote stéréo
- **Signal stéréo L−R (38 kHz)** : décodage DSB-SC complet — reconstruction des canaux gauche et droit séparés
- **Niveaux L/R indépendants** : niveaux audio Left et Right affichés séparément en dBFS
- **Sous-porteuse RDS (57 kHz)** : niveau RF du canal RDS indépendamment du décodage RDS
- **Puissance MPX totale** : niveau RMS global du signal multiplex en dBFS
- **SNR** : rapport signal/bruit mesuré (signal audio 100 Hz–15 kHz vs plancher de bruit 60–75 kHz)
- **Alerte sur-déviation** : email automatique si la déviation FM dépasse le seuil configurable (défaut : 80 kHz)

#### Dashboard redesigné — no-scroll
- **Layout no-scroll** : toutes les informations visibles sans défilement sur un écran 1080p
- **Bande du haut en 3 blocs séparés** : Station+Fréquence+Email / Uptime+Alertes / Modulation+Signal
- **Colonne gauche** : VU-mètre + Player + RDS + niveaux L/R
- **Colonne droite** : Analyse MPX complète (4 lignes) + historique audio pleine largeur
- **VU-mètre unifié** : barre fine verte cohérente (même style pour audio, L/R, déviation, pilote, SNR...)
- **Stabilité d'affichage** : largeur fixe sur les champs d'état — plus de déplacement des blocs lors des changements d'état

#### Page À propos enrichie
- **Schéma visuel du spectre MPX** (0–75 kHz) avec zones colorées
- **6 fiches techniques** : déviation FM, pilote 19 kHz, stéréo 38 kHz, RDS 57 kHz, puissance MPX, SNR

### 🛠️ Technique

- **`mpx_analyzer.py`** : filtres Butterworth pré-calculés (scipy), traitement 1 chunk/4 pour économiser le CPU, thread-safe
- **`monitor.py`** : intégration `MPXAnalyzer` dans `_master_monitor()`, flag `mpx_enabled` toggleable, surveillance sur-déviation dans `_monitor_signal()`
- **`requirements.txt`** : ajout de `scipy`

### ⚙️ Configuration

Nouveaux paramètres optionnels dans `config.json` :

```json
"audio": {
  "deviation_alert_threshold": 80.0,
  "deviation_alert_delay": 10
}
```

---

## [0.4.3] - 2026-03-12

### 🔧 Corrections

- **Durée des alertes** : calculée depuis les timestamps réels (début → fin) au lieu du champ `duration_seconds` en base — les durées affichées en page Statistiques sont maintenant exactes
- **Enregistrement audio** : reprise correcte de l'affichage (chrono + barre) au retour sur l'onglet après mise en arrière-plan du navigateur (`visibilitychange`)
- **Fichiers orphelins** : nettoyage automatique des enregistrements non téléchargés au démarrage du service et toutes les 10 minutes (suppression après 30 min)

### 🛠️ Infrastructure

- **Serveur WSGI** : migration du serveur de développement Flask vers **Gunicorn** (1 worker, 4 threads) — suppression de l'avertissement de production dans les logs
- **`requirements.txt`** : ajout de `gunicorn==25.1.0`

---

## [0.4.2] - 2026-03-11

### 🎉 Fonctionnalités

#### Enregistrement audio à la demande
- **Bouton REC** dans le player — même style que le bouton Play, positionné à sa gauche
- **Démarrage / arrêt** : clic pour enregistrer, reclic pour stopper et télécharger automatiquement le fichier MP3
- **Nommage automatique** : `fm-monitor_YYYYMMDD_HHMMSS.mp3`
- **Limite 50 Mo** : arrêt automatique à 50 Mo (~52 min à 128 kbps), téléchargement déclenché automatiquement
- **Barre de progression** : chrono `MM:SS`, taille en Mo et barre rouge pendant l'enregistrement
- **Nettoyage automatique** : le fichier est supprimé du Pi 5 secondes après le téléchargement
- Nouvelles routes API : `POST /api/record/start`, `POST /api/record/stop`, `GET /api/record/download`, `GET /api/record/status`

### 🔧 Corrections

- **Voyant modulation** : connecté à `modulation_ok` (était statique) — affiche Active / Absente / — selon l'état réel
- **Player** : lien "Player externe" déplacé sur la même ligne que le contrôle du volume

---

## [0.4.1] - 2026-03-10

### 🔔 Surveillance avancée

#### Détection d'absence de modulation
- **Surveillance par écart-type** : détection de l'absence de modulation audio même quand l'émetteur reste actif (porteuse sans programme)
- **Alerte email** : envoi automatique après un délai configurable (défaut : 30s)
- **Alerte de rétablissement** : email envoyé dès le retour de la modulation

#### Surveillance de la présence RDS
- **Détection de perte RDS** : alerte si aucune donnée RDS reçue depuis un délai configurable (défaut : 120s)
- **Alerte de rétablissement** RDS
- **Voyant RDS** sur le dashboard : 🔘 jamais reçu / 🟢 actif / 🔴 absent

### ⚙️ Configuration étendue

Tous les seuils sont modifiables depuis l'interface web sans redémarrer le service :

- **Seuil de modulation** (dB std) — sensibilité de détection
- **Délai avant alerte modulation** (secondes)
- **Seuil perte émetteur** (dBFS)
- **Délai avant alerte émetteur** (secondes)
- **Délai avant alerte RDS absent** (secondes)

### 📊 Page Statistiques

- **Colonne Type** : badges colorés distincts — 🔴 Perte émetteur / 🟠 Absence modulation / 🟣 RDS absent
- **Groupement multi-types** : paires perte/rétablissement correctement regroupées pour les 3 types d'alertes

### 🔧 Technique

- `monitor.py` : surveillance modulation par buffer glissant 30s + état RDS ; `rds_timeout` lu depuis la config
- `database.py` : `get_alerts_history_grouped` refactorisé pour tous les types de paires
- `app.py` : sauvegarde et application à chaud de `modulation_alert_delay`, `modulation_std_threshold`, `signal_lost_threshold`, `rds_timeout`

---

## [0.4.0] - 2026-03-09

### 🎉 Fonctionnalités

#### Script d'installation automatique
- **`install.sh`** : Installation complète en une commande — dépendances système, compilation redsea, configuration Icecast2, certificats SSL, service systemd
- **Durée estimée** : ~10-15 minutes (compilation redsea incluse)

#### Nouvel affichage historique des niveaux audio
- **Graphique historique repensé** : Visualisation plus claire des niveaux audio sur 24h
- **Meilleure lisibilité** : Distinction visuelle améliorée entre silence, signal faible et signal normal

### 🔧 Modifications

#### Suppression du contrôle des services
- **Retrait du panneau de contrôle des services** depuis l'interface web (start/stop/restart)
- Le Pi 3B+ gère les services de manière autonome via systemd — le contrôle manuel est superflu

### 🛠️ Maintenance

- **Nettoyage dépôt GitHub** : Harmonisation des messages de commit, suppression des références à l'ancienne numérotation interne
- **`config.json.example`** : Fichier d'exemple épuré sans données sensibles

### ⚠️ Breaking Changes

- Le panneau de contrôle des services est supprimé de l'interface — utiliser `systemctl` en ligne de commande si besoin

### ⏭️ Note : v0.3.3 skippée

La v0.3.3 prévue (monitoring RDS, stabilité) n'a pas été publiée — son contenu sera traité dans une version ultérieure.

---

## [0.3.1] - 2026-03-01

### 🎉 Fonctionnalités majeures

#### Streaming Icecast2 professionnel
- **Remplacement du streaming par fichier** : Migration de sox > /tmp/fm_stream.mp3 vers ffmpeg > Icecast2 HTTPS
- **Serveur Icecast2** : Configuration HTTPS avec certificats SSL
- **Proxy Flask** : Route `/stream.mp3` proxifie Icecast pour éviter Mixed Content
- **Stabilité 24/7** : Élimination des corruptions audio après plusieurs heures de fonctionnement
- **Multi-auditeurs** : Support de jusqu'à 100 auditeurs simultanés
- **Reconnexion automatique** : Le stream se reconnecte automatiquement en cas de coupure

#### Système de licences v3
- **Trois niveaux de licence** :
  - **Lite (gratuit)** : Streaming et VU-mètre uniquement
  - **Full Trial (30 jours)** : Toutes fonctionnalités pendant 30 jours
  - **Full Permanent (199€)** : Toutes fonctionnalités à vie
- **Clés uniques** : Chaque licence générée est unique avec HMAC SHA-256
- **Association email** : Chaque clé est liée à une adresse email unique
- **Anti-partage** : Une clé ne peut être activée qu'une seule fois
- **Multi-licences** : Un email peut avoir plusieurs licences (ex: 3 Raspberry Pi)
- **Base de données** : Traçabilité complète dans `licenses_db.json`
- **Interface d'activation** : Page `/license` pour activer et gérer les licences

### ✨ Améliorations

#### Interface utilisateur
- **Masquage frontend** : Fonctionnalités Full grisées et désactivées en mode Lite
- **Badges visuels** : Indicateurs "Full" sur les fonctionnalités premium
- **Messages CTA** : Call-to-action pour upgrade vers Trial ou Permanent
- **Sidebar cohérente** : Design unifié sur toutes les pages (Dashboard, Config, Stats, Licence)

#### Performance et stabilité
- **Rate limiting optimisé** : Exemption pour routes appelées fréquemment
  - `/stream.mp3` : Stream audio continu
  - `/api/stats` : Appelée toutes les 10 secondes
  - `/api/services/status` : Appelée toutes les 5 secondes
  - `/api/stream/stats` : SSE (Server-Sent Events) continu
  - `/api/audio/history` : Historique temps réel
- **Gestion mémoire** : Optimisation du streaming pour éviter les fuites mémoire
- **Reconnexion ffmpeg** : Gestion automatique des déconnexions Icecast

#### Sécurité
- **Certificats SSL** : Support complet HTTPS pour Icecast et Flask
- **CORS configuré** : Headers Access-Control-Allow-Origin pour le streaming
- **Validation cryptographique** : HMAC SHA-256 pour les clés de licence
- **Protection backend** : Décorateur `@license_required()` sur les routes premium

### 🔧 Corrections

#### Streaming audio
- **Corruption après plusieurs heures** : Résolu via migration vers Icecast2
- **Son métallique** : Éliminé grâce au streaming professionnel
- **Décalage 40s** : Réduit drastiquement avec Icecast
- **Glitchs audio** : Supprimés avec pipeline ffmpeg optimisé

#### Interface web
- **Mixed Content errors** : Résolu via proxy Flask HTTPS
- **Erreurs 429 (Too Many Requests)** : Routes fréquentes exemptées du rate limiting
- **Parsing JSON** : Gestion correcte des erreurs de rate limiting
- **SSE déconnexions** : Connexion Server-Sent Events stabilisée
- **Cadres verts services** : Affichage correct du statut des services

#### Backend
- **IndentationError** : Correction du fichier monitor.py corrompu
- **Module requests manquant** : Ajout à requirements.txt
- **Certificats Icecast** : Configuration bundle.pem pour compatibilité
- **Redirections /login** : Routes publiques correctement configurées

### 🛠️ Technique

#### Architecture
- **Pipeline audio** :
  ```
  RTL-SDR → rtl_fm → ffmpeg → Icecast2 HTTPS (port 8443)
                                    ↓
                              Flask proxy (/stream.mp3)
                                    ↓
                              Player Dashboard (HTTPS port 5000)
  ```
- **Streaming** :
  - Format : MP3 128 kbps
  - Codec : libmp3lame
  - Channels : Stéréo (2)
  - Sample rate : 44100 Hz
  
#### Dépendances ajoutées
- `requests>=2.32.0` : Pour le proxy stream Icecast

#### Configuration Icecast
- Port HTTP : 8000
- Port HTTPS : 8443
- Mountpoint : `/fmmonitor`
- Certificats : Bundle cert + key
- CORS : Activé (`Access-Control-Allow-Origin: *`)

### 📚 Documentation

#### Nouveaux guides
- Guide installation Icecast2 professionnel
- Migration monitor.py vers Icecast
- Système de licences complet
- Design cohérent interface

#### Fichiers de configuration
- Configuration serveur Icecast2
- Version monitor.py avec streaming Icecast
- Templates licence avec email

### 🔒 Sécurité et confidentialité

**Fichiers exclus de GitHub** (sensibles) :
- Générateur de clés (contient SECRET_KEY)
- Gestionnaire avec SECRET_KEY
- Base de données des licences
- Licence active
- Certificats SSL
- Configuration personnelle
- Bases de données

### ⚠️ Breaking Changes

- **Streaming** : Nécessite maintenant Icecast2 installé et configuré
- **Dépendances** : Module `requests` obligatoire
- **Port HTTPS** : Icecast écoute sur 8443 (configurable)
- **Certificats** : Bundle cert+key nécessaire pour Icecast HTTPS

### 🔄 Migration depuis v0.2.x

1. Installer Icecast2 : `sudo apt install icecast2 ffmpeg`
2. Configurer certificats SSL bundle
3. Modifier monitor.py pour streaming Icecast
4. Installer requests : `pip install requests`
5. Redémarrer services

---

## [0.2.0] - 2026-02-26

### Ajouté
- Système de licences Lite/Full
- Protection routes API avec `@license_required()`
- Interface de gestion des licences
- CSRF protection sur toutes les routes
- SSL/HTTPS avec certificats auto-signés

### Modifié
- Migration vers HTTPS obligatoire
- Amélioration interface Dashboard
- Optimisation performances base de données

### Corrigé
- Fuites mémoire lors du streaming
- Erreurs CSRF sur formulaires
- Problèmes de permissions fichiers

---

## [0.1.0] - 2026-02-15

### Ajouté
- Streaming audio FM via RTL-SDR
- Décodage RDS en temps réel
- VU-mètre temps réel
- Détection de silence
- Watchdog de surveillance
- Alertes email
- Historique audio
- Configuration réseau WiFi
- Dashboard web responsive
- Base de données SQLite
- Authentification HTTP Basic

### Technique
- Flask + Python 3.13
- RTL-SDR via rtl_fm
- Décodage RDS avec redsea
- Conversion audio avec sox
- Interface Tailwind CSS

---

[0.5.0]: https://github.com/LyonelB/fm-monitor/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/LyonelB/fm-monitor/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/LyonelB/fm-monitor/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/LyonelB/fm-monitor/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/LyonelB/fm-monitor/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/LyonelB/fm-monitor/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/LyonelB/fm-monitor/compare/v0.2.0...v0.3.1
[0.2.0]: https://github.com/LyonelB/fm-monitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/LyonelB/fm-monitor/releases/tag/v0.1.0
