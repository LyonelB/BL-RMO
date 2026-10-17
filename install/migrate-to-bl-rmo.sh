#!/bin/bash
#
# BL-RMO - Migration d'une installation FM-Monitor vers les noms BL-RMO
#
#   bash install/migrate-to-bl-rmo.sh             inventaire, ne modifie rien
#   bash install/migrate-to-bl-rmo.sh --apply     migration
#   bash install/migrate-to-bl-rmo.sh --rollback  retour à l'état d'avant la migration
#
# Ce qui change :
#   ~/fm-monitor            -> ~/bl-rmo   (~/fm-monitor reste, en lien vers ~/bl-rmo)
#   services fm-monitor*    -> bl-rmo*    (les anciens noms restent des alias systemd)
#   Nginx, certificats      -> bl-rmo     (bl-rmo.local ajouté, l'ancien nom reste servi)
#   nom de la machine       -> bl-rmo     (l'ancien nom mDNS reste annoncé, ex. bl-fmo.local)
#   annonce NetBIOS         -> BL-RMO-…   (si elle contenait BL-FMO)
#   sudoers, crontab, autres fichiers de /etc qui citent ~/fm-monitor
#
# Ne change pas : l'adresse IP, les données (pige, extraits, bases, config.json),
# le point de montage Icecast. Tout ce qui est modifié est d'abord sauvegardé dans
# ~/bl-rmo-migration-<date>/, ce qui permet le retour arrière.
#
# À lancer en tant qu'utilisateur de BL-RMO (graffiti), pas avec sudo.
#
set -uo pipefail

ROOT="${ROOT:-}"                       # préfixe des fichiers système (tests uniquement)
OLD=fm-monitor
NEW=bl-rmo
OLD_DIR="$HOME/$OLD"
NEW_DIR="$HOME/$NEW"
STATE="$HOME/.bl-rmo-migration"
NEW_HOST="$NEW"
SYSD="$ROOT/etc/systemd/system"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step() { echo -e "\n${BLUE}==>${NC} $1"; }
ok()   { echo -e "  ${GREEN}[ok]${NC} $1"; }
info() { echo -e "  ${BLUE}[i]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[!]${NC} $1"; }
die()  { echo -e "  ${RED}[x]${NC} $1"; exit 1; }

MODE="${1:-check}"
case "$MODE" in check|--check) MODE=check ;; --apply) MODE=apply ;; --rollback) MODE=rollback ;;
    *) echo "Usage : $0 [--apply | --rollback]"; exit 1 ;; esac

[ "$(id -u)" = 0 ] && [ -z "$ROOT" ] && die "Lancer sans sudo, en tant que l'utilisateur de BL-RMO (le script demande sudo quand il en a besoin)."

# ── Sauvegarde et journal des fichiers créés ─────────────────────────────
BK=""
save() {                 # copie un fichier ou dossier avant de le modifier ou de le supprimer
    local p="$1"
    [ -e "$p" ] || [ -L "$p" ] || return 0
    grep -qxF "$p" "$BK/modified.list" 2>/dev/null && return 0
    sudo mkdir -p "$BK/files$(dirname "$p")"
    sudo cp -a "$p" "$BK/files$p"
    echo "$p" >> "$BK/modified.list"
}
created() { echo "$1" >> "$BK/created.list"; }
sudo_write() {           # sudo_write <fichier> <mode> < contenu
    local tmp; tmp=$(mktemp)
    cat > "$tmp"
    sudo install -m "$2" "$tmp" "$1"
    rm -f "$tmp"
}

