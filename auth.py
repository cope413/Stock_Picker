"""User accounts and signed session cookies for the web UI.

Passwords are scrypt-hashed with a per-user salt and stored in
``webapp_users.json`` (git-ignored, chmod 600). Sessions are stateless
HMAC-signed tokens so a container restart doesn't log anyone out; the
signing key lives in ``webapp_secret.key`` and is created on first use.
The token signature covers a prefix of the password hash, so changing a
password (or deleting the user) invalidates that user's sessions.

There is no self-registration — users are managed from the CLI:

    python webapp.py adduser <name>     # prompts for a password
    python webapp.py passwd  <name>
    python webapp.py deluser <name>
    python webapp.py users
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from typing import Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(_HERE, "webapp_users.json")
SECRET_FILE = os.path.join(_HERE, "webapp_secret.key")

SESSION_COOKIE = "sp_session"
SESSION_TTL = 30 * 24 * 3600            # 30 days

USERNAME_RE = re.compile(r"^[a-z0-9._-]{1,32}$")
MIN_PASSWORD_LEN = 8

_SCRYPT = dict(n=2 ** 14, r=8, p=1)     # ~16 MB, fine in the container


# --------------------------------------------------------------------------- #
# User store
# --------------------------------------------------------------------------- #

def _load_users() -> Dict[str, Dict]:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE) as f:
        return json.load(f)


def _save_users(users: Dict[str, Dict]) -> None:
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(users, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_FILE)


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


def _check_new(name: str, password: str) -> str:
    name = name.strip().lower()
    if not USERNAME_RE.match(name):
        raise ValueError("Username must be 1-32 chars: a-z 0-9 . _ -")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} "
                         "characters.")
    return name


def create_user(name: str, password: str) -> None:
    name = _check_new(name, password)
    users = _load_users()
    if name in users:
        raise ValueError(f"User {name!r} already exists.")
    salt = secrets.token_bytes(16)
    users[name] = {"salt": salt.hex(), "hash": _hash(password, salt).hex(),
                   "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _save_users(users)


def set_password(name: str, password: str) -> None:
    name = _check_new(name, password)
    users = _load_users()
    if name not in users:
        raise ValueError(f"No such user {name!r}.")
    salt = secrets.token_bytes(16)
    users[name].update(salt=salt.hex(), hash=_hash(password, salt).hex())
    _save_users(users)


def delete_user(name: str) -> None:
    users = _load_users()
    name = name.strip().lower()
    if name not in users:
        raise ValueError(f"No such user {name!r}.")
    del users[name]
    _save_users(users)


def list_users() -> Dict[str, Dict]:
    return _load_users()


def verify_user(name: str, password: str) -> bool:
    users = _load_users()
    rec = users.get(name.strip().lower())
    if rec is None:
        # Burn the same work as a real check so a missing user isn't
        # detectable by response time.
        _hash(password, b"x" * 16)
        return False
    got = _hash(password, bytes.fromhex(rec["salt"]))
    return hmac.compare_digest(got, bytes.fromhex(rec["hash"]))


# --------------------------------------------------------------------------- #
# Session tokens — user:expiry:hmac(secret, user:expiry:hash-prefix)
# --------------------------------------------------------------------------- #

def _secret() -> bytes:
    if not os.path.exists(SECRET_FILE):
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    with open(SECRET_FILE, "rb") as f:
        return f.read()


def _sign(user: str, exp: int, pw_hash: str) -> str:
    msg = f"{user}:{exp}:{pw_hash[:16]}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def issue_token(user: str) -> str:
    rec = _load_users()[user]
    exp = int(time.time()) + SESSION_TTL
    return f"{user}:{exp}:{_sign(user, exp, rec['hash'])}"


def verify_token(token: str) -> Optional[str]:
    """Return the username for a valid, unexpired token, else None."""
    try:
        user, exp_s, sig = token.split(":")
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return None
    if exp < time.time():
        return None
    rec = _load_users().get(user)
    if rec is None:
        return None
    if not hmac.compare_digest(sig, _sign(user, exp, rec["hash"])):
        return None
    return user


# --------------------------------------------------------------------------- #
# CLI — invoked from webapp.py
# --------------------------------------------------------------------------- #

def _read_password(confirm: bool) -> str:
    if sys.stdin.isatty():
        pw = getpass.getpass("Password: ")
        if confirm and getpass.getpass("Again: ") != pw:
            raise ValueError("Passwords do not match.")
        return pw
    return sys.stdin.readline().rstrip("\n")   # piped, e.g. from a script


def cli(argv: list) -> int:
    usage = ("usage: python webapp.py "
             "{adduser <name> | passwd <name> | deluser <name> | users}")
    try:
        cmd = argv[0]
        if cmd == "adduser":
            create_user(argv[1], _read_password(confirm=True))
            print(f"User {argv[1].strip().lower()!r} created.")
        elif cmd == "passwd":
            set_password(argv[1], _read_password(confirm=True))
            print("Password changed; existing sessions are now invalid.")
        elif cmd == "deluser":
            delete_user(argv[1])
            print(f"User {argv[1].strip().lower()!r} deleted.")
        elif cmd == "users":
            users = list_users()
            if not users:
                print("No users yet — add one with: "
                      "python webapp.py adduser <name>")
            for name, rec in sorted(users.items()):
                print(f"{name}\tcreated {rec.get('created', '?')}")
        else:
            print(usage)
            return 2
    except IndexError:
        print(usage)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
