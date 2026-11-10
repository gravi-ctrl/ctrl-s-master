#!/usr/bin/env python3
"""
recovery-codes: one encrypted file for your 2FA recovery codes.

Plaintext only ever lives in memory. Codes are never taken as command-line
arguments (shell history), never written to disk unencrypted, and `use` wipes
the screen after you have copied the code.

    recovery-codes.py -i VAULT init
    recovery-codes.py -i VAULT add SERVICE [--from-file FILE] [--notes]
    recovery-codes.py -i VAULT list
    recovery-codes.py -i VAULT show [SERVICE]
    recovery-codes.py -i VAULT use [SERVICE]
    recovery-codes.py -i VAULT unuse [SERVICE]
    recovery-codes.py -i VAULT rm [SERVICE]
    recovery-codes.py -i VAULT rename [SERVICE] [NEW_NAME]
    recovery-codes.py -i VAULT note [SERVICE] [--clear]
    recovery-codes.py -i VAULT passwd

Requires: pip install -U cryptography   (version 44.0 or newer, for Argon2id)

---------------------------------------------------------------------------
FILE FORMAT (keep a copy of this on your paper sheet: it is all you need to
write a decryptor if this script is ever lost)

    offset  size  field
    0       4     magic            b"RCVT"
    4       1     format version   1
    5       1     KDF id           1 = Argon2id
    6       4     memory, KiB      big-endian
    10      1     iterations (t)
    11      1     lanes (p)
    12      16    salt
    28      12    nonce
    40      ...   AES-256-GCM ciphertext + 16-byte tag

    key        = Argon2id(password as UTF-8, salt, length=32, t, lanes=p, memory)
    plaintext  = AES-256-GCM decrypt, nonce from header, AAD = the 40 header bytes
    payload    = 4-byte big-endian length N, then N bytes of UTF-8 JSON, then
                 zero padding up to a multiple of 4096 bytes
    JSON       = {"v": 1, "services": {NAME: {"notes", "regenerated_at",
                  "codes": [{"value", "used", "used_at"}]}}}

Vault files use the extension .rcv (`init` adds it to a bare name). Backups are exact
copies of the vault file, named recovery-backup-<date>-<time>.rcv, in VAULT.backups/.
---------------------------------------------------------------------------
"""

import argparse
import getpass
import hashlib
import json
import os
import secrets
import struct
import sys
import time
import unicodedata
from datetime import datetime

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
except ImportError:
    sys.exit("error: needs the 'cryptography' package, version 44.0 or newer "
             "(pip install -U cryptography)")

# ------------------------------------------------------------- settings ----

VAULT_EXT = ".rcv"            # added to a bare vault name by `init`; used on backups too

MAGIC = b"RCVT"
FORMAT_VERSION = 1
KDF_ARGON2ID = 1
HEADER = struct.Struct(">4sBBIBB16s12s")  # magic, version, kdf, memory KiB, t, lanes, salt, nonce

# Used for new vaults, and to upgrade older vaults on their next save.
DEFAULT_MEMORY_KIB = 512 * 1024   # TODO(test): tune to ~1-2 s on your slowest realistic machine
DEFAULT_TIME = 3
DEFAULT_LANES = 4

# Upper bounds, checked BEFORE deriving a key so a corrupted header can't make
# the tool try to allocate absurd amounts of memory.
MAX_MEMORY_KIB = 1024 * 1024
MAX_TIME = 10
MAX_LANES = 16

PAD_TO = 4096                 # payload padded so file size doesn't reveal the code count
MAX_VAULT_FILE = 1024 * 1024  # a vault is a few KiB; refuse to read anything bigger
MAX_INPUT_FILE = 64 * 1024    # cap for --from-file
MIN_PASSWORD_LEN = 12
MAX_ATTEMPTS = 3
KEEP_BACKUPS = 15
LOW_AT = 3                    # `list` flags LOW when some codes are used and this many or fewer are left
NAME_MAX = 32                 # longer service names are shortened in listings


class WrongPassword(Exception):
    pass


def die(msg):
    # Python prints a string passed to sys.exit() at interpreter exit, i.e. after
    # any `finally` block (such as the screen wipe in `use`) has run.
    sys.exit(f"error: {msg}")


# --------------------------------------------------------------- crypto ----

def derive_key(password, salt, memory_kib, iterations, lanes):
    kdf = Argon2id(salt=salt, length=32, iterations=iterations,
                   lanes=lanes, memory_cost=memory_kib)
    return kdf.derive(password.encode("utf-8"))


