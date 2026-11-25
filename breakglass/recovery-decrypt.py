#!/usr/bin/env python3

# One-way decryptor for recovery-codes vaults (format "RCVT", version 1).
# Standalone on purpose: it shares no code with recovery-codes.py, so it still
# works if that script is broken or lost. THIS HEADER IS THE FORMAT SPECIFICATION
# (keep a copy of it on your paper sheet: it is all you need to write a decryptor
# in any language if every script is lost).
#
# this depends on the 'cryptography' package (version 44.0 or newer, for Argon2id)
# pip install -U cryptography
#
# example usage: recovery-decrypt.py --input codes.rcv                   (prints to screen)
#                recovery-decrypt.py --input codes.rcv --output out.json (writes plaintext to disk!)
#
# ---------------------------------------------------------------------------
# FILE FORMAT (all integers big-endian)
#
#     offset  size  field
#     0       4     magic            b"RCVT"
#     4       1     format version   1
#     5       1     KDF id           1 = Argon2id
#     6       4     memory, KiB
#     10      1     iterations (t, the Argon2 time cost)
#     11      1     lanes (p, the Argon2 parallelism)
#     12      16    salt
#     28      12    nonce
#     40      ...   AES-256-GCM ciphertext followed by its 16-byte tag
#
#     key        = Argon2id(password, salt, output length 32 bytes, time cost t,
#                  parallelism p, memory m KiB). Argon2 version 0x13 (19, the
#                  current one), no secret key, no associated data.
#                  password = Unicode NFKC-normalized, then encoded as UTF-8
#                  (a no-op for plain ASCII passwords)
#     plaintext  = AES-256-GCM decrypt with that key and the nonce from the header;
#                  associated data (AAD) = the 40 header bytes
#     payload    = 4-byte length N, then N bytes of UTF-8 JSON, then zero padding
#                  up to a multiple of 4096 bytes
#     JSON       = {"v": 1, "services": {NAME: {"notes", "regenerated_at",
#                  "codes": [{"value", "used", "used_at"}]}}}
# ---------------------------------------------------------------------------

import argparse
import getpass
import json
import os
import struct
import sys
import unicodedata

try:
    from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
except ImportError:
    sys.exit("error: needs the 'cryptography' package, version 44.0 or newer "
             "(pip install -U cryptography)")

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

    try:
        with open(args.input, "rb") as f:
            blob = f.read()
    except OSError as e:
        die(f"cannot read {args.input}: {e.strerror}")

    if len(blob) < HEADER.size + 16:
        die("file is too short to be a vault")
    magic, version, kdf, memory, iterations, lanes, salt, nonce = HEADER.unpack_from(blob)
    if magic != b"RCVT" or version != 1 or kdf != 1:
        die("not a supported vault (expected RCVT, version 1, Argon2id)")
    if not (1 <= lanes <= 16 and 1 <= iterations <= 10 and 8 * lanes <= memory <= 1024 * 1024):
        die("out-of-range KDF settings in the header (damaged file?)")

    password = unicodedata.normalize("NFKC", getpass.getpass()).encode("utf-8")
    try:
        key = Argon2id(salt=salt, length=32, iterations=iterations,
                       lanes=lanes, memory_cost=memory).derive(password)
    except UnsupportedAlgorithm:
        die("this build of 'cryptography' has no Argon2id support "
            "(the pip wheel does: pip install -U cryptography)")

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