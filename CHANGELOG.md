# Changelog

Toutes les modifications notables de ce projet seront documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).
## [Non publié]

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