def canonical(db):
    return json.dumps(db, sort_keys=True)


def encrypt_db(db, password, memory_kib, iterations, lanes):
    """Return the full file bytes. Fresh salt and nonce on every call."""
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    header = HEADER.pack(MAGIC, FORMAT_VERSION, KDF_ARGON2ID,
                         memory_kib, iterations, lanes, salt, nonce)
    raw = canonical(db).encode("utf-8")
    body = struct.pack(">I", len(raw)) + raw
    body += b"\0" * (-len(body) % PAD_TO)
    key = derive_key(password, salt, memory_kib, iterations, lanes)
    return header + AESGCM(key).encrypt(nonce, body, header)


def parse_header(blob):
    """Validate the header and return its fields. Never derives a key."""
    if len(blob) < HEADER.size + 16:
        die("file is too short to be a vault")
    magic, version, kdf, memory, iterations, lanes, salt, nonce = HEADER.unpack_from(blob)
    if magic != MAGIC:
        die("not a recovery-codes vault (wrong magic bytes)")
    if version != FORMAT_VERSION:
        die(f"unsupported vault format version {version}")
    if kdf != KDF_ARGON2ID:
        die(f"unknown KDF id {kdf}")
    if not (1 <= lanes <= MAX_LANES and 1 <= iterations <= MAX_TIME
            and 8 * lanes <= memory <= MAX_MEMORY_KIB):
        die("vault header has out-of-range KDF settings (damaged file?)")
    return {"memory": memory, "iterations": iterations, "lanes": lanes,
            "salt": salt, "nonce": nonce}


def decrypt_db(blob, password):
    """Decrypt file bytes in memory. Returns (db, header). Raises WrongPassword."""
    h = parse_header(blob)
    key = derive_key(password, h["salt"], h["memory"], h["iterations"], h["lanes"])
    try:
        body = AESGCM(key).decrypt(h["nonce"], blob[HEADER.size:], blob[:HEADER.size])
    except InvalidTag:
        raise WrongPassword()
    (n,) = struct.unpack(">I", body[:4])
    return json.loads(body[4:4 + n].decode("utf-8")), h


def params_for_save(h):
    """Never downgrade: use the stronger of the stored and the default settings."""
    return (max(h["memory"], DEFAULT_MEMORY_KIB),
            max(h["iterations"], DEFAULT_TIME),
            max(h["lanes"], DEFAULT_LANES))


# ----------------------------------------------------------------- files ----

def read_vault_file(path):
    try:
        if os.path.getsize(path) > MAX_VAULT_FILE:
            die(f"{path} is too big to be a vault")
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        die(f"cannot read {path}: {e.strerror}")


def fingerprint(blob):
    return hashlib.sha256(blob).digest()


