#!/usr/bin/env python3
"""
aegis-code: read-only 2FA code viewer for an encrypted Aegis vault backup.

Decrypts the vault in memory only and prints the current code for one or more
services (comma-separated). Supports TOTP, Steam Guard and HOTP entries.

READ-ONLY GUARANTEE: the vault is opened for reading once and never written.
There is no output flag, nothing is written to disk, and secrets are never
printed. The only thing that ever leaves memory is the code you asked for.

Requires: pip install cryptography
"""

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import math
import os
import struct
import sys
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

STEAM_CHARS = "23456789BCDFGHJKMNPQRTVWXY"
HASHES = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}
SUPPORTED = ("totp", "steam", "hotp")
MAX_PASSWORD_ATTEMPTS = 3
NEXT_BELOW_ONCE = 10  # one-shot mode: also show the next code when <= this many seconds are left
NEXT_BELOW_LIVE = 5   # live mode: same, but with a tighter threshold


class WrongPassword(Exception):
    pass


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- vault ----

def decrypt_vault(data, password):
    """Decrypt an Aegis vault (already parsed JSON) in memory. Pure function."""
    header = data["header"]
    slots = [s for s in header["slots"] if s.get("type") == 1]  # 1 = password slot
    if not slots:
        die("vault has no password slot (biometric-only slots are not supported)")

    pw = password.encode("utf-8")
    master_key = None
    for slot in slots:
        kdf = Scrypt(
            salt=bytes.fromhex(slot["salt"]),
            length=32,
            n=slot["n"],
            r=slot["r"],
            p=slot["p"],
        )
        key = kdf.derive(pw)
        params = slot["key_params"]
        try:
            master_key = AESGCM(key).decrypt(
                bytes.fromhex(params["nonce"]),
                bytes.fromhex(slot["key"]) + bytes.fromhex(params["tag"]),
                None,
            )
            break
        except InvalidTag:
            continue
    if master_key is None:
        raise WrongPassword()

    params = header["params"]
    content = base64.b64decode(data["db"])
    db = AESGCM(master_key).decrypt(
        bytes.fromhex(params["nonce"]),
        content + bytes.fromhex(params["tag"]),
        None,
    )
    return json.loads(db.decode("utf-8"))


