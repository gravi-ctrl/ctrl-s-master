# dependencies:
# pip install cryptography argon2-cffi 

# usage:
# python json-decryptor.py encrypted.json --write decrypted.json

# Copyright © 2023 Thorsten Zirwes
# All rights reserved.
# Released under the "GNU General Public License v3.0". Please see the LICENSE.
# Script for decrypting password protected json files from exported bitwarden vaults.
# Based on the code by https://github.com/GurpreetKang/BitwardenDecrypt



import json, base64, sys, getpass
try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import ciphers, hashes, hmac, padding
    from cryptography.hazmat.primitives.ciphers import algorithms, Cipher, modes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
    import shutil
    from pathlib import Path
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ModuleNotFoundError:
    print("ERROR: package 'cryptography' required! (pip install cryptography)")
    sys.exit(1)

def get_keys(data, passphrase):
    if not (data.get("encrypted") and data.get("passwordProtected")):
        print("Input: not encrypted or account protected!")
        sys.exit(1)

    salt = data["salt"].encode("utf-8")

    if data["kdfType"] == 0:  # PBKDF2
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt,
            iterations=data["kdfIterations"], backend=default_backend()
        )
        key = kdf.derive(passphrase)

    elif data["kdfType"] == 1:  # Argon2id
        try:
            import argon2
        except ModuleNotFoundError:
            print("ERROR: package 'argon2-cffi' required! (pip install argon2-cffi)")
            sys.exit(1)

        digest = hashes.Hash(hashes.SHA256())
        digest.update(salt)
        salt_hash = digest.finalize()

        key = argon2.low_level.hash_secret_raw(
            passphrase, salt=salt_hash, time_cost=data["kdfIterations"],
            memory_cost=data["kdfMemory"] * 1024, parallelism=data["kdfParallelism"],
            hash_len=32, type=argon2.low_level.Type.ID
        )
    else:
        print("ERROR: unknown KDF!")
        sys.exit(1)

    enc_key = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=b"enc", backend=default_backend()).derive(key)
    mac_key = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=b"mac", backend=default_backend()).derive(key)
    return enc_key, mac_key

def decrypt(inp, enc_key, mac_key):
    parse = inp.split("|")
    if len(parse) != 3 or len(parse[0]) < 3 or parse[0][0:2] != "2.":
        print("ERROR: incorrect file format!")
        sys.exit(1)

    iv    = base64.b64decode(parse[0][2:], validate=True)
    vault = base64.b64decode(parse[1],     validate=True)
    mac   = base64.b64decode(parse[2],     validate=True)

    h = hmac.HMAC(mac_key, hashes.SHA256(), backend=default_backend())
    h.update(iv)
    h.update(vault)
    if mac != h.finalize():
        print("ERROR! MAC mismatch!")
        sys.exit(1)

    cipher    = Cipher(algorithms.AES(enc_key), modes.CBC(iv), backend=default_backend()).decryptor()
    decryptor = cipher.update(vault) + cipher.finalize()
    unpadder  = padding.PKCS7(128).unpadder()
    return (unpadder.update(decryptor) + unpadder.finalize()).decode('utf-8')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("ERROR: first argument must be the json file name!")
        sys.exit(1)

    input_filename = sys.argv[1]
    output_filename = "decrypted.json"

    with open(input_filename, 'r', encoding="utf-8") as f:
        data = json.load(f)

    password = getpass.getpass(prompt="Enter Password: ").encode("utf-8")
    enc_key, mac_key = get_keys(data, password)

    validation = decrypt(data["encKeyValidation_DO_NOT_EDIT"], enc_key, mac_key)
    print("Info: encKeyValidation_DO_NOT_EDIT:", validation)

    vault = decrypt(data["data"], enc_key, mac_key)

    try:
        vault_data = json.loads(vault)
    except json.JSONDecodeError:
        print("ERROR: Decrypted data is not valid JSON!")
        sys.exit(1)

    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(vault_data, f, indent=4, ensure_ascii=False)
    print("Info: decrypted vault written to", output_filename)