def _create_exclusive(path):
    """Create a new file, failing if it exists. Owner-only permissions where supported."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    return os.open(path, flags, 0o600)


def _write_synced(fd, data):
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def resolve_vault_path(path, creating):
    """Give a bare vault name the .rcv extension.

    init:   'codes' -> 'codes.rcv'. A name that already has an extension is used as typed.
    others: 'codes' finds 'codes.rcv' if that is what exists. An existing vault with
            no extension (made by an earlier version) keeps working as it is.
    """
    if os.path.splitext(path)[1]:
        return path
    if creating:
        return path + VAULT_EXT
    if not os.path.exists(path) and os.path.exists(path + VAULT_EXT):
        return path + VAULT_EXT
    return path


def backup_dir(vault):
    return vault + ".backups"


def list_backups(vault):
    d = backup_dir(vault)
    if not os.path.isdir(d):
        return []
    # Sort by name without the extension: timestamps sort chronologically, and a
    # same-second counter suffix sorts after the original.
    return sorted((n for n in os.listdir(d) if n.startswith("recovery-backup-")),
                  key=lambda n: os.path.splitext(n)[0])


def new_backup(vault, data):
    """Store `data` as a new, never-overwritten backup. Returns its name."""
    d = backup_dir(vault)
    os.makedirs(d, mode=0o700, exist_ok=True)
    stamp = time.strftime("recovery-backup-%Y%m%d-%H%M%S")
    name, n = stamp + VAULT_EXT, 1
    while True:
        try:
            fd = _create_exclusive(os.path.join(d, name))
            break
        except FileExistsError:
            n += 1
            name = f"{stamp}-{n}{VAULT_EXT}"
    _write_synced(fd, data)
    return name


def prune_backups(vault):
    old = list_backups(vault)[:-KEEP_BACKUPS]
    for name in old:
        os.remove(os.path.join(backup_dir(vault), name))
    return old


def commit(path, password, db, params, make_backup=True, expect=None):
    """Encrypt `db` and atomically replace the vault. Returns the backup name or None.

    temp file -> fsync -> self-test (re-derive from the password, decrypt, compare)
    -> change check -> backup of the previous file -> os.replace. If anything fails
    before the replace, the existing vault is untouched.

    `expect` is the fingerprint of the vault file as it was when opened. If the file
    on disk no longer matches (another copy of the tool, a sync tool), nothing is
    saved. This replaces locking: no lock files, nothing OS-specific, nothing stale.
    """
    blob = encrypt_db(db, password, *params)
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)),
                       f".{os.path.basename(path)}.{secrets.token_hex(4)}.tmp")
    try:
        _write_synced(_create_exclusive(tmp), blob)

        try:
            check, _ = decrypt_db(read_vault_file(tmp), password)
        except WrongPassword:
            check = None
        if check is None or canonical(check) != canonical(db):
            die("self-test failed: the new file does not decrypt back to the same data. "
                "Vault NOT changed.")

        if expect is not None and fingerprint(read_vault_file(path)) != expect:
            die("the vault file changed while this command was running (another copy "
                "of the tool, or a sync?). Nothing was saved. Run the command again.")

        backup = None
        if make_backup and os.path.exists(path):
            backup = new_backup(path, read_vault_file(path))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    prune_backups(path)
    return backup


# ----------------------------------------------------------------- vault ----

class Vault:
    """An opened vault: decrypted data in memory plus what's needed to save it."""

    def __init__(self, path, password, db, header, blob):
        self.path, self.password, self.db, self.header = path, password, db, header
        self.before = canonical(db)
        self.fingerprint = fingerprint(blob)  # of the file as opened

    def save(self):
        """Save only if something actually changed. Returns a message to print."""
        if canonical(self.db) == self.before:
            return "Nothing to change."
        backup = commit(self.path, self.password, self.db, params_for_save(self.header),
                        expect=self.fingerprint)
        self.before = canonical(self.db)
        self.fingerprint = fingerprint(read_vault_file(self.path))
        return f"saved (previous version backed up as {backup})"


def open_vault(path):
    blob = read_vault_file(path)
    parse_header(blob)  # reject junk files before asking for a password
    for attempt in range(MAX_ATTEMPTS):
        password = getpass.getpass("Vault password: ")
        try:
            db, header = decrypt_db(blob, password)
            return Vault(path, password, db, header, blob)
        except WrongPassword:
            left = MAX_ATTEMPTS - attempt - 1
            print("Wrong password or damaged file." +
                  (f" {left} attempt(s) left." if left else ""), file=sys.stderr)
    die("too many failed attempts (wrong password or damaged file)")


def exact_service(db, name):
    for key in db["services"]:
        if key.lower() == name.strip().lower():
            return key
    return None


def summarize(svc):
    """(unused, total, flag, note_lines) for listings."""
    codes = svc["codes"]
    total = len(codes)
    unused = sum(1 for c in codes if not c["used"])
    if total and unused == 0:
        flag = "EMPTY"
    elif total and unused < total and unused <= LOW_AT:
        flag = "LOW"      # some are used and only a few are left: time for a new batch
    else:
        flag = ""
    return unused, total, flag, len(svc.get("notes", "").splitlines())


def short_state(unused, total):
    return f"{unused}/{total} unused" if total else "no codes"


def fit(name):
    return name if len(name) <= NAME_MAX else name[:NAME_MAX - 3] + "..."


def pick_service(db, names, heading=None):
    """Numbered list of `names`; returns (name, data), or None if cancelled."""
    names = sorted(names, key=str.lower)
    info = [summarize(db["services"][n]) for n in names]
    states = [short_state(u, t) for u, t, _, _ in info]
    nw = max(len(fit(n)) for n in names)
    sw = max(len(x) for x in states)
    print(f"\n{heading}" if heading else "")
    for i, (name, (_, _, flag, note_lines), state) in enumerate(zip(names, info, states), 1):
        tail = (f"  {flag}" if flag else "") + ("  notes" if note_lines else "")
        print(f"  {i:>2}) {fit(name):<{nw}}  {state:<{sw}}{tail}")
    choice = pick_number(len(names), "\nNumber of the service (Enter to cancel): ")
    if choice is None:
        return None
    return names[choice - 1], db["services"][names[choice - 1]]