def load_db(path):
    """Read the vault file (read-only) and return the decrypted database."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        die(f"cannot read {path}: {e.strerror}")
    except json.JSONDecodeError:
        die(f"{path} is not valid JSON")

    header = data.get("header") or {}
    if not header.get("slots"):  # unencrypted Aegis export
        db = data.get("db")
        return db if isinstance(db, dict) else json.loads(db)

    for attempt in range(MAX_PASSWORD_ATTEMPTS):
        password = getpass.getpass("Aegis vault password: ")
        try:
            return decrypt_vault(data, password)
        except WrongPassword:
            left = MAX_PASSWORD_ATTEMPTS - attempt - 1
            print("Wrong password." + (f" {left} attempt(s) left." if left else ""),
                  file=sys.stderr)
    die("too many wrong passwords")


# ---------------------------------------------------------------- codes ----

def b32decode(secret):
    s = secret.replace(" ", "").replace("-", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _truncate(key, counter, algo):
    digest = hmac.new(key, struct.pack(">Q", counter), HASHES[algo]).digest()
    offset = digest[-1] & 0x0F
    return struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF


def hotp_code(key, counter, digits=6, algo="SHA1"):
    return str(_truncate(key, counter, algo) % (10 ** digits)).zfill(digits)


def steam_code(key, counter):
    n = _truncate(key, counter, "SHA1")
    out = ""
    for _ in range(5):
        out += STEAM_CHARS[n % len(STEAM_CHARS)]
        n //= len(STEAM_CHARS)
    return out


def compute(entry, now):
    """Return a dict describing the current code(s) for an entry, or None."""
    kind = entry.get("type")
    if kind not in SUPPORTED:
        return None
    info = entry.get("info", {})
    key = b32decode(info["secret"])
    algo = info.get("algo", "SHA1").upper()
    digits = info.get("digits", 6)

    if kind in ("totp", "steam"):
        period = info.get("period", 30)
        counter = int(now // period)
        code = steam_code(key, counter) if kind == "steam" else hotp_code(key, counter, digits, algo)
        return {"code": code, "left": period - (now % period), "period": period}

    counter = info.get("counter", 0)  # HOTP
    return {
        "counter": counter,
        "codes": [hotp_code(key, counter + i, digits, algo) for i in range(3)],
    }


# -------------------------------------------------------------- display ----

def label(entry):
    issuer, name = entry.get("issuer", ""), entry.get("name", "")
    if issuer and name:
        return f"{issuer} ({name})"
    return issuer or name or "(unnamed)"


def group(code):
    if code.isdigit() and len(code) in (6, 8):
        h = len(code) // 2
        return f"{code[:h]} {code[h:]}"
    return code


MAX_LABEL = 32  # longer labels are truncated so a row never wraps (keeps --live redraw intact)


def fields(entry, now, next_below):
    """Return (label, code, timer, next) as display strings for one entry."""
    name = label(entry)
    if len(name) > MAX_LABEL:
        name = name[:MAX_LABEL - 2] + ".."
    r = compute(entry, now)
    if r is None:
        return name, "-", f"{entry.get('type')} not supported", ""
    if "codes" in r:
        c = r["codes"]
        return (name, c[0], f"HOTP #{r['counter']}",
                f"next: {c[1]}, {c[2]} (stored counter may be stale)")
    left = max(1, math.ceil(r["left"]))
    filled = round(10 * r["left"] / r["period"])
    timer = f"[{'#' * filled}{'-' * (10 - filled)}] {left:>2}s"
    nxt = ""
    if r["left"] <= next_below:
        nxt = f"next: {group(compute(entry, now + r['left'] + 1)['code'])}"
    return name, group(r["code"]), timer, nxt


def render(entries, now, next_below):
    """Build one aligned line per entry (label | code | timer | next)."""
    rows = [fields(e, now, next_below) for e in entries]
    w = [max(len(r[i]) for r in rows) for i in range(3)]
    return [f"{l:<{w[0]}}  {c:<{w[1]}}  {t:<{w[2]}}" + (f"  {n}" if n else "")
            for l, c, t, n in rows]


def enable_ansi():
    if os.name == "nt":
        os.system("")  # turns on ANSI escape handling in Windows terminals


def show_once(entries):
    now = time.time()
    print("\n".join(render(entries, now, NEXT_BELOW_ONCE)))


def show_live(entries):
    enable_ansi()
    print("Live mode, Ctrl+C to quit.\n", file=sys.stderr)
    n = len(entries)
    first = True
    try:
        while True:
            now = time.time()
            if not first:
                sys.stdout.write(f"\033[{n}A")
            for ln in render(entries, now, NEXT_BELOW_LIVE):
                sys.stdout.write("\033[2K\r" + ln + "\n")
            sys.stdout.flush()
            first = False
            time.sleep(1 - (time.time() % 1) + 0.02)
    except KeyboardInterrupt:
        print()


# ------------------------------------------------------------ selection ----

def match(entries, query):
    words = query.lower().split()
    return [e for e in entries
            if all(w in f"{e.get('issuer', '')} {e.get('name', '')}".lower() for w in words)]


def pick(matches, term):
    print(f'Multiple matches for "{term}":', file=sys.stderr)
    for i, e in enumerate(matches, 1):
        print(f"  {i}) {label(e)}  [{e.get('type')}]", file=sys.stderr)
    while True:
        raw = input("Number(s) comma-separated, 'a' for all, Enter to cancel: ").strip().lower()
        if not raw:
            sys.exit(0)
        if raw == "a":
            return matches
        try:
            idx = [int(x) for x in raw.split(",")]
            if all(1 <= i <= len(matches) for i in idx):
                return [matches[i - 1] for i in idx]
        except ValueError:
            pass
        print("Invalid choice.", file=sys.stderr)


# ----------------------------------------------------------------- main ----

def main():
    p = argparse.ArgumentParser(
        description="Read-only 2FA code viewer for an encrypted Aegis vault backup.",
        epilog="Read-only: never modifies the vault and never writes anything to disk.",
    )
    p.add_argument("-i", "--input", required=True, help="Aegis vault backup file (.json)")
    p.add_argument("-s", "--service",
                   help="service(s) to look up, case-insensitive. Separate several with commas "
                        "(e.g. 'github,steam'). Words separated by a space must all match one "
                        "entry (e.g. 'google work'). Omit to list all entries without codes")
    p.add_argument("-a", "--all", action="store_true",
                   help="if a term matches several entries, show all of them instead of asking")
    p.add_argument("-l", "--live", action="store_true",
                   help="keep refreshing the codes every second until Ctrl+C")
    args = p.parse_args()

    db = load_db(args.input)
    entries = db.get("entries", [])

    if not args.service:
        for i, e in enumerate(sorted(entries, key=lambda x: label(x).lower()), 1):
            note = "" if e.get("type") in SUPPORTED else "  (unsupported)"
            print(f"{i:>3}. {label(e)}  [{e.get('type')}]{note}")
        return

    chosen, seen = [], set()
    for term in [t.strip() for t in args.service.split(",") if t.strip()]:
        matches = match(entries, term)
        if not matches:
            print(f"no entry matches '{term}'", file=sys.stderr)
            continue
        picked = matches if (args.all or len(matches) == 1) else pick(matches, term)
        for e in picked:
            if id(e) not in seen:
                seen.add(id(e))
                chosen.append(e)
    if not chosen:
        die("nothing matched (run without --service to list all entries)")

    unsupported = [e for e in chosen if e.get("type") not in SUPPORTED]
    for e in unsupported:
        print(f"skipping {label(e)}: type '{e.get('type')}' is not supported", file=sys.stderr)
    chosen = [e for e in chosen if e not in unsupported]
    if not chosen:
        die("nothing to show")

    if args.live and sys.stdout.isatty():
        show_live(chosen)
    else:
        if args.live:
            print("not a terminal, ignoring --live", file=sys.stderr)
        show_once(chosen)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)
