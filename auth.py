"""
Authentification et comptes utilisateurs (Bcrypt, SQLite).

Trois rôles, du moins au plus étendu :
  - lecteur   : consulte les pages, écoute le direct, la pige et les extraits d'alertes.
                Pas d'export, pas d'enregistrement, pas de réglages.
  - operateur : lecteur + exports (pige, extraits, chaîne, journaux), enregistrement,
                coupure des alertes mail, relevé immédiat d'un appareil.
  - admin     : tout, dont Configuration, « Gérer la chaîne » et les comptes.

Les comptes sont dans la table `users` de fm_monitor.db. Au premier démarrage, le
compte de config.json (section « auth ») devient le premier administrateur.
"""
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from flask_bcrypt import Bcrypt

logger = logging.getLogger(__name__)
bcrypt = Bcrypt()

ROLES = ('lecteur', 'operateur', 'admin')
ROLE_LABELS = {'lecteur': 'Lecteur', 'operateur': 'Opérateur', 'admin': 'Administrateur'}
ROLE_LEVEL = {r: i for i, r in enumerate(ROLES)}
MIN_PASSWORD = 8


class AuthError(ValueError):
    pass


class Auth:
    def __init__(self, config_path='config.json', db_path='fm_monitor.db'):
        self.config_path = config_path
        self.db_path = db_path
        self._init_db()

    # ── Base ────────────────────────────────────────────────────────────
    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_db(self):
        with self._conn() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'lecteur',
                enabled INTEGER NOT NULL DEFAULT 1,
                full_name TEXT DEFAULT '',
                created_at TEXT,
                last_login TEXT
            )''')
            if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
                return
            # Premier démarrage : reprendre le compte de config.json
            username, pw_hash = 'admin', None
            try:
                with open(self.config_path) as f:
                    a = json.load(f).get('auth', {})
                username = a.get('username') or username
                pw_hash = a.get('password_hash')
            except (OSError, ValueError):
                pass
            if not pw_hash:
                pw_hash = bcrypt.generate_password_hash('password').decode('utf-8')
                logger.warning("Aucun compte trouvé : création de admin / password. CHANGEZ CE MOT DE PASSE.")
            c.execute('INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)',
                      (username, pw_hash, 'admin', _now()))
            logger.info(f"Comptes : « {username} » repris de config.json comme administrateur")

    # ── Lecture ─────────────────────────────────────────────────────────
    @staticmethod
    def _public(r):
        if r is None:
            return None
        return {'id': r['id'], 'username': r['username'], 'role': r['role'],
                'role_label': ROLE_LABELS.get(r['role'], r['role']), 'enabled': bool(r['enabled']),
                'full_name': r['full_name'] or '', 'created_at': r['created_at'], 'last_login': r['last_login']}

    def get_user(self, username=None, user_id=None):
        with self._conn() as c:
            if user_id is not None:
                r = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            else:
                r = c.execute('SELECT * FROM users WHERE username=?', (username or '',)).fetchone()
        return self._public(r)

    def list_users(self):
        with self._conn() as c:
            rows = c.execute('SELECT * FROM users ORDER BY username COLLATE NOCASE').fetchall()
        return [self._public(r) for r in rows]

    # ── Connexion ───────────────────────────────────────────────────────
    def verify_credentials(self, username, password):
        """Renvoie le compte (dict) si les identifiants sont bons et le compte actif, sinon None."""
        if not username or not password:
            return None
        with self._conn() as c:
            r = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if not r or not r['enabled']:
                return None
            try:
                ok = bcrypt.check_password_hash(r['password_hash'], password)
            except Exception as e:
                logger.error(f"Vérification du mot de passe : {e}")
                ok = False
            if not ok:
                return None
            c.execute('UPDATE users SET last_login=? WHERE id=?', (_now(), r['id']))
        return self._public(r)

    def current_user(self):
        """Compte de la session en cours (relu en base : rôle et désactivation pris en compte aussitôt)."""
        if not session.get('logged_in'):
            return None
        u = self.get_user(session.get('username'))
        return u if u and u['enabled'] else None

    # ── Modification ────────────────────────────────────────────────────
    @staticmethod
    def _check_password(pw):
        if not pw or len(pw) < MIN_PASSWORD:
            raise AuthError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD} caractères")

    @staticmethod
    def _check_username(name):
        name = (name or '').strip()
        if not name or len(name) > 64 or any(ch.isspace() for ch in name):
            raise AuthError("Nom d'utilisateur invalide (sans espace, 64 caractères au plus)")
        return name

    def _admins_left(self, c, excluding_id):
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1 AND id<>?",
                         (excluding_id,)).fetchone()[0]

    def create_user(self, username, password, role='lecteur', full_name=''):
        username = self._check_username(username)
        self._check_password(password)
        if role not in ROLES:
            raise AuthError('Rôle inconnu')
        with self._conn() as c:
            if c.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                raise AuthError('Ce nom d\'utilisateur existe déjà')
            cur = c.execute('INSERT INTO users (username, password_hash, role, full_name, created_at) VALUES (?,?,?,?,?)',
                            (username, self.hash_password(password), role, (full_name or '').strip()[:100], _now()))
            return cur.lastrowid

    def update_user(self, user_id, username=None, role=None, enabled=None, password=None, full_name=None):
        with self._conn() as c:
            r = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not r:
                raise AuthError('Compte introuvable')
            sets, vals = [], []
            if username is not None and username != r['username']:
                username = self._check_username(username)
                if c.execute('SELECT 1 FROM users WHERE username=? AND id<>?', (username, user_id)).fetchone():
                    raise AuthError('Ce nom d\'utilisateur existe déjà')
                sets.append('username=?'); vals.append(username)
            if role is not None and role != r['role']:
                if role not in ROLES:
                    raise AuthError('Rôle inconnu')
                if r['role'] == 'admin' and not self._admins_left(c, user_id):
                    raise AuthError('Il faut garder au moins un administrateur actif')
                sets.append('role=?'); vals.append(role)
            if enabled is not None and bool(enabled) != bool(r['enabled']):
                if not enabled and r['role'] == 'admin' and not self._admins_left(c, user_id):
                    raise AuthError('Il faut garder au moins un administrateur actif')
                sets.append('enabled=?'); vals.append(1 if enabled else 0)
            if password:
                self._check_password(password)
                sets.append('password_hash=?'); vals.append(self.hash_password(password))
            if full_name is not None:
                sets.append('full_name=?'); vals.append(full_name.strip()[:100])
            if sets:
                c.execute(f'UPDATE users SET {", ".join(sets)} WHERE id=?', (*vals, user_id))
        return self.get_user(user_id=user_id)

    def delete_user(self, user_id):
        with self._conn() as c:
            r = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not r:
                raise AuthError('Compte introuvable')
            if r['role'] == 'admin' and not self._admins_left(c, user_id):
                raise AuthError('Il faut garder au moins un administrateur actif')
            c.execute('DELETE FROM users WHERE id=?', (user_id,))
        return r['username']

    def change_own_password(self, username, current, new):
        with self._conn() as c:
            r = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if not r or not self.check_password(current or '', r['password_hash']):
            raise AuthError('Mot de passe actuel incorrect')
        self._check_password(new)
        self.update_user(r['id'], password=new)

    # ── Décorateurs ─────────────────────────────────────────────────────
    def login_required(self, f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'logged_in' not in session:
                if request.is_json or request.path.startswith('/api/'):
                    return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
                return redirect(url_for('login', next=request.url))
            return f(*args, **kwargs)
        return decorated_function

    # ── Outils ──────────────────────────────────────────────────────────
    @staticmethod
    def hash_password(password):
        return bcrypt.generate_password_hash(password).decode('utf-8')

    @staticmethod
    def check_password(password, password_hash):
        try:
            return bcrypt.check_password_hash(password_hash, password)
        except Exception as e:
            logger.error(f"Erreur lors de la vérification du mot de passe: {e}")
            return False


def _now():
    return datetime.now().isoformat(timespec='seconds')