def resolve_service(db, query):
    """A service by name, or (no name given) pick one from a numbered list.

    A name matches exactly (any letter case) or as part of a name; if a part of a
    name matches several services you pick from just those. Returns (name, data),
    or None if the user cancelled the picker."""
    if query is None:
        if not db["services"]:
            die("the vault is empty (add a service first)")
        return pick_service(db, db["services"])
    key = exact_service(db, query)
    if key:
        return key, db["services"][key]
    hits = [k for k in db["services"] if query.strip().lower() in k.lower()]
    if not hits:
        die(f"no service matches '{query}' (try the list command)")
    if len(hits) == 1:
        return hits[0], db["services"][hits[0]]
    return pick_service(db, hits, f"'{query}' matches {len(hits)} services:")


# -------------------------------------------------------------- terminal ----

def clear_screen():
    """Wipe the screen and scrollback (where the terminal supports it)."""
    if sys.stdout.isatty():
        if os.name == "nt":
            os.system("")  # turns on ANSI escape handling in Windows terminals
        sys.stdout.write("\033[2J\033[3J\033[H")
        sys.stdout.flush()


def mask(code):
    """First character + last 3 (last 2 for short codes). The first character is
    there so a stray prefix (a bullet, '1.', a quote) is visible at a glance."""
    if len(code) <= 4:
        return "*" * len(code)
    tail = 3 if len(code) >= 8 else 2
    return code[0] + "*" * (len(code) - 1 - tail) + code[-tail:]


def has_control_chars(text):
    """True for terminal control characters (escape sequences and the like)."""
    return any(unicodedata.category(ch) == "Cc" for ch in text)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def prompt_new_password(label):
    while True:
        a = getpass.getpass(f"{label} (at least {MIN_PASSWORD_LEN} characters): ")
        if len(a) < MIN_PASSWORD_LEN:
            print("Too short. A long passphrase beats a short complex one.", file=sys.stderr)
            continue
        if a != getpass.getpass("Repeat it: "):
            print("They don't match, try again.", file=sys.stderr)
            continue
        return a


def pick_number(count, prompt, extra=None):
    """Ask for a number 1..count, or the single letter `extra`. None if just Enter."""
    while True:
        raw = input(prompt).strip().lower()
        if not raw:
            return None
        if extra and raw == extra:
            return extra
        if raw.isdigit() and 1 <= int(raw) <= count:
            return int(raw)
        print("Invalid choice.", file=sys.stderr)


def _drain_pending_input():
    """Throw away anything still queued (e.g. leftover pasted lines)."""
    try:
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass


def _hidden_line_windows():
    import msvcrt
    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch == "\r":
            if msvcrt.kbhit():          # swallow the \n of a \r\n pair
                nxt = msvcrt.getwch()
                if nxt != "\n":
                    msvcrt.ungetwch(nxt)
            return "".join(chars)
        if ch == "\n":
            return "".join(chars)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):      # special key: ignore it and its follow-up code
            msvcrt.getwch()
        elif ch == "\b":
            chars = chars[:-1]
        else:
            chars.append(ch)


def _input_pending():
    """True if more input is already waiting (the rest of a pasted block)."""
    try:
        if os.name == "nt":
            import msvcrt
            return msvcrt.kbhit()
        import select
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except Exception:
        return False


def read_hidden_block(prompt, progress=None):
    """Read lines with echo off until an empty line. Returns the stripped lines.

    `progress(n)` returns a status line that is shown whenever the input has been
    caught up with (after a typed line, or at the end of a pasted block), so you can
    see each Enter registered without anything secret being printed.

    This deliberately avoids getpass in a loop: on Linux/macOS getpass discards
    pending input every time it runs, which eats the rest of a pasted block.
    """
    print(prompt)
    lines = []
    if not sys.stdin.isatty():  # piped input (tests)
        for raw in sys.stdin:
            if not raw.strip():
                break
            lines.append(raw.strip())
        return lines

    if os.name == "nt":
        while True:
            line = _hidden_line_windows().strip()
            if not line:
                break
            lines.append(line)
            if progress and not _input_pending():
                print(progress(len(lines)))
        print()
        _drain_pending_input()
        return lines

    import termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    quiet = termios.tcgetattr(fd)
    quiet[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSADRAIN, quiet)
    try:
        while True:
            raw = sys.stdin.readline()
            if raw == "" or not raw.strip():
                break
            lines.append(raw.strip())
            if progress and not _input_pending():
                print(progress(len(lines)))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print()
    _drain_pending_input()
    return lines


