# Changelog

Toutes les modifications notables de ce projet seront documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Non publié]

---

## [1.0.0] - 2026-11-20

Première version stable : réception FM et DAB+, chaîne de diffusion, parc et réseau Dante dans une seule interface, en français ou en anglais.

**Points forts**
- **Réception DAB+** (reprise de BL-DMO) : page DAB+, station suivie, alertes, pige, récepteurs USB.
- **Navigation par domaine** : FM, DAB+, Chaîne, Dante, Parc et Système, chacun avec son journal, sa pige et sa configuration.
- **Page Parc** : PC de production, NAS Synology et sites web.
- **Version anglaise** de l'interface, des mails et du README.
- **Surveillance Dante** : appareils, abonnements, horloge et niveaux par canal.
- **Agent BL-RMO** pour les PC Windows : installation simple, relevés système et Dante, réglés depuis BL-RMO.
- **Noms bl-rmo** : dossier, services et adresse, avec un script de migration pour les installations existantes.

Le mode RTL-SDR en FM, l'analyse MPX et Docker sont toujours présents mais ne sont plus développés ; ils seront retirés dans la prochaine version.

### Agent BL-RMO pour les PC Windows
- **Un agent à installer sur les PC du parc** (`agent/agent_bl_rmo.py`), qui envoie ses relevés à BL-RMO toutes les minutes (`POST /api/agent/report`, jeton propre à chaque PC). Les modules se choisissent dans BL-RMO et arrivent dans la réponse : rien à refaire sur le PC.
  - **Système** : allumé depuis, processeur (moyenne sur la minute), mémoire, disques fixes. Même relevé qu'en SNMP, mêmes alertes (éteint, redémarré, disque, processeur), sans SNMP à activer ni communauté.
  - **Dante** : la lecture netaudio de la sonde Dante, envoyée à la page Dante. L'agent relance netaudio pour chaque lecture : son moteur privé s'arrête aussitôt après.
- **Installation simple** (`agent/setup-agent-bl-rmo.iss`, Inno Setup, français ou anglais selon Windows) : adresse de BL-RMO et jeton du PC, puis installation dans `C:\Program Files\BL-RMO Agent`, réglages dans `C:\ProgramData\BL-RMO\agent` (SYSTEM et administrateurs seulement), règle du pare-feu pour netaudio, tâche planifiée « BL-RMO Agent » (SYSTEM, au démarrage) et premier envoi de test, dont le résultat s'affiche à la fin. Retire l'ancienne sonde Dante. Désinstallation depuis « Applications installées ». Installation silencieuse : `/VERYSILENT /URL=… /TOKEN=…`.
- **Construction par GitHub Actions** (`.github/workflows/agent.yml`) : exe PyInstaller (Python, psutil et netaudio embarqués, rien à installer sur le PC) puis installeur. Artefact à chaque modification de `agent/`, version GitHub « Agent BL-RMO x.y.z » au tag `agent-vx.y.z`.
- **Parc › Appareils** : pour un PC, relevé « Agent BL-RMO installé sur le PC » ou « SNMP ». Avec l'agent : case Dante, jeton (afficher, copier, nouveau jeton) et étapes d'installation. État : « Agent ne répond plus » quand le PC répond sur le réseau sans relevé de l'agent, version de l'agent à côté de l'adresse.
- **Niveaux Dante** (agent 1.1.0) : le module Dante lit aussi les niveaux (`netaudio meter --snapshot --detailed`), une fois par minute. Page Dante : section « Niveaux » (crête par canal en dBFS, de -60 à 0 dB, muet, silence, écrêtage) pour les canaux nommés, abonnés ou portant du signal, et colonne « Niveau » dans les abonnements. Sur le réseau de Graffiti : niveaux détaillés de la DHD (64 entrées, 64 sorties), présence de signal seulement pour dhd-core ; Dante Virtual Soundcard ne transmet pas de niveaux.
- **Agent 1.0.1** : la tâche planifiée « BL-RMO Agent » n'était pas créée par l'installeur 1.0.0 (variable `$action` confondue avec le paramètre `-Action` du script, PowerShell ignorant la casse).
- **Page Dante, onglet Sonde** : installation par l'agent ; l'ancienne sonde en script PowerShell reste acceptée, dans une section repliée.

