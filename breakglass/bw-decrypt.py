#!/usr/bin/env python3
# Needs: cryptography (44.0 or newer, for Argon2id)
# Offline: python -m pip install --no-index --find-links wheels cryptography

# Based on BitwardenDecrypt by Gurpreet Kang.
# Copyright © 2020-2022 Gurpreet Kang
# All rights reserved.
#
# Released under the "GNU General Public License v3.0". Please see the LICENSE.
# https://github.com/GurpreetKang/BitwardenDecrypt
#
# This is a modified, reduced version of that script and stays under the same
# license (GPL-3.0). Keep this header intact. Changes compared to upstream:
#   - only password-protected Bitwarden exports are handled (no data.json files)
#   - Argon2id comes from the 'cryptography' package (no argon2-cffi needed)
#   - prints to the screen by default; status messages go to stderr, so piping works
#   - --output refuses to overwrite and creates the file with mode 0600
#   - KDF settings read from the file are range-checked; MACs compared in constant time
#
# Decrypts a password-protected Bitwarden export:
#   Bitwarden > Export vault > File format ".json (Encrypted)" > "Password protected".
# "Account restricted" exports cannot be decrypted here: they need the account itself.
#
# Usage: bw-decrypt.py export.json                    (prints to the screen)
#        bw-decrypt.py export.json --output out.json  (writes PLAINTEXT to disk!)

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import sys

try:
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives import hashes, padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    sys.exit("error: this script needs the 'cryptography' package, version 44.0 or newer\n"
             "       pip install -U cryptography")

MAX_PASSWORD_ATTEMPTS = 3


class WrongPassword(Exception):
    pass


class MacMismatch(Exception):
    pass


def die(msg):
    sys.exit(f"error: {msg}")


# ---------------------------------------------------------------- export ----

def load_export(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        die(f"cannot read {path}: {e.strerror}")
    except ValueError:  # bad JSON or bad encoding
        die(f"{path} is not a valid JSON file")
    if not isinstance(data, dict) or not (data.get("encrypted") and data.get("passwordProtected")):
        die("not a password-protected Bitwarden export (account-restricted exports "
            "can't be decrypted without the Bitwarden account)")
    return data


def kdf_settings(data):
    """Read and range-check the KDF settings, so a damaged file can't demand absurd memory."""
    try:
        salt = data["salt"]
        kdf_type = int(data.get("kdfType", 0))
        iterations = int(data["kdfIterations"])
        memory = int(data.get("kdfMemory", 0))            # MiB, Argon2id only
        parallelism = int(data.get("kdfParallelism", 0))  # Argon2id only
    except (KeyError, TypeError, ValueError):
        die("the export's KDF settings are missing or malformed (damaged file?)")
    if not isinstance(salt, str) or not salt:
        die("the export has no usable salt (damaged file?)")
    if kdf_type == 0:
        in_range = 1 <= iterations <= 10_000_000
    elif kdf_type == 1:
        in_range = 1 <= iterations <= 100 and 1 <= memory <= 2048 and 1 <= parallelism <= 64
    else:
        die(f"unsupported kdfType {kdf_type} (expected 0 = PBKDF2 or 1 = Argon2id)")
    if not in_range:
        die("the export's KDF settings are out of range (damaged file?)")
    return salt, kdf_type, iterations, memory, parallelism


# ---------------------------------------------------------------- crypto ----

def derive_keys(password, salt, kdf_type, iterations, memory, parallelism):
    """Password -> master key -> (encryption key, MAC key), the way Bitwarden does it."""
    salt = salt.encode("utf-8")
    if kdf_type == 1:
        # Bitwarden hashes the salt with SHA-256 first; memory is given in MiB, Argon2 wants KiB
        master = Argon2id(salt=hashlib.sha256(salt).digest(), length=32, iterations=iterations,
                          lanes=parallelism, memory_cost=memory * 1024).derive(password)
    else:
        master = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                            iterations=iterations).derive(password)

    def expand(info):
        return HKDFExpand(algorithm=hashes.SHA256(), length=32, info=info).derive(master)

    return expand(b"enc"), expand(b"mac")


def decrypt_cipherstring(cipherstring, enc_key, mac_key):
    """Decrypt '2.<iv>|<ciphertext>|<mac>' (base64 parts, AES-256-CBC + HMAC-SHA256) to bytes."""
    try:
        enc_type, rest = cipherstring.split(".", 1)
        iv, ciphertext, mac = (base64.b64decode(part) for part in rest.split("|"))
    except (AttributeError, ValueError):
        raise ValueError("malformed encrypted string")
    if enc_type != "2":
        raise ValueError(f"unsupported encryption type {enc_type}")

    expected = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise MacMismatch()

    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def decrypt_export(data, settings, password):
    """Return the decrypted vault (parsed JSON). Raises WrongPassword."""
    enc_key, mac_key = derive_keys(password, *settings)

    validated = False
    check = data.get("encKeyValidation_DO_NOT_EDIT")
    if check:  # a small encrypted value that only opens with the right password
        try:
            decrypt_cipherstring(check, enc_key, mac_key)
            validated = True
        except MacMismatch:
            raise WrongPassword()
        except ValueError:
            die("the export's password check value is malformed (damaged file?)")

    try:
        plain = decrypt_cipherstring(data.get("data"), enc_key, mac_key)
        return json.loads(plain.decode("utf-8"))
    except MacMismatch:
        if validated:
            die("the encrypted data failed its integrity check (damaged or modified file)")
        raise WrongPassword()
    except ValueError:
        die("the encrypted data is malformed (damaged file?)")


# ---------------------------------------------------------------- output ----

def emit(db, path):
    text = json.dumps(db, indent=2, ensure_ascii=False)
    if path is None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
        print(text)
        return

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        die(f"{path} already exists; refusing to overwrite it")
    except OSError as e:
        die(f"cannot write {path}: {e.strerror}")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"wrote PLAINTEXT to {path}. Delete it when you are done.", file=sys.stderr)


# ------------------------------------------------------------------ main ----

def main():
    p = argparse.ArgumentParser(
        description="Decrypt a password-protected Bitwarden JSON export.",
        epilog="Prints to the screen by default. --output writes the PLAINTEXT to disk: "
               "delete it afterwards (deleting is not secure erasure on an SSD).",
    )
    p.add_argument("inputfile", help="password-protected Bitwarden export (.json)")
    p.add_argument("--output", metavar="OUTPUTFILE",
                   help="write the decrypted JSON to this file instead (refuses to overwrite)")
    args = p.parse_args()

    if args.output and os.path.exists(args.output):
        die(f"{args.output} already exists; refusing to overwrite it")

    data = load_export(args.inputfile)
    settings = kdf_settings(data)

    db = None
    for attempt in range(MAX_PASSWORD_ATTEMPTS):
        password = getpass.getpass("Export password: ").encode("utf-8")
        try:
            db = decrypt_export(data, settings, password)
            break
        except WrongPassword:
            left = MAX_PASSWORD_ATTEMPTS - attempt - 1
            print("Wrong password." + (f" {left} attempt(s) left." if left else ""),
                  file=sys.stderr)
        except UnsupportedAlgorithm:
            die("this build of 'cryptography' lacks Argon2id; run: pip install -U cryptography")
    if db is None:
        die("too many wrong passwords")

    emit(db, args.output)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
        sys.exit(130)