# ----------------------------------------------------------- code intake ----

def codes_from_paste(skippable=False):
    skip = ("\nNo codes for this one? Just press Enter to go straight to the notes."
            if skippable else "")
    return read_hidden_block(
        "Type or paste the codes, one per line. Nothing is shown on screen.\n"
        "Press Enter on an empty line when you're done; you'll review before anything is saved."
        + skip,
        progress=lambda n: f"  {n} code(s) received. Add more, or press Enter on an empty line "
                           "if you're done:")


def notes_from_paste():
    """Notes are secrets too: typed or pasted with nothing shown, one line per line."""
    lines = read_hidden_block(
        "Type or paste the notes, one per line. Nothing is shown on screen.\n"
        "Press Enter on an empty line when you're done.",
        progress=lambda n: f"  {n} line(s) received. Add more, or press Enter on an empty line "
                           "if you're done:")
    lines = [line.replace("\t", "    ") for line in lines]
    if any(has_control_chars(line) for line in lines):
        die("the notes can't contain control characters")
    return lines


def codes_from_file(path):
    try:
        size = os.path.getsize(path)
    except OSError as e:
        die(f"cannot read {path}: {e.strerror}")
    if size > MAX_INPUT_FILE:
        die(f"{path} is too big for a codes file")
    print("The file must contain one code per line and nothing else\n"
          "(no title, numbering or notes). Fix it first if it doesn't.")
    input("Press Enter to continue, Ctrl+C to cancel: ")
    try:
        with open(path, encoding="utf-8-sig") as f:  # utf-8-sig drops a Notepad BOM
            text = f.read()
    except UnicodeDecodeError:
        die(f"{path} is not a UTF-8 text file")
    except OSError as e:
        die(f"cannot read {path}: {e.strerror}")
    return [line.strip() for line in text.splitlines() if line.strip()]


def review_codes(codes, allow_empty=False):
    """Drop duplicates, show count + masked endings, warn about odd lengths."""
    unique = list(dict.fromkeys(codes))
    if not unique:
        if allow_empty:
            return []
        die("no codes found")
    bad = sum(1 for c in unique if has_control_chars(c))
    if bad:
        die(f"{bad} code(s) contain control characters, which a real code never has. "
            "Nothing was stored; check where the codes came from.")
    print(f"\nRead {len(unique)} code(s):")
    for i, c in enumerate(unique, 1):
        print(f"  {i:>2}) {mask(c)}")
    if len(unique) != len(codes):
        print(f"WARNING: {len(codes) - len(unique)} duplicate(s) removed.")
    lengths = sorted({len(c) for c in unique})
    if len(lengths) > 1:
        print("WARNING: the codes have different lengths "
              f"({', '.join(map(str, lengths))}). A title or numbering left in the input?")
    return unique


# -------------------------------------------------------------- commands ----

def cmd_init(args):
    if os.path.exists(args.input):
        die(f"{args.input} already exists; refusing to overwrite it")
    if not os.path.isdir(os.path.dirname(os.path.abspath(args.input))):
        die("the vault's folder does not exist")
    print(f"Creating a new vault at {args.input}")
    print("There is NO way to recover this vault if you forget its password.\n"
          "Write the password on your paper sheet before you continue.\n")
    password = prompt_new_password("Vault password")
    commit(args.input, password, {"v": 1, "services": {}},
           (DEFAULT_MEMORY_KIB, DEFAULT_TIME, DEFAULT_LANES), make_backup=False)
    print("Vault created and verified (decrypted back successfully).")


def cmd_add(args):
    v = open_vault(args.input)
    name = args.service.strip()
    if not name:
        die("service name is empty")
    if has_control_chars(name):
        die("the service name can't contain control characters")
    existing = exact_service(v.db, name)
    if existing:
        old = v.db["services"][existing]
        unused = sum(1 for c in old["codes"] if not c["used"])
        print(f"'{existing}' already has {len(old['codes'])} code(s) ({unused} unused).")
        if input("Replace the whole batch? Services invalidate old codes when they "
                 "issue new ones. [y/N]: ").strip().lower() != "y":
            print("Cancelled.")
            return
        name = existing

    if args.from_file:
        codes = review_codes(codes_from_file(args.from_file))
    else:
        codes = review_codes(codes_from_paste(skippable=args.notes), allow_empty=args.notes)
    notes = []
    if args.notes:
        notes = notes_from_paste()
        if notes:
            print(f"\nRead {len(notes)} note line(s).")
    if not codes and not notes:
        die("nothing to store")
    if existing and not codes:
        die("no codes given. To change only the notes of an existing service, "
            "use the note command.")

    if input(f"\nStore these under '{name}'? [y/N]: ").strip().lower() != "y":
        print("Cancelled.")
        return

    old = v.db["services"].get(name, {})
    v.db["services"][name] = {
        "notes": "\n".join(notes) if notes else old.get("notes", ""),
        "regenerated_at": now_iso(),
        "codes": [{"value": c, "used": False, "used_at": None} for c in codes],
    }
    message = v.save()
    clear_screen()
    print(message)
    if args.from_file:
        print(f"Reminder: delete {args.from_file} yourself (and the browser's download "
              "cache if the service forced a download).")