### Surveillance Dante, étape 1 : visualisation
- **Page Dante** (menu, entre Chaîne et Parc), onglet **État** : synthèse (réseau, appareils, abonnements, horloge), liste « À vérifier », appareils (état, produit, adresse IP, rôle d'horloge, latence, fréquence, canaux) et abonnements (récepteur, source, état, transport).
- **Sonde Dante** (`install/windows/sonde-dante.py`) : sur un PC Windows du réseau Dante, lit toutes les minutes `netaudio status` et `netaudio clock`, en lecture seule, et envoie le relevé compressé à `POST /api/dante/report` avec son jeton. Pas de service netaudio lancé, aucun port ouvert sur le PC.
- **Installeur** (`install/windows/installer-sonde-dante.ps1`, PowerShell en administrateur) : vérifie Python 3.10 ou plus (hors Microsoft Store), installe netaudio 0.6.1, copie la sonde dans `C:\ProgramData\BL-RMO\sonde-dante`, demande le jeton, restreint le fichier de réglages à SYSTEM et aux administrateurs, crée la tâche planifiée « BL-RMO Sonde Dante » (SYSTEM, au démarrage). `-Desinstaller` pour la retirer.
- **Référence** : tout appareil et tout abonnement vus entrent dans la référence ; s'ils disparaissent, ils sont signalés. Signalés aussi : abonnement en erreur, aucun ou plusieurs leaders d'horloge, leader changé, horloge non synchronisée, sonde muette depuis plus de 3 minutes.
- **Onglet Sonde** (administrateur) : jeton (afficher, copier, nouveau jeton), commandes d'installation, état de la sonde et versions, « Prendre l'état actuel comme référence ».
- **Stockage** : table `dante_kv` de `fm_monitor.db` (jeton, dernier relevé, référence). Lecteur : consultation de la page ; réglages réservés à l'administrateur.
- Traduction anglaise de la page. Alertes, journal et tuile du tableau de bord : étape suivante.

### Traduction anglaise, étape 4 : Système, À propos, mails, messages du serveur et README anglais
- **Pages traduites** : Système (Général, Comptes, Journal BL-RMO, Sauvegarde), À propos et l'assistant de configuration initiale ; le rôle du compte dans le menu.
- **Mails d'alerte dans la langue de BL-RMO** : objet, cadre et type d'alerte, et les lignes de détail connues (FM, DAB+, chaîne, parc). La langue est lue au moment de l'envoi ; un mail en attente part dans la langue en vigueur.
- **Messages du serveur traduits** : réponses de l'API (« Configuration enregistrée », erreurs de saisie...), messages des sondes, détails d'alerte et événements du parc affichés dans les journaux.
- **Modèles de traduction** (section `_patterns` de `i18n/en.json`) : un texte composé avec des valeurs (« RDS absent depuis 120s », « Chaîne : DB91-RX injoignable ») est reconnu et traduit, côté serveur (`i18n.tr()`) comme dans le navigateur (`_t()`).
- **README.en.md** : README en anglais, relié depuis le README français.
- Textes : tirets cadratins retirés de l'assistant de configuration et d'un message de l'API.

### Traduction anglaise, étape 3 : Chaîne et Parc
- **Chaîne de diffusion traduite**, ses quatre onglets : Synoptique (panneau de détail, courbes, réglages), Journal (alertes, messages syslog et traps), Appareils (formulaires de chaque type de sonde) et Configuration (règles de masquage, aide syslog, événements externes).
- **Parc traduit** : État (PC, NAS, sites web), Journal et Appareils.
- **Valeurs venues des appareils** (libellés de mesures, messages de sonde, états du NAS) traduites à l'affichage quand elles sont connues. Les modèles SNMP gardent leurs libellés d'origine, enregistrés tels quels avec l'appareil.
- **Outils** : `tools/i18n_wrap.py` traduit aussi le texte collé à un bloc `<code>` ; `tools/i18n_js.py` laisse de côté les valeurs CSS et les adresses.

### Traduction anglaise, étape 2 : pages FM et DAB+
- **Pages FM et DAB+ traduites**, tous onglets : Direct, Journal, Pige et Configuration, avec les messages affichés par le JavaScript (états, tuiles, journal, pige, détection des récepteurs, balayage de la bande III).
- **Libellés des alertes** (Signal perdu, Absence modulation, Silence audio...) et mode de décodage DAB+ traduits à l'affichage ; les filtres du journal gardent leur valeur d'origine.
- **Analyseur de spectre** (`static/spectrum.js`) : boutons et messages traduits, séparateur décimal de la langue. Barres G/D affichées L/R en anglais, aussi sur le tableau de bord.
- **Outil** `tools/i18n_js.py` : entoure les textes français du JavaScript des modèles de `_t('…')` (`--dry` pour la liste), sans toucher au code, aux classes CSS ni aux gabarits à valeurs, traduits à la main.

### Traduction anglaise, étape 1 : mécanisme, choix de la langue, menu, connexion et tableau de bord
- **Choix de la langue** dans Système › Général : Français ou English, un réglage pour tout BL-RMO (`config.json`, `"ui": {"language": "fr" | "en"}`), enregistré dès le choix sans redémarrer la surveillance. Les mails suivront la même langue (étape 4).
- **Mécanisme** (`i18n.py`, `i18n/en.json`, `templates/_i18n.html`) : le texte français sert de clé et `i18n/en.json` donne sa traduction ; un texte non traduit reste en français. Dans les modèles `{{ _('…') }}`, dans le JavaScript `_t('…')`, en Python `i18n.t('…')`. Les dates, le séparateur décimal et les unités (Go, Mo, j) suivent la langue.
- **Traduits** : le menu, les onglets de la Chaîne, la page de connexion (messages d'erreur compris) et le tableau de bord. Les autres pages suivent aux étapes 2 à 4 (FM et DAB+, Chaîne et Parc, Système, À propos, mails et README.en.md).
- **Outil** `tools/i18n_wrap.py` : entoure les textes visibles d'un modèle de `{{ _('…') }}` et liste les textes à traduire (`--keys`).

### Lecteur FM épuré
- **Écoute et spectre (page FM)** : la mention « Son reçu par le tuner, en direct » et le suffixe « · Antenne en direct » sont retirés, comme sur le lecteur DAB+. Le lecteur affiche le PS de la station, sinon la fréquence reçue.

### Chaîne, onglet Appareils allégé
- **Doublons supprimés** : les cartes des appareils (état, mesures, courbes) et le tableau des sondes avec leurs mesures reprenaient le Synoptique ; le second tableau « Sondes réseau » doublait le premier ; le résumé et le bouton d'ajout de l'en-tête doublaient ceux des tableaux.
- **Trois tableaux de même forme** : Appareils de la chaîne (ordre du signal), Sondes réseau et Capteurs de site. Chacun a son bouton d'ajout, une ligne d'explication, et par élément : ordre, état (pastille, détail au survol), nom, site, type de sonde, cible, intervalle, mail, « Sonder maintenant » et « Modifier ». Les capteurs se réordonnent aussi.
- L'état en direct, les mesures et l'historique restent sur le Synoptique.

### Navigation regroupée par domaine, étape 4 : Système et nouveau menu
- **Nouveau menu**, le même sur toutes les pages (`templates/_sidebar.html`) : Dashboard, FM, DAB+, Chaîne, Parc et Système ; À propos, GitHub, l'utilisateur connecté et Déconnexion en bas. Configuration, Pige, Journaux et Utilisateurs disparaissent du menu : leur contenu est rangé dans les pages de chaque domaine.
- **Page Système** (`/system`) en quatre onglets : **Général** (alertes email, réseau, volume DAC+, enregistrement et redémarrage de la surveillance), **Comptes** (mon compte, niveaux d'accès, comptes et rôles), **Journal BL-RMO** (services, erreurs, journal de chaque service) et **Sauvegarde** (export et import de la configuration). Général et Sauvegarde sont réservés à l'administrateur ; les autres rôles y trouvent leur compte et le journal de BL-RMO.
- **Doublon supprimé** : la rubrique « Authentification » de l'ancienne Configuration (nom et mot de passe) faisait doublon avec les comptes ; chacun change son mot de passe dans Système, Comptes.
- **Anciennes adresses** : `/config` mène à Système, Général ; `/account` à Système, Comptes ; `/pige` à l'onglet Pige de la page FM (ou DAB+) ; `/logs` à l'onglet Journal du bon domaine (`#reception` FM, `#dab` DAB+, `#chaine` Chaîne, `#blfmo` Système, filtre `?src=` conservé). Les pages `config.html`, `account.html` et `logs.html` sont supprimées.
- **Tableau de bord** : la pastille Pige ouvre la pige FM ; la pastille « Alertes 24 h » n'est plus un lien (pas de vue globale : le détail est dans le Journal de chaque page).
- **README et À propos** : navigation par domaine, une pige par réception, station DAB+ suivie, « Détecter les récepteurs » dans l'onglet Configuration de la page FM.

### Navigation regroupée par domaine, étape 3 : Chaîne et Parc
- **Chaîne de diffusion en quatre onglets** : Synoptique (`/chain`), Journal (`/chain/journal`), Appareils (`/chain/appareils`, administrateur) et Configuration (`/chain/configuration`, administrateur), avec la même barre d'onglets sur chaque page. Le bouton « Gérer la chaîne » disparaît ; `/chain/manage` mène à l'onglet Appareils.
- **Journal de la chaîne** : une seule liste « Alertes et changements d'état » remplace les « Alertes de la chaîne » de la page Journaux et le « Journal des changements d'état » de Gérer la chaîne. Elle regroupe les appareils, sondes et capteurs, et les événements des équipements tiers (netwatch, scripts : badge « externe »), avec filtre, export CSV, archive ZIP des journaux (sur une durée, ou ±10 min autour d'une alerte) et « Écouter » qui lit la pige FM pendant l'alerte. En dessous, les messages des appareils (syslog et traps SNMP), repris de la page Journaux.
- **Appareils** : les appareils dans l'ordre du signal, les sondes réseau et les capteurs de site (ancienne page Gérer la chaîne, sans son journal).
- **Configuration de la chaîne** : messages masqués (règles), envoi des journaux d'un appareil vers BL-RMO (syslog UDP 514, et maintenant les traps SNMP sur UDP 162) et événements externes (URL, jeton, exemple RouterOS).
- **Parc en trois onglets** : État (`/park#etat`), Journal (`/park#journal`) et Appareils (`/park#appareils`, administrateur). Le Journal remplace les « Derniers événements » : tous les événements des 90 derniers jours, filtres par appareil et alertes seules, export CSV (nouvelle route `/api/park/events`). Le formulaire des appareils passe dans l'onglet Appareils.
- **Liens** : le « Dernier événement » de la chaîne sur le tableau de bord mène au Journal de la chaîne ; « Tous les journaux de cet appareil » du synoptique ouvre ce Journal filtré sur l'appareil. La page Journaux reste en place jusqu'à l'étape 4.

### Mails d'alerte FM par type
- **Page FM, onglet Configuration** : une rubrique « Mails d'alerte », comme en DAB+, avec une case par type d'alerte : émetteur non capté, absence de modulation, RDS absent, RadioText absent (et sur-déviation avec une clé RTL-SDR). Une alerte décochée est enregistrée dans le journal et sur le tableau de bord comme les autres, sans mail (Mails = 0), et son rétablissement aussi. Les cases sont enregistrées dans `config.json` (`audio.email_kinds`) et lues à chaque alerte, sans redémarrage.

### Nom de la station selon la réception
- **Mails d'alerte** : la station est nommée par son PS (Program Service) reçu en FM, et par le nom du service de la station suivie en DAB+ (« ⚠️ ALERTE - GRAFFITI - Absence modulation »). Si le signal FM est perdu, le dernier PS reçu est gardé. Les mails de la chaîne, du parc et le mail de test gardent le nom de `config.json`, sinon « BL-RMO ».
- **Configuration** : la fenêtre « 1. Station » (nom de la station) est supprimée ; les rubriques sont renumérotées.
- **Page DAB+, onglet Direct** : la fenêtre « Services du multiplex » est supprimée (une seule station surveillée, choisie dans l'onglet Configuration, dont la liste reprend tous les services). Le nombre de services du multiplex passe dans la tuile Ensemble.

### Une seule station DAB+ surveillée, onglet Configuration
- **Plus de doublon avec la colonne « Surveillé »** : on surveille une seule station DAB+, celle choisie dans « Station suivie » (onglet Configuration). Les cases à cocher du tableau « Services du multiplex » disparaissent ; la station suivie y porte un badge, les autres services sont seulement listés (présence, niveau, diffusion, écoute). Enregistrer la configuration fait de la station suivie le seul service surveillé (alertes silence et absence, décodage continu, pige).
- **Libellés** : décodage « Station suivie seulement (conseillé) », mails « Silence audio de la station suivie » et « Station suivie absente du multiplex ».
- **Onglet « Réglages » renommé « Configuration »** sur les pages FM et DAB+ (`/fm#configuration`, `/dab#configuration`) ; les anciens liens `#reglages` mènent toujours au bon onglet.

### Logo de la station FM
- **Écoute et spectre (page FM)** : le logo de la station s'affiche à côté du bouton de lecture, comme le diaporama en DAB+. C'est le logo déjà trouvé par le PI dans rds-station-db pour le tableau de bord ; il disparaît quand le signal est perdu ou qu'aucun logo n'est connu. Même cadre qu'en DAB+ (64 × 48 px, image entière sans recadrage), pour le logo FM comme pour le diaporama DAB+.

### Une pige par réception
- **Réception DAB+ comme en FM** : la case « Activer la réception DAB+ » disparaît. La réception tourne dès qu'un récepteur est choisi (clé RTL-SDR ou dongle Si468x) ; le nouveau choix « Aucun : pas de réception DAB+ » l'arrête, avec sa pige.
- **Station suivie** : « Station suivie (tableau de bord et pige) » (anciennement « Service du tableau de bord ») est cochée d'office dans les services surveillés, donc décodée en continu, et enregistrée par la pige DAB+.
- **Une seule pige DAB+** : elle enregistre la station suivie. Sans station suivie, pas de pige (plus de repli sur le premier service surveillé). Changer de station change ce qu'elle enregistre ; les enregistrements déjà faits restent jusqu'à la fin de leur conservation. Avec le dongle Si468x, la station suivie est le service décodé.
- **Réglages réduits** : pour la FM comme pour le DAB+, la durée de conservation et un bouton « Arrêter la pige » / « Démarrer la pige ». La création, la suppression et le choix du service d'une pige DAB+ disparaissent, avec le menu « Service » de l'onglet Pige. L'API `/api/pige/config` n'accepte plus que `update` (conservation, marche ou arrêt).
- **Reprise de l'existant** : la pige DAB+ déjà créée garde son identifiant et ses enregistrements (`pige/extra/<id>`) ; ses réglages passent dans `config.json` sous `"pige"."dab"` au premier enregistrement. Les autres piges de l'ancienne liste `"extra"` ne sont plus enregistrées.
- **Service de pige** : bl-rmo-pige reste lancé même sans pige active, pour qu'une pige arrêtée puis redémarrée reprenne sans redémarrage du service. Les enregistrements d'une pige arrêtée (ou d'une pige DAB+ sans station) partent eux aussi à la fin de leur conservation.
- **Page Pige supprimée** : `/pige` mène à l'onglet Pige de la page FM, ou de la page DAB+ pour un lien vers la pige DAB+ (`?p=`).

### Navigation regroupée par domaine, étape 2 : la page DAB+
- **Page DAB+ en quatre onglets**, chacun avec son adresse : Direct (`/dab#direct`), Journal (`/dab#journal`), Pige (`/dab#pige`) et Réglages (`/dab#reglages`, administrateur), sur le modèle de la page FM.
- **Direct** : tuiles, écoute et spectre, les 5 derniers DLS du service suivi, services du multiplex. Le bloc « Derniers événements » est supprimé (doublon du Journal). L'écoute s'arrête automatiquement après 15 min, comme en FM (« En écoute (arrêt automatique après 15 min) »). Le spectre n'est calculé que sur l'onglet Direct, l'état du multiplex est relu toutes les 2 s sur Direct et toutes les 15 s ailleurs.
- **Journal** : les alertes DAB+ (filtres par type et par service, export CSV, liens vers la pige) et tout l'historique du DLS par service, repris de la page Journaux ; alertes avec mail sur 30 jours et « Tout est OK depuis ». Un DLS encore dans la pige a son lien « Écouter ».
- **Pige** : la même fenêtre simple qu'en FM (date, heure de début, heure de fin, Lecture, Télécharger, disponibilité sur une ligne), avec le choix du service quand plusieurs piges DAB+ existent. En dessous, les réglages des piges DAB+ (administrateur) : liste, conservation, suspendre, supprimer, créer la pige d'un service.
- **Réglages** : réception et seuils d'alerte, mails d'alerte, puis récepteurs et balayage de la bande III.
- **Liens** : « Écouter » sur une alerte DAB+ ouvre l'onglet Pige et lit tout le créneau de l'alerte (de 30 s avant à 30 s après le rétablissement). Les anciens liens `/pige?p=<pige DAB+>&t=` mènent à l'onglet Pige de la page DAB+ ; la dernière alerte DAB+ du tableau de bord mène au Journal DAB+.

### Navigation regroupée par domaine, étape 1 : la page FM
- **Page FM en quatre onglets**, chacun avec son adresse : Direct (`/fm#direct`), Journal (`/fm#journal`), Pige (`/fm#pige`) et Réglages (`/fm#reglages`, administrateur). Tout ce qui concerne la FM est au même endroit.
- **Direct** : tuiles, écoute et spectre, les 5 derniers RadioText ; « Tout l'historique » mène au Journal. Le bloc « Derniers événements » est supprimé (doublon du Journal). Le spectre n'est calculé que sur l'onglet Direct.
- **Journal** : les alertes de la réception FM (filtre par type, export CSV, liens vers la pige) et tout l'historique du RadioText (export, « Afficher plus »), repris de la page Journaux ; alertes avec mail sur 30 jours et « Tout est OK depuis ». Un RadioText encore dans la pige a son lien « Écouter ».
- **Pige** : fenêtre simplifiée, date, heure de début, heure de fin, Lecture et Télécharger, avec la disponibilité sur une ligne (« Disponible du … jusqu'à maintenant »). Une fin avant le début passe au lendemain. La frise des 30 jours n'est pas reprise. En dessous, la durée de conservation de la pige FM (administrateur). La liste « Extraits des dernières alertes » est supprimée : elle doublait le Journal (avec des heures différentes) et mêlait les alertes de la chaîne à celles de la FM.
- **Un seul lecteur** : « Écouter » sur une alerte du Journal ouvre l'onglet Pige et lance la lecture du créneau de l'alerte, de 30 s avant à 30 s après le rétablissement (4 h au plus). Une alerte sortie de la pige est lue depuis son extrait archivé, dans le même lecteur, avec un bandeau qui le signale ; « Télécharger » donne alors cet extrait. Un lien vers un moment sorti de la pige (chaîne, ancien lien) retrouve aussi l'extrait archivé qui le couvre. Plus de lecteur séparé en bas de la page.
- **Journal FM** : « Retour seul » (rétablissement enregistré sans le début de l'alerte) n'affiche plus une durée de « 0 s » ; le compteur indique « parmi les 500 plus récentes » quand la liste est tronquée.
- **Réglages** : fréquence, seuils et délais d'alerte, source de réception et « Détecter les récepteurs ».
- **Liens** : « Écouter » d'un RadioText ouvre l'onglet Pige 30 s avant, sans recharger la page. Les anciens liens `/pige?t=` (Journaux, chaîne) mènent à l'onglet Pige de la page FM ; `/stats` et la dernière alerte FM du tableau de bord mènent au Journal FM. Les pages Pige et Journaux restent en place jusqu'à la fin du regroupement.

### DAB+ : récepteurs USB et balayage de la bande III (dongle Si468x, essai)
- **Dongle Si468x comme récepteur DAB+** (réglage « Récepteur » : « Clé RTL-SDR + welle-cli » ou « Dongle Si468x (essai) ») : même fonctionnement qu'avec welle-cli, la surveillance d'une station et l'écoute. `si468x_server.py` pilote le dongle par son port série et sert la même API que welle-cli (`/mux.json`, `/mp3/<sid>`, `/slide/<sid>`, `/status`) : alertes, écoute, pige DAB+, DLS et diaporama fonctionnent sans changement. Le Si4684 ne décode qu'un service (celui du tableau de bord, sinon le premier coché) ; les autres services du multiplex restent suivis (présence), sans son. Son lu sur la carte son du dongle (`hw:DAB`), niveaux mesurés sur le PCM, MP3 encodé par ffmpeg pour tous les auditeurs (surveillance, écoute, pige). Qualité FIC et CNR (MER) donnés par la puce, affichés « CNR (MER) » à la place du SNR. Le protocole ne donnant que le numéro local des services, leur SID est appris au démarrage (une seconde par service) puis gardé en cache par ensemble (`si468x_sids.json`). Synchronisation perdue plus de 5 s : ensemble et services absents (alertes habituelles), nouveau réglage du canal après 60 s ; dongle muet 15 s : relance par le service DAB+.
- **Dongle Si468x, premiers essais** : après un réglage de canal, la liste des services et la synchronisation ne sont lues qu'après l'accusé du dongle (`*TUNE=`) ; avant, la liste du multiplex précédent (Métropolitain 1, laissé par le balayage) pouvait être prise pour celle du 9A. SID des services : le numéro ID de `$I` n'est pas celui de la liste `$L` (SERVICE=2 répond ID=3, SERVICE=12 répond ID=18) ; le `$I` reçu après l'accusé `*SERVICE=` est donc attribué au service demandé, ce qui donnait des SID manquants ou décalés. Détail des réponses du dongle dans le journal quand un SID ne vient pas ; cache des SID en version 2 (l'ancien est ignoré). Textes défilants et image remis à zéro à chaque changement de service.
- **Dongle Si468x, firmware de novembre 2026** (mc_popa, à partir de nos retours) : les SID arrivent par la nouvelle ligne `$SIDS=` après la liste des services, sans sélectionner chaque service au démarrage (plus de coupure de 25 s) ; FIBERR (erreurs FIC cumulées depuis le réglage), CU (unités de capacité décodées) et ALVL (niveau audio après soft mute, expérimental) lus dans `$S` ; SNR signé de la puce exposé à côté du CNR. Les firmwares précédents restent pris en charge (apprentissage des SID service par service).
- **README** : captures d'écran du tableau de bord, de la réception FM et DAB+, de la chaîne et du parc (`docs/captures/`), réalisées sur une instance de démonstration fictive (« Radio Exemple ») ; présentation étendue au DAB+, au parc et à l'écoute.
- **Page À propos** : présentation à jour (FM et DAB+, parc, pige de 30 jours), cartes « Réception DAB+ », « Parc » et « Écoute », nouvelle section « La radio numérique DAB+ » (multiplex et services, DLS et diaporama, SNR, CNR et qualité FIC, les deux récepteurs possibles).
- **Page de connexion** : sous-titre « Surveillance radio FM, DAB+ et chaîne de diffusion ».
- **Dashboard** : avec le dongle Si468x, la tuile DAB+ affiche « CNR » comme la page DAB+.
- **Nginx en HTTP/2** (`http2 on;`) : en HTTP/1.1, le navigateur n'ouvre que 6 connexions par site ; les flux longs de plusieurs onglets (écoute FM ou DAB+, statistiques en direct) les occupaient toutes et la connexion restait bloquée sur « Connexion… ». Installations existantes : ajouter la ligne dans `/etc/nginx/sites-available/bl-rmo`.
- **Lecteur DAB+** : le nom du service remplace son SID (« 0xfa41 ») dès que la liste des services arrive.
- **Alertes DAB+ restées ouvertes** au redémarrage de la surveillance (ou suivies d'un nouveau défaut du même type) : « Fin inconnue » au lieu de rester « En cours » indéfiniment (page DAB+ et Journaux).
- **Bloc « Récepteurs »** sur la page DAB+ (administrateur) : « Détecter les récepteurs » liste le TEF, le dongle DAB+ Si468X Headless et les clés RTL-SDR branchés, avec leur identifiant USB, leur port série stable (`/dev/serial/by-id`), leur carte son et leur rôle. Lecture de `/sys` seulement : aucun port n'est ouvert, welle-cli et le TEF ne sont pas dérangés.
- **Balayage de la bande III** avec le dongle Si468x : les 38 canaux de 5A à 13F en 1 min 30 environ ; pour chaque multiplex trouvé, l'ensemble, son EId et son ECC, le CNR, la qualité FIC et ses services. Un canal n'est retenu qu'avec synchronisation et ensemble (pas de verrouillage fantôme), et la liste des services est attendue avec ses noms décodés. Le dongle n'est ouvert que pendant le balayage : la clé RTL-SDR et welle-cli continuent la surveillance.
- **FM** : si `/dev/ttyACM0` n'est pas le TEF (dongle DAB+ branché à côté, ordre des ports inversé au redémarrage), BL-RMO ouvre le port stable du TEF, vérifié à chaque reconnexion ; TEF débranché, aucune commande FM n'est envoyée au dongle DAB+. « Détecter les récepteurs » de la page FM ne propose plus que les TEF, par leur port stable, et plus tous les `/dev/ttyACM*`.

### Chaîne : flux web écouté, flèche « Diffusion »
- **Nouvelle sonde « Flux audio »** : à chaque relevé, BL-RMO écoute le flux quelques secondes, comme un auditeur. Son reçu ou non, niveaux crête et moyen, silence prolongé (seuil et durée réglables, 60 s par défaut), titre en cours (métadonnées ICY), auditeurs et débit lus sur la page d'état Icecast quand elle est publique. Un point de montage « présent » sur le serveur peut ne diffuser que du silence : seule l'écoute le voit. Exemple : le flux web de Graffiti sur le serveur de la Frap, en tuile « Flux web » dans le bloc Réception.
- **Synoptique** : à l'entrée du bloc Réception, une simple flèche « Diffusion » remplace le bandeau de la liaison hertzienne, qui reprenait en double l'état et les mesures de la réception FM et laissait croire que le DAB+ et le flux web sortent de l'émetteur FM. Pas de flèche entre les réceptions (FM, DAB+, flux web), arrivées en parallèle.

### Petites améliorations
- **DAB+** : un service présent dans le multiplex mais non décodé (décodage « Services surveillés seulement ») est affiché « Non surveillé », en gris.
- **Parc** : le bloc d'ajout s'appelle « Appareils et sites surveillés ».
- **Journaux** : les blocs « Titres diffusés (RadioText) » et « Titres diffusés (DLS) » se replient, état mémorisé dans le navigateur.
- **Chaîne** : au-delà de 24 h, la durée d'un état s'affiche en jours et heures (« 13 j 4 h ») au lieu d'un compteur d'heures ; la date exacte reste au survol et dans le détail.
- **Pige** : après une coupure de la source, les 2 à 5 s que ffmpeg met à se reconnecter sont comblées par du silence, comme le reste de la coupure : plus de petits trous dans la pige (vus sur la pige DAB+ le 26/10), ni d'extrait signalé incomplet pour si peu.
- **Pige** : la lecture ne saute plus d'une ou deux secondes au changement de segment (11:49:58 puis 11:50:00). ffmpeg coupe parfois un segment une fraction de seconde avant l'heure ronde et son nom, tronqué à la seconde, reculait d'une seconde : le début est recalé sur l'heure ronde. Aucun son n'était perdu.
- **Chaîne** : la sonde « Flux audio » attend jusqu'à 10 s la page d'état du serveur Icecast (38 points de montage à la Frap), au lieu du délai de l'appareil (3 s) : le nombre d'auditeurs ne disparaît plus quand le serveur est lent.
- **Chaîne** : descriptions des sondes plus claires dans l'ajout d'un appareil : « Icecast » lit la page d'état du serveur sans écouter, « Flux audio » écoute le flux.
- **README** : décodage DAB+ conseillé, réglage du gain de la clé (14,4 dB sur une NESDR SMArTee), filtre coupe-bande FM en option.

### Parc : délai avant alerte, SNMP muet distingué d'une panne
- **Délai avant alerte** réglable par appareil (« Alerte après », 3 min par défaut) : un relevé SNMP manqué n'envoie plus de mail. Le 28/10, le NAS, très chargé par les transferts d'archives, avait produit une douzaine d'alertes « injoignable » de 1 à 3 min.
- **SNMP muet ou appareil injoignable** : quand SNMP ne répond plus, BL-RMO vérifie si l'appareil répond encore sur le réseau (ping, ou un port courant comme le partage de fichiers, Windows bloquant souvent le ping). S'il répond : état « SNMP ne répond plus », mail dédié, sans le compter éteint ; sinon : « injoignable » (24 h/24 et NAS) ou « éteint ».
- **Redémarrage** reconnu à l'heure de démarrage annoncée par l'appareil : un retour après un silence SNMP n'est plus pris pour un redémarrage, et « Démarré » / « Allumé depuis » restent justes (24 h/24 et NAS). Les autres PC gardent l'heure à laquelle BL-RMO les voit revenir (démarrage rapide de Windows).
- SNMP plus patient (3 s, 2 nouveaux essais) ; si le NAS ne renvoie pas à temps la table des disques, des volumes ou du processeur, les valeurs précédentes sont gardées au lieu de le compter injoignable.

### Chaîne : plus de « dégradé » pour un paquet SNMP perdu
- Les appareils qui ne renvoient qu'une valeur par requête (émetteur BW Broadcast) sont interrogés OID par OID, sans nouvel essai : un seul paquet perdu sur la liaison vers le pylône donnait « OID absent » et un mail « dégradé » (BW TX600, Régulation, le 27/10 à 17:47). Chaque requête perdue est maintenant retentée une fois ; sans réponse, la valeur est notée « pas de réponse » sans changer l'état. « OID absent » ne reste que si l'appareil répond que l'OID n'existe pas.

### Nouvelle page Parc : PC de production, NAS et sites web
- **PC Windows** (SNMP, HOST-RESOURCES-MIB) : allumé ou éteint et depuis quand, processeur (moyenne des cœurs, maximum depuis l'allumage), occupation des disques fixes. « 24 h/24 » : injoignable et redémarrage = alerte ; les autres PC peuvent être éteints (simple information). « Allumé depuis » est noté par BL-RMO, car avec le démarrage rapide de Windows la durée de fonctionnement ne repart pas de zéro après un arrêt.
- **NAS Synology** (SNMP v2c ou v3, SYNOLOGY-MIB) : système, alimentation, ventilateurs, température, disques (état et santé), volumes (état et remplissage), mise à jour DSM, processeur et mémoire.
- **Sites web** : réponse HTTPS (certificat vérifié), texte attendu, temps de réponse, disponibilité sur 24 h.
- Alertes par mail à l'apparition et au retour : injoignable, redémarrage, disque presque plein, processeur saturé, défauts du NAS, site en erreur ou lent ; journal des événements sur la page.
- Appareils ajoutés et réglés sur la page (administrateur), bouton Tester ; tables `park_devices`, `park_events`, `park_samples` dans `fm_monitor.db`.
- `install/windows/activer-snmp-bl-rmo.ps1` : installe le service SNMP de Windows 10 ou 11, en lecture seule et réservé à BL-RMO, et ouvre le pare-feu pour lui seul.
- Lien « Parc » dans le menu de toutes les pages.

### Connexion refusée après une fin de session
- Après une déconnexion pour inactivité, la connexion échouait (« Erreur de connexion au serveur ») tant que la page n'était pas rechargée : le jeton de sécurité (CSRF) de la page de connexion avait expiré (onglet resté ouvert plus d'une heure), ou avait été effacé par un autre onglet BL-RMO qui interrogeait le serveur au moment où la session expirait.
- La page de connexion demande un jeton frais à chaque essai et réessaie une fois si le jeton est refusé ; la fin de session ne retire plus que la connexion, pas le jeton.
- Messages lisibles au lieu de « Erreur de connexion au serveur » : jeton expiré, trop de tentatives (5 par minute), autre erreur du serveur. Bandeau « Session fermée après 60 minutes d'inactivité » quand on arrive sur la page après une fin de session.

### DAB+ : une alerte par incident, canal dans les mails
- Délai de rétablissement (`restore_delay`, 30 s) pour les alertes silence, ensemble dégradé et service absent : le retour à la normale doit durer 30 s avant le mail de rétablissement, et une nouvelle coupure dans l'intervalle prolonge l'alerte en cours. Le 26 octobre, une réception hésitante de 5 min avait envoyé 4 alertes et 4 rétablissements.
- Les mails DAB+ indiquaient la fréquence FM de la station (« Fréquence: 88.6M ») : ils affichent maintenant le canal DAB+ (« Canal DAB+: 9A (202,928 MHz) »). Les retours à la ligne des détails sont conservés dans la version HTML du mail.

### DAB+ : décodage « Services surveillés seulement »
- Nouveau choix de décodage, conseillé pour surveiller une radio : welle-cli est lancé sans `-D` ni `-C` et ne décode que les services cochés (à défaut, celui du tableau de bord). BL-RMO garde ouvert un flux sur chacun d'eux : ils sont mesurés en continu, sans le trou d'un tour de carrousel (10 s par service), et la détection du son figé s'applique comme en décodage complet.
- Charge processeur d'un seul décodeur : le décodage complet occupait plus de deux cœurs (welle-cli réencode chaque service du multiplex en MP3), charge moyenne à 9 sur 4 cœurs constatée. SNR, FIC et présence de tous les services restent suivis.
- L'écoute n'épingle plus le service dans ce mode (hors carrousel, un service épinglé restait décodé indéfiniment) ; la pige DAB+ fonctionne dans ce mode.
- Un service que welle-cli ne décode pas (plus de niveau dans `/mux.json`) n'est plus affiché en « Silence » : c'était le cas de tous les services non surveillés après le passage du carrousel à ce mode. Le premier relevé d'un service n'est plus pris pour un niveau neuf non plus.

### DAB+ : journal de welle-cli limité à 20 Mo
- welle-cli écrit sans arrêt dans `logs/welle-cli.log` (une ligne par surcharge du convertisseur ou perte de synchronisation) : 139 Mo en 16 h constatés avec une NESDR SMArTee réglée trop fort. Le journal n'était renouvelé qu'au redémarrage de welle-cli ; il est désormais copié en `.1` et vidé dès qu'il dépasse 20 Mo, sans relancer welle-cli.

### Page DAB+ : spectre pendant l'écoute, niveaux efficaces
- Pendant l'écoute, le spectre est calculé **dans le navigateur** sur le son écouté (même échelle que celui de BL-RMO), sans pige ni flux de plus sur welle-cli. Hors écoute, il lit la pige DAB+ du service comme avant ; si elle n'enregistre pas, la page le dit et propose de lancer l'écoute.
- Barres G/D pendant l'écoute en niveau efficace, comme la FM et le tableau de bord : la crête collait à 0 dBFS sur une antenne traitée par un processeur.

### DAB+ : clé débranchée puis rebranchée
- Quand la clé RTL-SDR disparaît de l'USB, welle-cli continue de tourner sur un « Null device » et ne la retrouve jamais, même rebranchée (il fallait désactiver puis réactiver la réception DAB+). BL-RMO le détecte dans le journal de welle-cli, compte le récepteur hors service (alerte « Récepteur DAB+ hors service » après son délai, message « clé DAB+ non détectée sur l'USB ») et relance welle-cli toutes les 30 s, ou 2 s après le retour de la clé sur l'USB.

### Chaîne : case « Réception DAB+ »
- Nouveau type de sonde **Réception DAB+** (`dab_local`), pendant de « Réception FM » : reprend ce que mesure déjà la clé pour le service suivi (ou un SId choisi) : présence, niveau et silence (son figé compris), SNR et qualité FIC, titre en cours. Aucune mesure de plus demandée à la clé. Silence et ensemble dégradé ne comptent qu'une fois l'alerte DAB+ déclenchée, après leur délai.
- Pas de flèche entre les réceptions FM et DAB+ sur le synoptique : ce sont deux arrivées en parallèle.
- Mail désactivé par défaut sur cette case (les mails DAB+ partent déjà de la surveillance DAB+) ; elle sert à voir et à corréler (« En même temps que » dans le journal de la chaîne).
- Bouton « Ajouter la réception DAB+ » dans Gérer la chaîne quand elle est vide ; sinon, type « Réception DAB+ » dans l'éditeur.

### Barres G/D fluides
- Page FM : les barres suivent chaque mesure du tuner (20 par seconde), spectre coché ou non.
- Page DAB+ : pendant l'écoute, barres mesurées dans le navigateur sur le son écouté (fluides, comme avant) ; sinon mesure de BL-RMO si le spectre est coché, ou niveau relevé par la clé toutes les 2 s.

### Pages FM et DAB+ : spectre à la demande, fenêtres repliables
- Case **« Spectre »** dans « Écoute et spectre » (FM et DAB+), mémorisée dans le navigateur : décochée, l'analyseur est masqué et rien n'est calculé côté BL-RMO. Cochée par défaut sur la FM, **décochée par défaut sur le DAB+**. Sans spectre, les barres G/D restent actives : niveau du tuner pour la FM, niveau relevé par la clé toutes les 2 s pour le DAB+.
- Bouton **Réduire / Afficher** sur « Derniers événements » (FM et DAB+) et « Services du multiplex » (DAB+), mémorisé dans le navigateur ; réduite, la fenêtre des événements garde une ligne avec le dernier événement.

### Pige : enregistrement continu
- Une pige enregistre maintenant **en continu** : quand la source ne donne plus de son (émetteur DAB+ coupé, welle-cli sans signal, Icecast sans source pendant un redémarrage de bl-rmo), la pige enregistre du **silence** à la place, jusqu'au retour du son. On écoute le silence tel qu'il a été reçu, au lieu d'un trou dans la barre. Un trou ne reste que si la pige elle-même est arrêtée (service ou serveur à l'arrêt).
- Les secondes qui précèdent la détection d'une coupure (15 s sans données) sont comblées par du silence ; retour de la source vérifié toutes les 10 s, pour ne pas multiplier les connexions à welle-cli.
- Journal du service : « plus de son à la source, silence enregistré » puis « son revenu après N s ».
- Retour de la source vérifié en lisant environ 1 s de flux, avec jusqu'à 15 s d'attente : welle-cli met parfois plusieurs secondes à servir les premiers octets, et un délai trop court faisait enregistrer du silence alors que le son était revenu.

### DAB+ : le spectre ne perturbe plus la réception
- Le spectre de la page DAB+ ouvrait un flux de plus sur welle-cli : la qualité FIC tombait à 0 à chaque ouverture de la page et la réception se dégradait (silences, ensemble dégradé). Il lit désormais le segment que la **pige DAB+** du service est en train d'écrire, sans rien demander à welle-cli. Un service sans pige DAB+ n'a pas de spectre (message sur la page).

### DAB+ : émetteur coupé détecté (son figé)
- Quand la réception s'arrête (émetteur DAB+ coupé), welle-cli garde la liste des services et leurs derniers niveaux audio : BL-RMO les croyait présents et ne voyait aucun silence. Désormais, **plus aucun nouveau niveau audio = silence** :
  - multiplex entier muet (aucun service ne reçoit de son) : alerte « Silence audio » sur chaque service surveillé après le délai de silence, avec le message « Aucun son décodé depuis N s : émission DAB+ interrompue ou réception perdue », et « Ensemble dégradé » après son propre délai ;
  - décodage complet (sans carrousel) : un service surveillé dont le son ne se rafraîchit plus est traité de même, seul.
- Le rétablissement n'est annoncé que sur un vrai nouveau niveau audio.
- Cas constaté le 22 octobre entre 9 h 44 et 9 h 51 : émetteur DAB+ déconnecté, coupures visibles dans la pige DAB+ (trous de 3 min et 30 s), aucune alerte.

### Mails : file d'attente quand le réseau est coupé
- Un mail d'alerte qui ne peut pas partir (Internet coupé, Livebox qui redémarre, serveur de mail indisponible) n'est plus perdu : il est **mis en attente et retenté toutes les minutes**, dans l'ordre, pendant 24 h au plus. Le mail de rétablissement attend derrière l'alerte : on reçoit les deux, dans le bon ordre. Un mail parti en retard indique l'heure réelle de l'événement et la durée du retard.
- La file est gardée dans un fichier (`email_queue_app.json`, `email_queue_dab_monitor.json`) : elle survit à un redémarrage du service.
- Chaîne et DAB+ : la colonne Mails des journaux est mise à jour quand le mail part, même en différé.
- Connexion au serveur de mail limitée à 30 s : un serveur qui ne répond pas ne bloque plus la surveillance.
- L'email de test n'est pas mis en attente : il dit tout de suite si l'envoi fonctionne.
- Cas constaté le 22 octobre à 3 h 25 : redémarrage de la Livebox du studio, plus d'Internet pendant 2 min ; les alertes Sound4 (flux DAB+ en surcharge réseau) et DAB+ (silence sur GRAFFITI) n'avaient pas pu partir, seuls les rétablissements étaient arrivés.

### Pages FM et DAB+ : écoute, spectre, titres diffusés
- **Nouvelle page FM** (menu « FM », au-dessus de DAB+), sur le modèle de la page DAB+ :
  - quatre cases : signal (dBf, S/B, multitrajet), modulation (niveau et seuil), mode stéréo ou mono, RDS (PS et PI) ;
  - écoute de l'antenne avec barres G/D et **analyseur de spectre** ;
  - titres diffusés (RadioText) ;
  - derniers événements, avec mails et liens vers la pige ;
  - réglages FM (administrateur) : source de réception, fréquence, seuil de modulation, délais d'alerte (modulation, émetteur, RDS, RadioText). Un changement de fréquence ou de source relance la réception FM ; les délais s'appliquent tout de suite.
- **Page DAB+ réorganisée** dans le même ordre :
  - lecteur toujours affiché sur le service suivi (celui du tableau de bord), avec barres G/D et analyseur ; « Écouter » dans le tableau des services le bascule sur un autre service ;
  - titres diffusés (DLS) ;
  - derniers événements ;
  - puis le tableau des services et les réglages, comme avant.
- **Analyseur de spectre** en tiers d'octave : 31 bandes de 20 Hz à 20 kHz, échelle de 0 à -96 dBFS, segments verts, jaunes au-dessus de -30, rouges au-dessus de -12, crête tenue 2 s par bande. Calculé par BL-RMO (nouveau module `spectrum.py`) seulement pendant qu'une page FM ou DAB+ est ouverte, arrêté 30 s après : FM sur le son du tuner, DAB+ en décodant le service affiché depuis welle-cli. Flux temps réel `/api/stream/spectrum`, 10 mesures par seconde. Les barres G/D de la page DAB+ viennent de la même mesure : elles bougent sans lancer l'écoute.
- **Titres diffusés repliables** sur les deux pages (« Réduire » / « Afficher », mémorisé dans le navigateur) : 10 derniers titres, « À l'antenne » sur le titre en cours, « Écouter » ouvre la pige à ce moment.
- **Historique des textes DLS** des services surveillés (nouvelle table `dab_dls`, 1 000 lignes par service), avec la même règle que le RadioText : texte stable 8 s, texte qui revient en boucle repris après 10 minutes. Tableau « Titres diffusés (DLS) » et export CSV dans l'onglet Réception DAB+ des Journaux ; route `/api/dab/dls_history`.
- **Configuration allégée** : il reste la station (nom), les mails, le réseau, l'authentification, le volume et la sauvegarde. La source, la fréquence, les seuils et délais FM passent sur la page FM ; un encart y renvoie.
- Le tableau de bord ne change pas.

### Chaîne : sonde Livebox
- Nouveau type de sonde **Livebox** (Orange, Livebox 4 à 7) : lit l'interface locale de la box avec le compte administrateur (mot de passe masqué comme les autres secrets) : temps de fonctionnement, nombre de redémarrages, état de la connexion Internet et du lien (fibre, ADSL), IP publique, modèle et version. Un **redémarrage** met l'appareil en alerte pendant une durée réglable (15 min par défaut), une connexion Internet coupée aussi ; « Hors service » si la box ne répond plus. Interface non documentée par Orange : les valeurs absentes d'un modèle sont ignorées.

### RadioText : titres vides ignorés
- Un RadioText sans lettre ni chiffre (« - », envoyé par l'automation entre deux morceaux quand l'artiste et le titre sont vides) n'est plus affiché, n'entre plus dans l'historique des titres et ne compte plus comme RadioText reçu pour l'alerte « RadioText absent ».

### Journaux : en-têtes uniformes, 10 lignes par défaut
- **Même en-tête de trois cases sur chaque onglet** :
  - Réception FM : **Tuner FM** (En réception, Pas de signal ou Tuner absent ; fréquence, champ, PS, stéréo), **Alertes, 30 derniers jours**, **Tout est OK depuis** ;
  - Réception DAB+ : **Récepteur DAB+**, alertes sur 30 jours, tout est OK depuis ;
  - Chaîne de diffusion : **État de la chaîne** (tout est OK, N en alerte, N hors service ; nombre d'appareils, sondes et capteurs, réception syslog), alertes sur 30 jours, tout est OK depuis ;
  - BL-RMO : **En service depuis** (déplacé de l'onglet FM, avec la version), **Services** (application, pige, journaux, DAB+ : actifs ou arrêtés), **Erreurs et avertissements, 24 h** (un clic les affiche dans le journal) avec l'espace disque.
- Les cases « Alertes, 30 derniers jours » ne comptent que les alertes **qui ont envoyé au moins un mail**, avec le nombre de mails ; « Tout est OK depuis » part de la fin de la dernière de ces alertes, ou devient « En alerte depuis » en rouge. La durée cumulée et la case « 7 jours » disparaissent.
- Le voyant de réception syslog quitte le bandeau pour la case « État de la chaîne ».
- Réception FM : une alerte restée « en cours » sans retour enregistré (surveillance redémarrée pendant l'alerte, ou alerte plus récente du même type) s'affiche « Fin inconnue » et ne met plus l'en-tête « En alerte depuis ».
- **10 lignes par défaut**, puis « Afficher plus » (50 de plus) : alertes FM, titres RadioText, alertes DAB+, alertes de la chaîne, journal des appareils ; journal de BL-RMO à 10 lignes (100, 500 ou 2 000 au choix).

### Alerte espace disque
- BL-RMO contrôle toutes les 10 min l'espace libre du disque des piges : **sous 40 Go**, une alerte, un mail et une erreur dans le journal ; un second mail au retour au-dessus du seuil. Seuil réglable : `system.disk_min_free_gb` dans `config.json`. Visible dans la case « Erreurs et avertissements » de l'onglet BL-RMO.
- Nouvelle route `/api/system/summary` (version, démarrage, services, erreurs sur 24 h, disque).

### DAB+ : mails par type d'alerte
- **DAB+ › Réglages › Mails d'alerte** : une case par type (silence audio d'un service surveillé, service surveillé absent, récepteur hors service, ensemble dégradé). Une alerte décochée reste enregistrée dans les journaux et sur le tableau de bord, sans mail. Par exemple, pour ne surveiller par mail que la radio et pas la réception du multiplex, décocher « Ensemble dégradé ».

### Journaux de la chaîne : alertes relevées par BL-RMO
- **Nouveau tableau « Alertes de la chaîne »** en tête de Journaux › Chaîne de diffusion, comme pour la réception FM et le DAB+ : date, appareil, alerte ou hors service, début et fin, durée, état, détail (valeurs hors seuil), « Écouter » et « MP3 » de la pige FM, filtre par appareil et export CSV. Il reprend tout l'historique enregistré, y compris les alertes passées.
- Les alertes qui démarrent à moins de 30 s d'écart sont signalées (« En même temps que DB91-TX ») : une cause commune en amont est probable, par exemple une source muette en studio vue par le DB91-TX, le DB91-RX et le Sound4.
- Colonne **Mails** : nombre de mails envoyés pour l'alerte et son rétablissement (désormais enregistré à chaque envoi ; « · » pour les alertes antérieures, non enregistrées). Elle figure aussi dans l'export CSV.

#### Changements d'état copiés dans le journal
- Chaque changement d'état relevé par BL-RMO (alerte, hors service, rétabli) est aussi écrit dans les journaux de la chaîne, sous l'adresse de l'appareil, programme `bl-rmo`. Il apparaît dans **Journaux › Chaîne de diffusion**, dans l'onglet Journaux de l'appareil et dans les exports, à côté des messages syslog et des traps de l'appareil, par exemple « DB91-RX (appareil) : alerte, Valeur hors seuil : Sortie G (moy.) = -80.0 dB ». Niveaux : hors service = Erreur, alerte = Avertissement, rétabli = Info. Le premier relevé après un démarrage n'est pas copié.

### RadioText FM plus fiable
- **Blocs RDS vérifiés** : le décodeur lit enfin le niveau d'erreur de chaque bloc envoyé par le tuner et n'utilise que les blocs intacts, comme FM-DX-Webserver. Un caractère corrompu n'entre plus dans le PS ou le RadioText.
- **Changement de titre propre** : beaucoup de codeurs ne basculent pas le drapeau A/B quand le texte change. Un segment dont le contenu change efface désormais le texte en cours, et le nouveau RadioText n'est affiché qu'une fois reçu en entier : plus de mélange de l'ancien et du nouveau titre, plus de titre qui tarde à se mettre à jour.
- RadioText en groupes **2B** (32 caractères) pris en charge, en plus des groupes 2A.
- Toutes les 15 min, le journal `bl-rmo` donne la qualité de réception RDS (part des groupes avec erreur).

### Journaux : exports des alertes
- **Réception FM** : bouton « Exporter » (CSV) des alertes affichées, filtre compris, avec le message de l'alerte et celui du rétablissement (par exemple « RadioText absent depuis 600s »).
- **Titres diffusés (RadioText)** : bouton « Exporter » de l'historique complet du RadioText reçu (date, heure, PS, texte), pour comparer avec les alertes RadioText.
- **Réception DAB+** : bouton « Exporter » (CSV) des alertes affichées, filtres compris (service, SId, canal, durée, détail SNR et FIC).
- Fichiers CSV séparés par des points-virgules, en UTF-8 (lisibles directement dans Excel ou LibreOffice).
- Feuille de route : battement de cœur extérieur, alerte audio figé et RadioText inchangé.

### Configuration
- **Email de test** : le bouton est grisé, avec un message, tant que l'expéditeur, le mot de passe ou les destinataires modifiés ne sont pas enregistrés (le test part toujours avec la configuration enregistrée).
- **Vrais réglages du tuner TEF** : avec le TEF, la section Surveillance montre le **seuil d'absence de modulation** (niveau audio reçu, en dBFS, -40 par défaut), utilisé depuis toujours mais absent de la page. Les deux anciens seuils RTL-SDR (écart-type de modulation, perte émetteur en dBFS), sans effet avec le TEF, ne s'affichent plus qu'en mode RTL-SDR, comme le gain. La perte du signal FM reste automatique (le tuner ne transmet plus de mesure).
- **Délai d'alerte « RadioText absent »** réglable (5 min par défaut), à côté de celui du RDS.
- La section 1 devient « Réception FM & Station ».

### Chaîne de diffusion
- **Capteurs de site** : troisième catégorie, à côté des appareils de la chaîne et des sondes réseau, pour ce qui surveille l'environnement d'un site sans faire partie du signal (température et humidité d'un shelter, porte, secteur...). Un capteur se rattache à un site ; il s'affiche dans l'en-tête de ce site sur le synoptique, avec ses mesures (par exemple « Shelter 19,9 °C · 43 %RH », en orange si un seuil est dépassé), et après les appareils de son site sur le tableau de bord. Il garde l'historique, les graphiques, les journaux syslog, le journal et les alertes mail. Nouvelle section « Capteurs de site » dans Gérer la chaîne. Ses alertes ne créent pas d'extrait de pige.
- **Modèle SNMP egnite Querx TH** (sonde de température et d'humidité, par exemple au shelter) : température, humidité, point de rosée, état du capteur et uptime, avec des seuils de départ (0 à 40 °C, 85 %RH) à ajuster.

### Piges multiples et nouvelle page Pige
- **Plusieurs piges** : la pige FM, plus une pige par service DAB+ voulu. Elles se créent, se modifient, se suspendent et se suppriment dans la nouvelle partie **Configuration des piges** de la page Pige (administrateur) : nom, service, durée de conservation (1 à 90 jours), estimation de l'espace disque.
- Le service `bl-rmo-pige` enregistre toutes les piges actives, une par fil, et relit la configuration toutes les 20 s : une pige créée démarre sans redémarrage. La pige FM ne change pas (même dossier, mêmes fichiers).
- **Une pige DAB+** lit le flux du service servi par welle-cli et le réencode en **MP3 128 kb/s constant** : welle-cli produit un MP3 à débit variable, or la durée des segments se déduit du débit. Environ 1,4 Go par jour. Elle demande le décodage « Tous les services en continu » (la configuration le signale). Ses fichiers sont dans `pige/extra/<pige>/` et partent avec la pige quand on la supprime.
- **Nouvelle page Pige**, en trois étapes : choisir l'instant (date, heure à la seconde, raccourcis dont la dernière alerte), écouter et se déplacer (−10 min, −1 min, −10 s, +10 s, +1 min, +10 min), extraire (début et fin marqués pendant l'écoute, ou une durée ; écouter l'extrait, puis télécharger le MP3).
- La grande frise disparaît : un **repère de 30 min** autour de la position montre les trous et l'extrait ; une **grille jour par jour** montre la disponibilité sur toute la durée de conservation (un clic : ce jour, à la même heure).
- **Journaux** : un lien « Écouter » de la chaîne ou de la réception FM ouvre toujours la pige FM ; les alertes DAB+ ont désormais, comme la FM, une colonne **Pige** en bout de ligne (« Écouter » et « MP3 », 30 s avant à 30 s après) : pige du service concerné, ou pour une alerte sur tout le multiplex (ensemble dégradé, récepteur hors service), la première pige DAB+ configurée ; « pas de pige » ou « hors pige » sinon.
- API : `?p=<pige>` sur `/api/pige/status`, `/api/pige/segment`, `/api/pige/export` (la FM par défaut) ; nouvelles routes `/api/pige/list` et `/api/pige/config`.
- Le service `bl-rmo-pige` s'appelle « BL-RMO - Pige audio (FM et DAB+) » dans le script d'installation (il disait encore « 24 h »).

### Nouveau tableau de bord
- **Une seule page pour tout surveiller** : un bandeau d'état (FM, DAB+, chaîne, pige, alertes des dernières 24 h), puis la réception FM et la réception DAB+ côte à côte, la chaîne de diffusion et la pige.
- **Réception FM** : logo, PS, PI, RadioText ; lecteur avec VU-mètre (niveaux du tuner, 20 mesures par seconde, crête maintenue 1,5 s) ; signal, modulation, **stéréo ou mono**, RDS, uptime ; dernière alerte, avec lien vers le journal.
- **Réception DAB+** : un service, choisi dans **DAB+ › Réglages › Service du tableau de bord** (par défaut le premier service surveillé) ; diaporama, titre en cours, lecteur avec VU-mètre ; présence, audio, stéréo ou mono, SNR et FIC de l'ensemble, disponibilité sur 24 h ; dernière alerte.
- **Un seul lecteur à la fois** : lancer le DAB+ coupe la FM, et l'inverse.
- **Chaîne de diffusion** : tous les appareils dans l'ordre, chaque sonde placée sur la liaison qu'elle mesure (comme la page Chaîne), sur plusieurs lignes si besoin, sans barre de défilement ; nombre d'appareils OK, en défaut, dernier événement.
- **Pige** : état, disponible depuis, espace utilisé et libre, remplissage de la durée de conservation.
- Disparaissent : le spectre audio, le compteur et l'adresse des alertes mail, le bouton d'enregistrement, la courbe du signal RF et le panneau MPX du mode RTL-SDR.
- **Mode public** (`/public`) : même tableau de bord ; l'écoute du DAB+ y demande une connexion.
- Nouvelle route `/api/dashboard`, publique comme `/public` : aucune adresse, aucun réglage, aucun identifiant.

### Stéréo FM
- **L'indicateur stéréo vient désormais du tuner** (trames `Ss` / `Sm` : pilote 19 kHz détecté ou non). Il était tiré du bit MS du RDS, qui distingue en réalité musique et parole.

### Page DAB+
- **Lien « Externe »** dans le lecteur : ouvre le flux MP3 du service dans un autre onglet ou un lecteur externe.

### Page DAB+ : changement de radio
- **Plus de « Écoute impossible » en changeant de radio** : une seule écoute DAB+ reste possible à la fois, mais la nouvelle remplace désormais la précédente au lieu d'être refusée. Le serveur mettait quelques secondes à voir que le navigateur avait coupé l'ancien flux, et refusait la nouvelle écoute entre-temps.
- Le serveur attend jusqu'à 3 s qu'un service tout juste épinglé démarre, et le lecteur réessaie une fois avant d'afficher un message d'erreur.

### Défauts audio : retirés de l'interface
- Sur 24 h de réception réelle, la détection n'a produit que des fausses alertes (aucun défaut audible dans les extraits). Le bloc « Défauts audio » disparaît de Journaux, Réception FM, et la détection est désactivée par défaut (`audio_defects.enabled`). Le module reste dans le dépôt, sans effet tant qu'il n'est pas réactivé.

### Défauts audio de la réception FM (observation)
- **Nouveau module `audio_defects.py`** : repère sur le signal brut du tuner TEF (48 kHz, avant tout encodage MP3) les défauts « numériques » de la chaîne de diffusion :
  - **micro-coupures** de 4 ms à 2 s : chute brutale du niveau (plus de 30 dB en 2 ms), ce qui les distingue d'une pause naturelle ;
  - **clics** : cassure isolée de la forme d'onde, repérée par prédiction linéaire ; un saut d'audio (morceau sauté ou répété par un codec) se présente ainsi ;
  - **craquements** : plus de 3 clics en moins d'une seconde, surlignés dans le journal avec le nombre de clics et le maximum relevé sur une seconde.
- Les angles de l'écrêteur du processeur de diffusion, qui ressemblent à des clics, sont écartés quand le programme touche sa crête.
- Chaque défaut est enregistré avec le niveau de réception (dBf, multitrajet) : s'il était faible ou perturbé, le défaut est marqué « réception douteuse ».
- **Extrait WAV** d'environ 3 s autour de chaque défaut (30 par heure au plus), gardé 7 jours ; événements gardés 30 jours (table `audio_defects` de `fm_monitor.db`, dossier `audio_defects/`).
- **Journaux, onglet Réception FM** : nouveau bloc « Défauts audio » avec décompte sur 24 h et 7 jours, période, filtre réception douteuse, écoute et téléchargement des extraits.
- **Export pour analyse** (opérateur et administrateur) : ZIP avec `defauts_audio.csv`, `reglages.json` (réglages du détecteur, fréquence, compteurs) et, au choix, les extraits WAV (300 au plus, les plus récents), sur 24 h, 7 ou 30 jours.
- Relevé seul pour l'instant, sans mail, le temps de régler les seuils sur la réception réelle. Réglages dans la section `audio_defects` de `config.json`.
- Calcul dans le fil de capture existant, sans nouvelle capture ALSA : environ 3 % d'un cœur.
- **Sauts du tuner, comptés à part** : le premier relevé a montré environ 5 sauts par minute dans le flux audio USB du Headless TEF Lite SE lui-même, présents aussi dans une capture directe `arecord` sans BL-RMO. Ils portent de l'énergie au-dessus de 20 kHz, hors de la bande FM (qui s'arrête vers 15 kHz), ce qu'un défaut parti à l'antenne ne peut pas faire. Ils sont repérés sur ce critère, classés « Saut du tuner », comptés dans leur propre case, masqués de la liste par défaut et sans extrait WAV ; les clics qui tombent dessus ne sont plus comptés comme clics.
- Un trou fait de zéros exacts (« silence numérique ») est classé « Saut du tuner » : il vient de la capture, une réception FM laisse toujours du souffle.
- Rien n'est signalé pendant les 5 premières secondes de capture (démarrage de ffmpeg).

### Pige sur 30 jours
- **Rétention par défaut : 30 jours** (`"pige": {"retention_hours": 720}`), environ 1,4 Go par jour à 128 kbit/s, soit 41 Go ; sous 500 Mo libres, les plus anciens segments sont supprimés.
- **Frise par journée** : « Jour précédent », « Jour suivant », choix d'une date et retour aux « Dernières 24 h ». Un lien « Écouter » venu des Journaux ouvre la journée de l'événement.
- `/api/pige/status` ne renvoie plus que les segments d'une fenêtre (`from`, `to`, 48 h par défaut) et donne `oldest` : plus de 8 000 segments sur 30 jours ne transitent plus à chaque rafraîchissement.

### Page DAB+
- **welle-cli relancé quand aucun ensemble n'est décodé** : après un redémarrage, welle-cli pouvait tourner avec du SNR mais sans rien décoder (FIC 0 %, EId 0x0000, aucun service) et ne s'en sortait pas seul. welle-cli finit en général par se synchroniser seul, mais après 2 à 3 minutes (« SyncOnPhase failed » en boucle dans `logs/welle-cli.log`) ; s'il n'a toujours rien décodé après 5 min (`sync_timeout`), il est relancé avec réinitialisation USB de la clé, puis à intervalles croissants (jusqu'à 30 min).
- **Réglage du gain de la clé** dans les réglages de la page DAB+ : « Automatique (welle-cli) » ou un gain fixe (0 à 49,6 dB, table des tuners R820T/R828D). Sur le site, l'automatique retardait la synchronisation de 2 à plus de 4 minutes, en changeant de gain toutes les 50 ms pendant que welle-cli cherchait à se caler ; à gain fixe (28 dB), l'ensemble est décodé en 2 s.
- **Plus de fausse alerte « Ensemble dégradé » à chaque redémarrage** : l'alerte attend la première synchronisation de welle-cli, ou 5 min sans synchro, et compte alors le défaut depuis le lancement. Le journal du service indique le temps de synchronisation (« ensemble décodé N s après le lancement »). Le script `/usr/local/bin/rtlsdr-usb-reset.sh` installé par `67-dab.sh` est utilisé par défaut.
- **Lecteur au style du tableau de bord** : bouton lecture rond, volume avec pourcentage ; un direct se relance depuis le début du flux.
- Le tableau des services garde une largeur de colonnes fixe : il ne bouge plus quand le DLS ou les niveaux changent ; le DLS tient sur une ligne.
- Les petits VU-mètres du tableau sont remplacés par le niveau en dBFS ; un seul VU-mètre, dans le lecteur, mesuré dans le navigateur sur l'audio écouté (crête en temps réel, maintien 1,5 s, vert jusqu'à 0 dBFS).
- Le décodage « Carrousel, 1 service à la fois » est retiré : avec lui, welle-cli indiquait tous les services absents. Un réglage existant à 1 passe à 2.

### Réception DAB+ (reprise de BL-DMO)
- **Nouveau service `fm-monitor-dab`** (`dab_monitor.py`) : pilote welle-cli (fork welle-monitor) sur une clé RTL-SDR dédiée, relève `/mux.json` toutes les 2 s, relance welle-cli avec un délai croissant (5 s à 5 min) et réinitialise la clé USB si besoin. En veille tant que le DAB+ n'est pas activé.
- **Page DAB+** : récepteur, ensemble, SNR et qualité FIC, tableau des services (présence, niveaux, DLS, débit, disponibilité 24 h), écoute d'un service avec diaporama, derniers événements, réglages (administrateur).
- **Mise en service guidée** : tant que le DAB+ est désactivé, la page DAB+ indique ce qui est déjà prêt (welle-cli installé, clé RTL-SDR branchée) et ce qui reste à faire.
- **Alertes** : récepteur hors service, ensemble dégradé, service absent, silence ; mail à l'alerte et au rétablissement ; services surveillés au choix (aucun coché = tous).
- En mode carrousel, un niveau audio n'est pris en compte que s'il vient d'être rafraîchi : plus de fausse alerte de silence sur un service qui n'est pas en cours de décodage.
- **Journaux** : nouvel onglet **Réception DAB+** (alertes, filtres par type et par service, 7 et 30 jours), séparé de la Réception FM ; le service `fm-monitor-dab` apparaît dans l'onglet BL-RMO. La pige n'enregistrant que la FM, les alertes DAB+ n'ont pas de lien vers la pige.
- Données dans `fm_monitor.db` (tables `dab_events`, `dab_uptime`, `dab_services`), événements 90 jours, disponibilité 31 jours.
- `install/build/67-dab.sh` : rtl-sdr, pilote TNT écarté, règle udev, script de réinitialisation USB, service ; `BUILD_WELLE=1` compile welle-cli (xxd requis pour intégrer l'interface web de welle-cli). Appelé par `install.sh` ; `update.sh` redémarre aussi `fm-monitor-dab`.

### Journaux : titres diffusés via le RadioText
- Onglet **Réception FM** : nouvelle fenêtre **Titres diffusés (RadioText)** avec les 10 derniers RadioText reçus et l'heure de leur premier affichage ; le texte en cours est marqué « À l'antenne ». Rafraîchie toutes les 15 s.
- Un RadioText n'est enregistré qu'après 8 s de réception identique (textes partiels ou corrompus écartés) ; un texte en rotation (nom de la station, slogan) n'est repris qu'après 10 minutes.
- **Correction** : en mode TEF, la réception du RadioText n'était jamais horodatée, donc l'alerte « RadioText absent » ne pouvait pas se déclencher. Elle fonctionne désormais (délai réglable, 5 min par défaut) et une alerte RadioText ouverte est clôturée au redémarrage du service, comme les autres.
- Données dans la table `rds_history` de `fm_monitor.db` (1 000 dernières lignes conservées), route `/api/rds/rt_history` accessible au rôle Lecteur.

### Dépôt
- Le dépôt GitHub devient [LyonelB/BL-RMO](https://github.com/LyonelB/BL-RMO) : liens mis à jour dans l'interface, le README et les scripts.

### Noms techniques : dossier, services, adresse
- Dossier `~/bl-rmo`, services `bl-rmo`, `bl-rmo-pige`, `bl-rmo-syslog`, `bl-rmo-dab`, site Nginx et certificats `bl-rmo`, adresse `bl-rmo.local` : `install.sh`, `update.sh` et les scripts `install/build` utilisent les nouveaux noms.
- **`install/migrate-to-bl-rmo.sh`** fait passer une installation existante aux nouveaux noms : inventaire sans rien modifier, `--apply`, `--rollback`. Sauvegarde de tout ce qu'il modifie, vérifications à la fin. Les anciens noms restent utilisables : `~/fm-monitor` devient un lien, `fm-monitor*` restent des alias systemd, l'ancien nom `.local` est toujours annoncé (service `bl-rmo-mdns-alias`), l'annonce NetBIOS passe de BL-FMO à BL-RMO.
- `update.sh` et l'onglet BL-RMO des Journaux fonctionnent avant comme après la migration ; le journal système affiche aussi l'historique enregistré sous l'ancien nom.

---

## [0.9.0] - 2026-10-17

### Nouveau nom : BL-RMO
- **FM-Monitor devient BL-RMO**, pour « Radio Monitoring » : le logiciel surveille la réception FM et DAB+ et toute la chaîne de diffusion.
- Le nom change dans toute l'interface (menu, titres, page À propos, connexion, assistant), dans les mails d'alerte, les exports (`blrmo-journaux_…csv`, `blrmo-chaine_…zip`), le README et les scripts d'installation.
- Rien ne change pour une installation existante : dossier `fm-monitor`, services `fm-monitor-*`, adresse `bl-fmo.local` et dépôt GitHub seront renommés lors d'étapes ultérieures, avec un script de migration.

### Journaux : choix des appareils
- Les appareils se choisissent par cases à cocher, plusieurs à la fois ; aucun coché = tous. La sélection est retenue. Le bloc de cartes et la liste déroulante, qui faisaient doublon, disparaissent.

### Journaux
- **Une seule page Journaux, trois onglets**, sans mélange :
  - **Réception FM** : alertes du tuner (signal, modulation, RDS, RadioText) avec durée, état, écoute et MP3 ; nombre d'alertes et durée cumulée sur 7 et 30 jours, calculés sur l'historique (plus de compteur remis à zéro au redémarrage) ;
  - **Chaîne de diffusion** : journaux syslog et traps des appareils (contenu de l'ancienne page Journaux) ;
  - **BL-RMO** : journal système de l'application, de la pige ou de la réception des journaux, 100 à 2 000 lignes, filtre avertissements et erreurs.
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

[0.7.0]: https://github.com/LyonelB/BL-RMO/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/LyonelB/BL-RMO/compare/v0.5.2...v0.6.0

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

[0.5.0]: https://github.com/LyonelB/BL-RMO/compare/v0.4.3...v0.5.0
[0.4.3]: https://github.com/LyonelB/BL-RMO/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/LyonelB/BL-RMO/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/LyonelB/BL-RMO/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/LyonelB/BL-RMO/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/LyonelB/BL-RMO/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/LyonelB/BL-RMO/compare/v0.2.0...v0.3.1
[0.2.0]: https://github.com/LyonelB/BL-RMO/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/LyonelB/BL-RMO/releases/tag/v0.1.0