# ── Inventaire ───────────────────────────────────────────────────────────
old_units() {            # unités fm-monitor*.service réelles (pas les alias), l'application d'abord
    for f in "$SYSD/$OLD.service" "$SYSD"/$OLD-*.service; do
        [ -f "$f" ] && [ ! -L "$f" ] && basename "$f" .service
    done
}
avahi_name() { sed -n 's/^host-name=//p' "$ROOT/etc/avahi/avahi-daemon.conf" 2>/dev/null | head -1; }
netbios_line() { grep -iE '^\s*netbios name\s*=' "$ROOT/etc/samba/smb.conf" 2>/dev/null | head -1; }
nginx_sites() {         # sites actifs de BL-RMO : nom fm-monitor ou ancien nom mDNS, ou renvoi vers le port 5000
    local e t b
    for e in "$ROOT"/etc/nginx/sites-enabled/*; do
        [ -e "$e" ] || continue
        t=$(readlink -f "$e"); b=$(basename "$t")
        if [[ "$b" == *"$OLD"* ]] || { [ -n "$ALIAS" ] && [[ "$b" == *"$ALIAS"* ]]; } \
           || sudo grep -qE -e "$OLD|127\.0\.0\.1:5000|localhost:5000${ALIAS:+|$ALIAS\.local}" "$t" 2>/dev/null; then
            echo "$t"
        fi
    done
}
new_name() { local n="${1/$OLD/$NEW}"; [ -n "$ALIAS" ] && n="${n/$ALIAS/$NEW}"; echo "$n"; }
sudoers_files() {        # /etc/sudoers.d n'est lisible que par root
    sudo find "$ROOT/etc/sudoers.d" -maxdepth 1 -type f \( -name "*$OLD*" -o -exec grep -qI -e "$OLD" {} \; \) -print 2>/dev/null | sort -u
}
other_etc() {            # autres fichiers de /etc qui citent ~/fm-monitor
    sudo grep -rlI -e "$OLD_DIR" "$ROOT/etc" 2>/dev/null \
        | grep -vE "^$ROOT/etc/(systemd/system/$OLD|sudoers.d/|nginx/sites-)" || true
}
ip_addr() { hostname -I 2>/dev/null | awk '{print $1}'; }

inventory() {
    step "Inventaire"
    if [ -L "$OLD_DIR" ] || [ -f "$SYSD/$NEW.service" ]; then
        warn "La migration semble déjà faite ($NEW_DIR, service $NEW). Retour arrière : --rollback"
    fi
    [ -d "$OLD_DIR" ] && [ ! -L "$OLD_DIR" ] && ok "Dossier : $OLD_DIR -> $NEW_DIR ($(du -sh "$OLD_DIR" 2>/dev/null | cut -f1))"
    [ -e "$NEW_DIR" ] && warn "$NEW_DIR existe déjà"
    local u
    for u in $(old_units); do
        info "Service $u -> ${u/$OLD/$NEW} ($(systemctl is-enabled "$u" 2>/dev/null || echo ?), $(systemctl is-active "$u" 2>/dev/null || echo ?))"
        [ -d "$SYSD/$u.service.d" ] && info "  avec surcharge $SYSD/$u.service.d"
    done
    for f in $(sudoers_files); do info "sudoers : $f"; done
    for f in $(nginx_sites); do info "Nginx : $f -> $(dirname "$f")/$(new_name "$(basename "$f")")"; done
    [ -z "$(nginx_sites)" ] && [ -d "$ROOT/etc/nginx" ] && warn "Aucun site Nginx de BL-RMO trouvé : Nginx ne sera pas modifié"
    info "Nom de la machine : $(hostname) -> $NEW_HOST ; mDNS : ${ANAME:-aucun host-name}.local -> $NEW_HOST.local (alias ${ALIAS:-aucun})"
    [ -n "$(netbios_line)" ] && info "Samba : $(netbios_line | xargs)"
    crontab -l 2>/dev/null | grep -q "$OLD_DIR" && info "crontab de $USER cite $OLD_DIR"
    sudo crontab -l 2>/dev/null | grep -q "$OLD_DIR" && info "crontab de root cite $OLD_DIR"
    for f in $(other_etc); do info "Autre fichier : $f"; done
    if [ -d "$OLD_DIR/.git" ] && [ -n "$(git -C "$OLD_DIR" status --porcelain 2>/dev/null)" ]; then
        warn "Modifications non commitées dans $OLD_DIR : mieux vaut commiter avant (elles suivent le dossier de toute façon)."
    fi
    command -v avahi-publish >/dev/null || info "avahi-utils sera installé (annonce de l'ancien nom .local)"
    local car; car=$(mkcert -CAROOT 2>/dev/null || true)
    if [ -n "$car" ] && [ -f "$car/rootCA-key.pem" ]; then ok "mkcert : certificat refait pour $NEW_HOST.local et ${ALIAS:-$NEW_HOST}.local"
    else info "mkcert sans autorité locale : les certificats actuels sont repris sous le nouveau nom"; fi
}

ANAME=$(avahi_name)
ALIAS=""
for n in "$ANAME" "$(hostname)"; do
    if [ -n "$n" ] && [ "$n" != "$NEW_HOST" ] && [ "$n" != "$OLD" ]; then ALIAS="$n"; break; fi
done
[ -z "$ALIAS" ] && [ "$(hostname)" = "$OLD" ] && ALIAS="$OLD"

# ═════════════════════════════════════════════════════════════════════════
if [ "$MODE" = check ]; then
    inventory
    echo -e "\nRien n'a été modifié. Pour migrer : bash $0 --apply"
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════════
if [ "$MODE" = apply ]; then
    [ -d "$OLD_DIR" ] && [ ! -L "$OLD_DIR" ] || die "$OLD_DIR introuvable ou déjà migré."
    [ -e "$NEW_DIR" ] && die "$NEW_DIR existe déjà."
    [ -n "$(old_units)" ] || die "Aucun service $OLD*.service dans $SYSD."
    sudo -v || die "sudo refusé."
    inventory
    echo
    read -r -p "Lancer la migration ? La surveillance et la pige seront coupées quelques minutes. [o/N] " r
    [[ "$r" =~ ^[oOyY] ]] || { echo "Abandon."; exit 0; }

    BK="$HOME/bl-rmo-migration-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BK"; : > "$BK/modified.list"; : > "$BK/created.list"
    cp "$0" "$BK/migrate-to-bl-rmo.sh"
    echo "$BK" > "$STATE"
    hostname > "$BK/hostname.old"
    trap 'echo -e "\n${RED}Échec.${NC} Retour arrière : bash $BK/migrate-to-bl-rmo.sh --rollback"' ERR
    set -e

    step "Arrêt des services"
    : > "$BK/units.list"
    for u in $(old_units); do
        en=$(systemctl is-enabled "$u" 2>/dev/null || true); ac=$(systemctl is-active "$u" 2>/dev/null || true)
        echo "$u ${u/$OLD/$NEW} $en $ac" >> "$BK/units.list"
    done
    # l'application en dernier : la pige et les journaux reprennent dès qu'elle revient
    sudo systemctl stop $(awk '{print $1}' "$BK/units.list" | tac) || true
    ok "Arrêtés : $(awk '{print $1}' "$BK/units.list" | xargs)"

    step "Dossier"
    mv "$OLD_DIR" "$NEW_DIR"
    ln -s "$NEW_DIR" "$OLD_DIR"
    : > "$BK/inner.list"
    for f in "$NEW_DIR"/config.json "$NEW_DIR"/.env "$NEW_DIR"/venv/pyvenv.cfg "$NEW_DIR"/venv/bin/*; do
        [ -f "$f" ] && [ ! -L "$f" ] && grep -qI -e "$OLD_DIR" "$f" 2>/dev/null || continue
        cp -a "$f" "$BK/$(echo "${f#$NEW_DIR/}" | tr / _).inner"
        sed -i "s#$OLD_DIR#$NEW_DIR#g" "$f"
        echo "$f" >> "$BK/inner.list"
    done
    ok "$OLD_DIR -> $NEW_DIR, lien $OLD_DIR conservé ; $(wc -l < "$BK/inner.list") fichier(s) du venv et de la config mis à jour"

    step "Services systemd"
    while read -r u n en ac; do
        src="$SYSD/$u.service"
        sed -e "s#$OLD_DIR#$NEW_DIR#g" -e "s/$OLD/$NEW/g" -e "s/^Description=FM Radio Monitoring System/Description=BL-RMO - Application web/" "$src" \
            | awk -v a="Alias=$u.service" '{print} /^\[Install\]/{print a}' | sudo_write "$SYSD/$n.service" 644
        created "$SYSD/$n.service"
        if [ -d "$SYSD/$u.service.d" ]; then
            sudo mkdir -p "$SYSD/$n.service.d"; created "$SYSD/$n.service.d"
            for d in "$SYSD/$u.service.d"/*; do
                [ -f "$d" ] && sed -e "s#$OLD_DIR#$NEW_DIR#g" -e "s/$OLD/$NEW/g" "$d" | sudo_write "$SYSD/$n.service.d/$(basename "$d")" 644
            done
            save "$SYSD/$u.service.d"; sudo rm -rf "$SYSD/$u.service.d"
        fi
        sudo systemctl disable "$u" >/dev/null 2>&1 || true
        save "$src"; sudo rm -f "$src"
        ok "$u -> $n (alias $u)"
    done < "$BK/units.list"
    sudo systemctl daemon-reload

    step "sudoers"
    for f in $(sudoers_files); do
        b=$(basename "$f"); nf="$(dirname "$f")/${b/$OLD/$NEW}"
        tmp=$(mktemp)
        sudo cat "$f" > "$tmp"
        sudo cat "$f" | grep -e "$OLD" | sed -e "s#$OLD_DIR#$NEW_DIR#g" -e "s/$OLD/$NEW/g" >> "$tmp" || true
        if sudo visudo -cf "$tmp" >/dev/null; then
            save "$f"
            [ "$nf" != "$f" ] && { sudo rm -f "$f"; created "$nf"; }
            sudo install -m 440 "$tmp" "$nf"
            ok "$nf (anciennes et nouvelles règles)"
        else
            warn "$f : règles générées invalides, fichier laissé tel quel"
        fi
        rm -f "$tmp"
    done

    step "Autres fichiers et crontab"
    for f in $(other_etc); do
        save "$f"; sudo sed -i "s#$OLD_DIR#$NEW_DIR#g" "$f"; ok "$f"
    done
    if crontab -l 2>/dev/null | grep -q "$OLD_DIR"; then
        crontab -l > "$BK/crontab.user"; sed "s#$OLD_DIR#$NEW_DIR#g" "$BK/crontab.user" | crontab -; ok "crontab de $USER"
    fi
    if sudo crontab -l 2>/dev/null | grep -q "$OLD_DIR"; then
        sudo crontab -l > "$BK/crontab.root"; sed "s#$OLD_DIR#$NEW_DIR#g" "$BK/crontab.root" | sudo crontab -; ok "crontab de root"
    fi

    step "Nginx et certificats"
    SSL="$ROOT/etc/nginx/ssl"
    for base in $OLD $ALIAS; do
        for kind in crt key; do
            if [ -f "$SSL/$base.$kind" ] && [ ! -e "$SSL/$NEW.$kind" ]; then
                sudo cp -a "$SSL/$base.$kind" "$SSL/$NEW.$kind"; created "$SSL/$NEW.$kind"
            fi
        done
    done
    CAROOT=$(mkcert -CAROOT 2>/dev/null || true)
    if [ -n "$CAROOT" ] && [ -f "$CAROOT/rootCA-key.pem" ] && [ -d "$SSL" ]; then
        names="$NEW_HOST.local"; [ -n "$ALIAS" ] && names="$names $ALIAS.local"
        tmpd=$(mktemp -d)
        if mkcert -cert-file "$tmpd/c.crt" -key-file "$tmpd/c.key" $names $(ip_addr) localhost 127.0.0.1 >/dev/null 2>&1; then
            [ -e "$SSL/$NEW.crt" ] && { grep -qxF "$SSL/$NEW.crt" "$BK/created.list" || save "$SSL/$NEW.crt"; }
            [ -e "$SSL/$NEW.key" ] && { grep -qxF "$SSL/$NEW.key" "$BK/created.list" || save "$SSL/$NEW.key"; }
            sudo install -m 644 "$tmpd/c.crt" "$SSL/$NEW.crt"; sudo install -m 600 "$tmpd/c.key" "$SSL/$NEW.key"
            grep -qxF "$SSL/$NEW.crt" "$BK/created.list" || created "$SSL/$NEW.crt"
            grep -qxF "$SSL/$NEW.key" "$BK/created.list" || created "$SSL/$NEW.key"
            ok "Certificat mkcert : $names $(ip_addr)"
        else
            warn "mkcert a échoué : certificat actuel repris"
        fi
        rm -rf "$tmpd"
    fi
    for f in $(nginx_sites); do
        b=$(basename "$f"); nf="$(dirname "$f")/$(new_name "$b")"
        content=$(sudo cat "$f" | sed -e "s#$OLD_DIR#$NEW_DIR#g" -e "s#/ssl/$OLD\.#/ssl/$NEW.#g")
        [ -n "$ALIAS" ] && content=$(sed -e "s#/ssl/$ALIAS\.#/ssl/$NEW.#g" <<<"$content")
        if ! grep -q "$NEW_HOST.local" <<<"$content"; then
            content=$(sed -E "s/server_name[[:space:]]+([^;]*);/server_name $NEW_HOST.local \1;/" <<<"$content")
        fi
        save "$f"
        en_old="$ROOT/etc/nginx/sites-enabled/$b"
        if [ "$nf" != "$f" ]; then
            sudo rm -f "$f"; created "$nf"
            if [ -L "$en_old" ] || [ -e "$en_old" ]; then
                save "$en_old"; sudo rm -f "$en_old"
                sudo ln -sf "$nf" "$ROOT/etc/nginx/sites-enabled/$(basename "$nf")"; created "$ROOT/etc/nginx/sites-enabled/$(basename "$nf")"
            fi
        fi
        printf '%s\n' "$content" | sudo_write "$nf" 644
        ok "$nf (server_name : $(grep -m1 -oE 'server_name[^;]*' <<<"$content" | cut -d' ' -f2- | xargs))"
    done
    if command -v nginx >/dev/null; then
        sudo nginx -t >/dev/null 2>&1 || { sudo nginx -t; false; }
        sudo systemctl reload nginx && ok "Nginx rechargé"
    fi

    step "Nom de la machine et annonces réseau"
    save "$ROOT/etc/hostname"; save "$ROOT/etc/hosts"
    OLD_HOST=$(hostname)
    # les deux noms le temps du changement, pour que sudo résolve toujours le nom de la machine
    if grep -q '^127\.0\.1\.1' "$ROOT/etc/hosts"; then
        sudo sed -i -E "s/^127\.0\.1\.1([[:space:]].*)?$/127.0.1.1\t$NEW_HOST $OLD_HOST/" "$ROOT/etc/hosts"
    else
        echo -e "127.0.1.1\t$NEW_HOST $OLD_HOST" | sudo tee -a "$ROOT/etc/hosts" >/dev/null
    fi
    sudo hostnamectl set-hostname "$NEW_HOST"
    sudo sed -i -E "s/^127\.0\.1\.1([[:space:]].*)?$/127.0.1.1\t$NEW_HOST/" "$ROOT/etc/hosts"
    ok "Nom de la machine : $OLD_HOST -> $NEW_HOST"
    AV="$ROOT/etc/avahi/avahi-daemon.conf"
    if [ -f "$AV" ]; then
        save "$AV"
        if grep -qE '^#?host-name=' "$AV"; then sudo sed -i -E "s/^#?host-name=.*/host-name=$NEW_HOST/" "$AV"
        else sudo sed -i "s/^\[server\]/[server]\nhost-name=$NEW_HOST/" "$AV"; fi
        sudo systemctl restart avahi-daemon && ok "mDNS : $NEW_HOST.local"
    fi
    if [ -n "$ALIAS" ]; then
        command -v avahi-publish >/dev/null || sudo apt-get install -y -qq avahi-utils >/dev/null 2>&1 || true
        if command -v avahi-publish >/dev/null || [ -n "$ROOT" ]; then
            sudo_write "$SYSD/$NEW-mdns-alias.service" 644 <<EOF
[Unit]
Description=BL-RMO - Ancien nom $ALIAS.local
After=avahi-daemon.service network-online.target
Requires=avahi-daemon.service

[Service]
ExecStart=/bin/sh -c 'exec /usr/bin/avahi-publish -a -R $ALIAS.local \$\$(hostname -I | cut -d" " -f1)'
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
            created "$SYSD/$NEW-mdns-alias.service"
            echo "$NEW-mdns-alias" >> "$BK/extra_units.list"
            ok "Ancien nom $ALIAS.local toujours annoncé (service $NEW-mdns-alias)"
        else
            warn "avahi-utils absent : $ALIAS.local ne répondra plus, utiliser $NEW_HOST.local ou l'adresse IP"
        fi
    fi
    SMB="$ROOT/etc/samba/smb.conf"
    if [ -n "$(netbios_line)" ] && netbios_line | grep -qiE 'fmo|fm-monitor'; then
        save "$SMB"
        sudo sed -i -E '/^\s*netbios name\s*=/I{s/BL-FMO/BL-RMO/gI;s/FM-MONITOR/BL-RMO/gI}' "$SMB"
        systemctl is-active --quiet nmbd 2>/dev/null && sudo systemctl restart nmbd
        ok "Samba : $(netbios_line | xargs)"
    fi

    step "Démarrage"
    sudo systemctl daemon-reload
    while read -r u n en ac; do
        [ "$en" = enabled ] && sudo systemctl enable "$n" >/dev/null 2>&1
        [ "$ac" = active ] && sudo systemctl start "$n"
    done < "$BK/units.list"
    for x in $(cat "$BK/extra_units.list" 2>/dev/null); do sudo systemctl enable --now "$x" >/dev/null 2>&1 || true; done
    trap - ERR; set +e

    step "Vérifications"
    sleep 5
    fail=0
    while read -r u n en ac; do
        [ "$ac" = active ] || continue
        if systemctl is-active --quiet "$n"; then ok "$n actif"; else warn "$n inactif : sudo journalctl -u $n -n 30 --no-pager"; fail=1; fi
    done < "$BK/units.list"
    code=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:5000/login 2>/dev/null)
    [ "$code" = 000 ] && code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/login 2>/dev/null)
    if [[ "$code" =~ ^(200|302)$ ]]; then ok "Application : page de connexion ($code)"; else warn "Application : réponse $code"; fail=1; fi
    if command -v nginx >/dev/null; then
        code=$(curl -sk -o /dev/null -w '%{http_code}' -H "Host: $NEW_HOST.local" https://127.0.0.1/login 2>/dev/null)
        [[ "$code" =~ ^(200|302)$ ]] && ok "Nginx : https://$NEW_HOST.local ($code)" || { warn "Nginx : réponse $code"; fail=1; }
    fi
    if command -v avahi-resolve >/dev/null; then
        for h in "$NEW_HOST" $ALIAS; do
            r=$(timeout 5 avahi-resolve -4 -n "$h.local" 2>/dev/null | awk '{print $2}')
            [ -n "$r" ] && ok "$h.local -> $r" || warn "$h.local ne répond pas encore (quelques secondes)"
        done
    fi

    echo
    if [ "$fail" = 0 ]; then
        echo -e "${GREEN}Migration terminée.${NC}"
    else
        echo -e "${YELLOW}Migration faite, avec des points à vérifier ci-dessus.${NC}"
    fi
    cat <<EOF

  Adresse          : https://$NEW_HOST.local   (et https://${ALIAS:-$NEW_HOST}.local, https://$(ip_addr))
  Dossier          : $NEW_DIR   ($OLD_DIR est un lien vers lui)
  Services         : $(awk '{print $2}' "$BK/units.list" | xargs)
  Sauvegarde       : $BK
  Retour arrière   : bash $BK/migrate-to-bl-rmo.sh --rollback

  Reste à faire :
    cd $NEW_DIR && git status        (puis commit comme d'habitude)
    Ouvrir une nouvelle session SSH pour voir le nouveau nom dans l'invite.
EOF
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════════
if [ "$MODE" = rollback ]; then
    BK="${2:-$(cat "$STATE" 2>/dev/null || true)}"
    [ -n "$BK" ] && [ -f "$BK/units.list" ] || die "Sauvegarde de migration introuvable (fichier $STATE)."
    sudo -v || die "sudo refusé."
    read -r -p "Revenir à l'installation $OLD (sauvegarde $BK) ? [o/N] " r
    [[ "$r" =~ ^[oOyY] ]] || { echo "Abandon."; exit 0; }

    step "Arrêt des services $NEW"
    for x in $(cat "$BK/extra_units.list" 2>/dev/null); do sudo systemctl disable --now "$x" >/dev/null 2>&1 || true; done
    while read -r u n en ac; do
        sudo systemctl stop "$n" 2>/dev/null || true
        sudo systemctl disable "$n" >/dev/null 2>&1 || true
    done < <(tac "$BK/units.list")
    ok "Arrêtés"

    step "Fichiers système"
    tac "$BK/created.list" | while read -r p; do [ -n "$p" ] && sudo rm -rf "$p"; done
    while read -r p; do
        [ -n "$p" ] || continue
        sudo rm -rf "$p"; sudo mkdir -p "$(dirname "$p")"; sudo cp -a "$BK/files$p" "$p"
    done < "$BK/modified.list"
    ok "$(wc -l < "$BK/modified.list") fichier(s) restauré(s), $(wc -l < "$BK/created.list") retiré(s)"
    [ -f "$BK/crontab.user" ] && crontab "$BK/crontab.user" && ok "crontab de $USER"
    [ -f "$BK/crontab.root" ] && sudo crontab "$BK/crontab.root" && ok "crontab de root"
    sudo hostnamectl set-hostname "$(cat "$BK/hostname.old")" && ok "Nom de la machine : $(cat "$BK/hostname.old")"

    step "Dossier"
    if [ -d "$NEW_DIR" ] && [ ! -L "$NEW_DIR" ]; then
        while read -r f; do
            [ -n "$f" ] || continue
            cp -a "$BK/$(echo "${f#$NEW_DIR/}" | tr / _).inner" "$f"
        done < "$BK/inner.list"
        [ -L "$OLD_DIR" ] && rm -f "$OLD_DIR"
        [ -e "$OLD_DIR" ] && die "$OLD_DIR existe et n'est pas un lien : à régler à la main."
        mv "$NEW_DIR" "$OLD_DIR"
        ok "$NEW_DIR -> $OLD_DIR"
    fi

    step "Redémarrage"
    sudo systemctl daemon-reload
    while read -r u n en ac; do
        [ "$en" = enabled ] && sudo systemctl enable "$u" >/dev/null 2>&1
        [ "$ac" = active ] && sudo systemctl start "$u"
    done < "$BK/units.list"
    systemctl is-active --quiet avahi-daemon 2>/dev/null && sudo systemctl restart avahi-daemon
    systemctl is-active --quiet nmbd 2>/dev/null && sudo systemctl restart nmbd
    command -v nginx >/dev/null && sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx
    rm -f "$STATE"
    sleep 3
    while read -r u n en ac; do
        [ "$ac" = active ] || continue
        systemctl is-active --quiet "$u" && ok "$u actif" || warn "$u inactif"
    done < "$BK/units.list"
    echo -e "\n${GREEN}Retour arrière terminé.${NC} La sauvegarde $BK peut être supprimée."
fi