def cmd_list(args):
    v = open_vault(args.input)
    services = v.db["services"]
    if not services:
        print("(vault is empty)")
        return
    names = sorted(services, key=str.lower)
    rows = []
    for n in names:
        unused, total, flag, note_lines = summarize(services[n])
        rows.append((fit(n), f"{unused}/{total}" if total else "-", flag,
                     "yes" if note_lines else "", services[n].get("regenerated_at", "")[:10]))
    nw = max(7, max(len(r[0]) for r in rows))
    cw = max(6, max(len(r[1]) for r in rows))
    print(f"\n{'SERVICE':<{nw}}  {'UNUSED':>{cw}}  {'':<5}  {'NOTES':<5}  ADDED")
    for name, counts, flag, notes, added in rows:
        print(f"{name:<{nw}}  {counts:>{cw}}  {flag:<5}  {notes:<5}  {added}")
    if any(r[2] for r in rows):
        print(f"\nLOW: some codes are used and {LOW_AT} or fewer are left (time for a new batch).  "
              "EMPTY: none left.")


def cmd_show(args):
    v = open_vault(args.input)
    found = resolve_service(v.db, args.service)
    if found is None:
        print("Cancelled.")
        return
    name, svc = found
    print(f"{name}  (batch added {svc.get('regenerated_at', '')[:10]})")
    note_lines = len(svc.get("notes", "").splitlines())
    if note_lines:
        print(f"notes: {note_lines} line(s), hidden (reveal them with 'use', then n)")
    for i, c in enumerate(svc["codes"], 1):
        state = f"used {c['used_at'][:10]}" if c["used"] else "unused"
        print(f"  {i:>2}) {mask(c['value'])}  {state}")


def cmd_use(args):
    v = open_vault(args.input)
    found = resolve_service(v.db, args.service)
    if found is None:
        print("Cancelled.")
        return
    name, svc = found
    unused = [c for c in svc["codes"] if not c["used"]]
    notes = svc.get("notes", "")
    if not unused and not notes:
        die(f"{name} has no unused codes left")
    print(f"\n{name}: {len(unused)} unused code(s)")
    for i, c in enumerate(unused, 1):
        print(f"  {i:>2}) {mask(c['value'])}")
    if notes:
        print(f"   n) notes ({len(notes.splitlines())} line(s), hidden)")
    choice = pick_number(
        len(unused),
        "\nNumber to reveal" + (", or n for the notes" if notes else "") + " (Enter to cancel): ",
        extra="n" if notes else None)
    if choice is None:
        print("Cancelled.")
        return

    if choice == "n":
        print()
        for line in notes.splitlines():
            print(f"    {line}")
        print()
        try:
            input("Press Enter once you've read them (the screen is then wiped): ")
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            clear_screen()
        print("Notes hidden again.")
        return

    code = unused[choice - 1]
    print(f"\n    {code['value']}\n")
    message, saving = "Left unused.", False
    try:
        if input("Copy it now. Type 'yes' once you have it "
                 "(anything else leaves it unused): ").strip().lower() in ("y", "yes"):
            code["used"], code["used_at"] = True, now_iso()
            saving = True
            message = f"{mask(code['value'])} marked as used, " + v.save()
    except (KeyboardInterrupt, EOFError):
        message = ("Interrupted. Run 'show' to check whether the code was marked as used."
                   if saving else "Interrupted. The code was NOT marked as used.")
    finally:
        clear_screen()  # runs on every exit path once the code has been shown
    print(message)


