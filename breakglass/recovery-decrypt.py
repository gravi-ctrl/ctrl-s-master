#!/usr/bin/env python3

# One-way decryptor for recovery-codes vaults (format "RCVT", version 1).
# Standalone on purpose: it shares no code with recovery-codes.py, so it still
# works if that script is broken or lost. The format is also described in the
# docstring of recovery-codes.py.
#
# this depends on the 'cryptography' package (version 44.0 or newer, for Argon2id)
# pip install -U cryptography
#
# example usage: recovery-decrypt.py --input codes.rcv                   (prints to screen)
#                recovery-decrypt.py --input codes.rcv --output out.json (writes plaintext to disk!)

import argparse
import getpass
import json
import os
import struct
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

# magic, version, kdf id, memory (KiB), iterations, lanes, salt, nonce
HEADER = struct.Struct(">4sBBIBB16s12s")


def die(msg):
    sys.exit(f"error: {msg}")


def main():
    parser = argparse.ArgumentParser(description="Decrypt a recovery-codes vault")
    parser.add_argument("--input", required=True, help="encrypted vault file")
    parser.add_argument("--output", default="-",
                        help="output file ('-' for the screen). Refuses to overwrite.")
    args = parser.parse_args()

    with open(args.input, "rb") as f:
        blob = f.read()

    if len(blob) < HEADER.size + 16:
        die("file is too short to be a vault")
    magic, version, kdf, memory, iterations, lanes, salt, nonce = HEADER.unpack_from(blob)
    if magic != b"RCVT" or version != 1 or kdf != 1:
        die("not a supported vault (expected RCVT, version 1, Argon2id)")
    if not (1 <= lanes <= 16 and 1 <= iterations <= 10 and 8 * lanes <= memory <= 1024 * 1024):
        die("out-of-range KDF settings in the header (damaged file?)")

    password = getpass.getpass().encode("utf-8")
    key = Argon2id(salt=salt, length=32, iterations=iterations,
                   lanes=lanes, memory_cost=memory).derive(password)

    try:
        # the 40 header bytes are the associated data
        body = AESGCM(key).decrypt(nonce, blob[HEADER.size:], blob[:HEADER.size])
    except InvalidTag:
        die("wrong password or damaged file")

    # payload = 4-byte length, then that many bytes of JSON, then zero padding
    (length,) = struct.unpack(">I", body[:4])
    db = json.loads(body[4:4 + length].decode("utf-8"))
    text = json.dumps(db, indent=2, ensure_ascii=False)

    if args.output == "-":
        print(text)
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(args.output, flags, 0o600)
        except FileExistsError:
            die(f"{args.output} already exists; refusing to overwrite it")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