def cmd_unuse(args):
    v = open_vault(args.input)
    found = resolve_service(v.db, args.service)
    if found is None:
        print("Cancelled.")
        return
    name, svc = found
    used = [c for c in svc["codes"] if c["used"]]
    if not used:
        die(f"{name} has no used codes")
    print(f"\n{name}: {len(used)} used code(s)")
    for i, c in enumerate(used, 1):
        print(f"  {i:>2}) {mask(c['value'])}  used {c['used_at'][:10]}")
    choice = pick_number(len(used), "\nNumber to mark as unused again (Enter to cancel): ")
    if choice is None:
        print("Cancelled.")
        return
    used[choice - 1]["used"], used[choice - 1]["used_at"] = False, None
    print(v.save())


def cmd_rm(args):
    v = open_vault(args.input)
    found = resolve_service(v.db, args.service)
    if found is None:
        print("Cancelled.")
        return
    name, svc = found
    unused = sum(1 for c in svc["codes"] if not c["used"])
    typed = input(f"This permanently removes '{name}' and its {len(svc['codes'])} codes "
                  f"({unused} unused).\nType the service name to confirm: ")
    if typed.strip().lower() != name.lower():
        print("Cancelled.")
        return
    del v.db["services"][name]
    print(v.save())


def cmd_rename(args):
    v = open_vault(args.input)
    found = resolve_service(v.db, args.service)
    if found is None:
        print("Cancelled.")
        return
    name, _ = found
    if args.new_name is None:
        new = input(f"New name for '{name}' (Enter to cancel): ").strip()
        if not new:
            print("Cancelled.")
            return
    else:
        new = args.new_name.strip()
        if not new:
            die("the new name is empty")
    if has_control_chars(new):
        die("the service name can't contain control characters")
    clash = exact_service(v.db, new)
    if clash and clash != name:  # a change of letter case only (github -> GitHub) is allowed
        die(f"'{clash}' already exists")
    if new == name:
        print("Nothing to change.")
        return
    v.db["services"][new] = v.db["services"].pop(name)
    print(v.save())


def cmd_note(args):
    v = open_vault(args.input)
    found = resolve_service(v.db, args.service)
    if found is None:
        print("Cancelled.")
        return
    name, svc = found
    has_notes = bool(svc.get("notes"))
    if args.clear:
        if not has_notes:
            print(f"'{name}' has no notes.")
            return
        if input(f"Remove all notes from '{name}'? [y/N]: ").strip().lower() != "y":
            print("Cancelled.")
            return
        svc["notes"] = ""
    else:
        print("Notes are secrets: nothing is shown on screen, and the old notes are not displayed.")
        if has_notes:
            print("The new notes REPLACE the current ones.")
        lines = notes_from_paste()
        if not lines:
            print("Cancelled.")
            return
        print(f"\nRead {len(lines)} note line(s).")
        if has_notes and input("Replace the current notes with these? [y/N]: "
                               ).strip().lower() != "y":
            print("Cancelled.")
            return
        svc["notes"] = "\n".join(lines)
    print(v.save())


def cmd_passwd(args):
    v = open_vault(args.input)
    new = prompt_new_password("New password")
    if new == v.password:
        die("the new password is the same as the old one")
    # No pre-change backup: it would be encrypted under the old password.
    commit(v.path, new, v.db, params_for_save(v.header), make_backup=False,
           expect=v.fingerprint)
    print("Password changed and verified.")

    old = list_backups(v.path)
    if old:
        print(f"\n{len(old)} existing backup(s) still open with the OLD password.")
        if input(f"Delete them? Type {len(old)} to confirm, anything else keeps them: "
                 ).strip() == str(len(old)):
            for name in old:
                os.remove(os.path.join(backup_dir(v.path), name))
            print("Old backups deleted.")
        else:
            print("Kept. They still open with the old password.")
    fresh = new_backup(v.path, read_vault_file(v.path))
    print(f"Fresh backup under the new password: {fresh}")
    print("\nNot covered by this tool: older vault copies elsewhere (your encrypted "
          "container, synced folders) and your paper sheet. Update those too.")


# ------------------------------------------------------------------ main ----

def main():
    for stream in (sys.stdout, sys.stderr):  # legacy consoles: print '?' instead of crashing
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Encrypted store for 2FA recovery codes (plaintext only in memory).",
        epilog="""how a command is built:   recovery-codes.py -i VAULT <command> [options]
(-i comes first, then the command, then that command's own options)

examples:
  recovery-codes.py -i codes.rcv init
  recovery-codes.py -i codes.rcv add github
  recovery-codes.py -i codes.rcv add github -f github-codes.txt
  recovery-codes.py -i codes.rcv add github -n        (codes, then notes; both typed or pasted hidden)
  recovery-codes.py -i codes.rcv add questions -n     (notes only: press Enter at the codes prompt)
  recovery-codes.py -i codes.rcv list
  recovery-codes.py -i codes.rcv use               (pick the service from a list)
  recovery-codes.py -i codes.rcv use github        (skip the list; pick n there for the notes)
  recovery-codes.py -i codes.rcv rename github GitHub   (fix a typo in a service name)
  recovery-codes.py -i codes.rcv note github       (replace the notes, typed or pasted hidden)
  recovery-codes.py -i codes.rcv note github --clear

recovery-codes.py -i codes.rcv <command> -h   shows help for one command""")
    p.add_argument("-i", "--input", required=True, metavar="VAULT", help="path to the vault file")
    sub = p.add_subparsers(dest="command", required=True, title="commands")

    def command(name, text, description=None, examples=None):
        # prog includes "-i VAULT" so each command's usage line is the whole command
        epilog = None
        if examples:
            epilog = "examples:\n" + "\n".join(
                f"  {p.prog} -i codes.rcv {e}" for e in examples)
        return sub.add_parser(name, help=text, description=description or text, epilog=epilog,
                              formatter_class=argparse.RawDescriptionHelpFormatter,
                              prog=f"{p.prog} -i VAULT {name}")

    command("init", "create a new, empty vault",
            examples=["init   (a bare name gets the .rcv extension)"])
    a = command("add", "add a service's codes and/or notes (replaces an existing batch)",
                "Add a service's codes. Codes are typed or pasted with nothing shown on\n"
                "screen (every line is one code), or read from a file. With --notes you are\n"
                "then asked for notes the same way (they are secrets too). For a service with\n"
                "no codes (a security question, say), press Enter at the codes prompt.\n"
                "--from-file and --notes are independent: use both, either, or neither.",
                examples=["add github", "add github -f github-codes.txt",
                          "add github -n   (codes, then notes)",
                          "add questions -n   (notes only: Enter at the codes prompt)"])
    a.add_argument("service")
    a.add_argument("-f", "--from-file", metavar="FILE",
                   help="read the codes from FILE (one per line) instead of typing/pasting them")
    a.add_argument("-n", "--notes", action="store_true",
                   help="also add notes, typed or pasted with nothing shown on screen")
    command("list", "list services with unused counts", examples=["list"])
    for cmd, text, examples in (
            ("show", "show a service's codes (masked) and their state", ["show", "show github"]),
            ("unuse", "mark a used code as unused again", ["unuse", "unuse github"]),
            ("rm", "delete a service and all its codes", ["rm", "rm github"])):
        command(cmd, text, examples=examples).add_argument(
            "service", nargs="?", help="service name (leave it out to pick from a numbered list)")
    u = command("use", "reveal one unused code (or the notes), then wipe the screen",
                "Pick a code by number to reveal it; it is marked used once you confirm,\n"
                "then the screen is wiped. Pick n instead to read the service's notes\n"
                "(nothing is marked, the screen is wiped afterwards).",
                examples=["use", "use github   (then a number for a code, or n for the notes)"])
    u.add_argument("service", nargs="?",
                   help="service name (leave it out to pick from a numbered list)")
    r = command("rename", "rename a service (fixes a typo; codes and history are kept)",
                examples=["rename github GitHub", "rename github   (then type the new name)",
                          "rename   (pick from a list, then type the new name)"])
    r.add_argument("service", nargs="?", help="current name (leave it out to pick from a list)")
    r.add_argument("new_name", nargs="?", help="the new name (leave it out to be asked)")
    n = command("note", "replace or remove a service's notes",
                "Replace a service's notes. They are typed or pasted with nothing shown on\n"
                "screen, and the current notes are never displayed here (read them with\n"
                "'use', then n). --clear removes the notes instead.",
                examples=["note github", "note github --clear",
                          "note   (pick from a list first)"])
    n.add_argument("service", nargs="?", help="service name (leave it out to pick from a list)")
    n.add_argument("--clear", action="store_true", help="remove the notes instead of replacing them")
    command("passwd", "change the vault password", examples=["passwd"])

    args = p.parse_args()
    args.input = resolve_vault_path(args.input, creating=(args.command == "init"))
    handler = {"init": cmd_init, "add": cmd_add, "list": cmd_list, "show": cmd_show,
               "use": cmd_use, "unuse": cmd_unuse, "rm": cmd_rm, "rename": cmd_rename,
               "note": cmd_note, "passwd": cmd_passwd}
    handler[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)