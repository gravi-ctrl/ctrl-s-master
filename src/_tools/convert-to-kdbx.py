#!/usr/bin/env python3
import json
import base64
import sys
import os
import io
import shutil
import tempfile
import uuid
from datetime import datetime, UTC
from dateutil.parser import parse as date_parse
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import ciphers, hashes, hmac, padding
from cryptography.hazmat.primitives.ciphers import algorithms, Cipher, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pykeepass import create_database
from pathlib import Path
from lxml import etree
from dotenv import load_dotenv

# common_utils.py lives in the same directory as this script (src/_tools/)
sys.path.append(str(Path(__file__).resolve().parent))
from common_utils import rotate_backups

DEFAULT_ARGON2_PARAMS = {'memory': 256, 'iterations': 4, 'parallelism': 4}

def load_master_env():
    root_path_str = os.getenv("AUTOMATION_ROOT")
    if root_path_str:
        root_path = Path(root_path_str)
    else:
        print("WARN: AUTOMATION_ROOT env var not set. Guessing path...")
        script_path = Path(__file__).resolve()
        root_path = script_path.parents[2]

    env_path = root_path / '.env'
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)
    else:
        print(f"WARN: Master .env file not found at {env_path}. Relying on existing environment variables.")
    return root_path

def get_keys(data, passphrase):
    if not (data.get("encrypted") and data.get("passwordProtected")):
        sys.exit("Error: Input is not encrypted or password protected!")
    try:
        salt = data["salt"].encode("utf-8")
        if data["kdfType"] == 0:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(), length=32, salt=salt,
                iterations=data["kdfIterations"], backend=default_backend()
            )
            key = kdf.derive(passphrase)
        elif data["kdfType"] == 1:
            try:
                import argon2
            except ImportError:
                sys.exit("Error: argon2-cffi is required for this vault. Run: pip install argon2-cffi")
            digest = hashes.Hash(hashes.SHA256())
            digest.update(salt)
            salt_hash = digest.finalize()
            key = argon2.low_level.hash_secret_raw(
                passphrase, salt=salt_hash,
                time_cost=data.get("kdfIterations", DEFAULT_ARGON2_PARAMS['iterations']),
                memory_cost=data.get("kdfMemory", DEFAULT_ARGON2_PARAMS['memory']) * 1024,
                parallelism=data.get("kdfParallelism", DEFAULT_ARGON2_PARAMS['parallelism']),
                hash_len=32, type=argon2.low_level.Type.ID
            )
        else:
            raise ValueError("Unsupported KDF type")

        enc_key = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=b"enc", backend=default_backend()).derive(key)
        mac_key = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=b"mac", backend=default_backend()).derive(key)
        return enc_key, mac_key
    except Exception as e:
        sys.exit(f"Error: Key derivation failed: {str(e)}")

def decrypt(inp, enc_key, mac_key):
    try:
        parse = inp.split("|")
        if len(parse) != 3 or len(parse[0]) < 3 or parse[0][:2] != "2.":
            sys.exit("Error: Invalid encrypted data format!")
        print("OK: Decrypting data...")
        iv = base64.b64decode(parse[0][2:])
        encrypted_data = base64.b64decode(parse[1])
        mac = base64.b64decode(parse[2])
        h = hmac.HMAC(mac_key, hashes.SHA256(), backend=default_backend())
        h.update(iv + encrypted_data)
        h.verify(mac)
        cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv), backend=default_backend()).decryptor()
        decryptor = cipher.update(encrypted_data) + cipher.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        decrypted = (unpadder.update(decryptor) + unpadder.finalize()).decode('utf-8')
        print("OK: Decryption successful.")
        return decrypted
    except Exception as e:
        sys.exit(f"Error during decryption: {e}. Check your password or the file for corruption.")

def import_bitwarden_data(kp, bw_data):
    root_group_element = kp.root_group._element
    folder_map = {}
    for folder in bw_data.get('folders', []):
        group_element = etree.Element('Group')
        etree.SubElement(group_element, 'UUID').text = base64.b64encode(uuid.uuid4().bytes).decode('utf-8')
        etree.SubElement(group_element, 'Name').text = folder.get('name', 'Unnamed Folder')
        root_group_element.append(group_element)
        folder_map[folder['id']] = group_element

    imported = 0
    skipped_items = []

    for item in bw_data.get('items', []):
        item_name_for_error = item.get('name', 'Unknown Item')
        try:
            parent_group_element = folder_map.get(item.get('folderId'), root_group_element)
            title = item.get('name') or 'No Title'
            username, password, url, totp = '', '', '', ''
            original_notes = item.get('notes') or ''
            password_history, notes_parts, extra_uris = [], [], []

            # Handle item types
            if item.get('type') == 1:  # Login
                login = item.get('login', {})
                username = login.get('username') or ''
                password = login.get('password') or ''
                totp = login.get('totp')
                password_history = login.get('passwordHistory') or []
                if login.get('uris'):
                    url = login['uris'][0].get('uri', '')
                    if len(login['uris']) > 1:
                        extra_uris = [u.get('uri') for u in login['uris'][1:] if u.get('uri')]
            elif item.get('type') == 3:  # Card
                notes_parts.append("--- Credit Card Details ---\n" + "\n".join(
                    f"{k}: {v}" for k, v in item.get('card', {}).items()
                ))
            elif item.get('type') == 4:  # Identity
                notes_parts.append("--- Identity Details ---\n" + "\n".join(
                    f"{k}: {v}" for k, v in item.get('identity', {}).items()
                ))

            if original_notes:
                notes_parts.append(original_notes)
            final_notes = "\n\n".join(notes_parts)

            # Build Entry XML
            entry_element = etree.Element('Entry')
            etree.SubElement(entry_element, 'UUID').text = base64.b64encode(uuid.uuid4().bytes).decode('utf-8')

            def create_string_element(key, value, protected=False):
                string_el = etree.Element('String')
                etree.SubElement(string_el, 'Key').text = key
                value_el = etree.SubElement(string_el, 'Value')
                if value is not None:
                    value_el.text = value
                if protected:
                    value_el.set('Protected', 'True')
                return string_el

            # Standard fields
            entry_element.append(create_string_element('Title', title))
            entry_element.append(create_string_element('UserName', username))
            entry_element.append(create_string_element('Password', password, protected=True))
            entry_element.append(create_string_element('URL', url))
            if final_notes:
                entry_element.append(create_string_element('Notes', final_notes))

            # TOTP
            if totp:
                entry_element.append(create_string_element(
                    'otp', f"otpauth://totp/Default?secret={totp.replace(' ', '')}", protected=True
                ))

            # Timestamps
            times_element = etree.Element('Times')
            now_iso = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
            if item.get('revisionDate'):
                try:
                    etree.SubElement(times_element, 'LastModificationTime').text = \
                        date_parse(item['revisionDate']).strftime('%Y-%m-%dT%H:%M:%SZ')
                except Exception:
                    pass
            etree.SubElement(times_element, 'CreationTime').text = \
                date_parse(item['creationDate']).strftime('%Y-%m-%dT%H:%M:%SZ') \
                if item.get('creationDate') else now_iso
            etree.SubElement(times_element, 'Expires').text = 'False'
            entry_element.append(times_element)

            # Extra URLs
            for i, uri in enumerate(extra_uris, start=1):
                entry_element.append(create_string_element(f"URL {i}", uri))

            # Custom fields
            for field in item.get('fields', []):
                name, value = field.get('name'), field.get('value')
                if name and value is not None:
                    is_protected = field.get('type') == 1
                    entry_element.append(create_string_element(name, str(value), protected=is_protected))

            # Creation date as a readable custom field
            if item.get('creationDate'):
                entry_element.append(create_string_element("CreationDate", item['creationDate']))

            # Password history
            if password_history:
                history_element = etree.Element('History')
                for hist_item in password_history:
                    hist_entry_element = etree.Element('Entry')
                    hist_entry_element.append(create_string_element('Title', title))
                    hist_entry_element.append(create_string_element('UserName', username))
                    hist_entry_element.append(create_string_element(
                        'Password', hist_item.get('password'), protected=True
                    ))
                    hist_entry_element.append(create_string_element('URL', url))
                    hist_times = etree.Element('Times')
                    if hist_item.get('lastUsedDate'):
                        try:
                            mod_time = date_parse(hist_item['lastUsedDate']).strftime('%Y-%m-%dT%H:%M:%SZ')
                            etree.SubElement(hist_times, 'LastModificationTime').text = mod_time
                        except Exception:
                            pass
                    hist_entry_element.append(hist_times)
                    history_element.append(hist_entry_element)
                entry_element.append(history_element)

            parent_group_element.append(entry_element)
            imported += 1

        except Exception as e:
            skipped_items.append({'name': item_name_for_error, 'error': str(e)})

    metrics = {
        "source_items": len(bw_data.get('items', [])),
        "imported_items": imported,
        "skipped_items": len(skipped_items),
        "source_folders": len(bw_data.get('folders', [])),
        "created_groups": len(folder_map)
    }
    return metrics, skipped_items

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    if len(sys.argv) < 2:
        sys.exit("ERROR: First argument must be the full path to the JSON file.")

    project_root = load_master_env()
    input_full_path = Path(sys.argv[1])

    vaults_dir_override = os.getenv("DRY_RUN_VAULTS_DIR")
    kdbx_dir = (Path(vaults_dir_override) if vaults_dir_override else project_root / "vaults") / "kdbx"
    kdbx_dir.mkdir(exist_ok=True, parents=True)

    try:
        serials_to_keep = int(os.getenv("BW_SERIALS_TO_KEEP", 0))
    except (ValueError, TypeError):
        serials_to_keep = 0

    final_output_path = kdbx_dir / f"{input_full_path.stem}.kdbx"
    if final_output_path.exists():
        print(f"OK: Skipping '{input_full_path.name}' because '{final_output_path.name}' already exists.")
        sys.exit(0)

    temp_file_path = None
    try:
        with open(input_full_path, 'r', encoding="utf-8") as f:
            data = json.load(f)

        vault_type = None
        if 'personal' in input_full_path.name.lower():
            vault_type = 'personal'
            bw_pass = os.getenv("BITWARDEN_PERSONAL_PASSWORD")
            kdbx_pass = os.getenv("KDBX_PASSWORD_OVERRIDE") or os.getenv("KDBX_PERSONAL_PASSWORD")
        elif 'work' in input_full_path.name.lower():
            vault_type = 'work'
            bw_pass = os.getenv("BITWARDEN_WORK_PASSWORD")
            kdbx_pass = os.getenv("KDBX_PASSWORD_OVERRIDE") or os.getenv("KDBX_WORK_PASSWORD")
        else:
            sys.exit(f"FATAL: Could not determine vault type from filename: {input_full_path.name}")

        if not bw_pass or not kdbx_pass:
            sys.exit("FATAL: Required password environment variables not found.")
        print("OK: Passwords loaded from environment.")

        enc_key, mac_key = get_keys(data, bw_pass.encode("utf-8"))
        vault_data = json.loads(decrypt(data["data"], enc_key, mac_key))

        with tempfile.NamedTemporaryFile(delete=False, suffix=".kdbx") as temp_f:
            temp_file_path = temp_f.name
        print(f"Creating new database at temporary location: {temp_file_path}")

        kp = create_database(filename=temp_file_path, password=kdbx_pass)
        metrics, skipped_items = import_bitwarden_data(kp, vault_data)
        print("OK: Saving populated data...")
        kp.save()

        print(f"OK: Moving to final destination: {final_output_path}")
        shutil.move(temp_file_path, final_output_path)
        temp_file_path = None
        print(f"OK: Successfully created '{final_output_path.name}'")

        print("\n--- Data Integrity Audit ---")
        print(f"  Source JSON Items:      {metrics['source_items']}")
        print(f"  Imported KDBX Entries:  {metrics['imported_items']}")
        print(f"  Skipped Items:          {metrics['skipped_items']}")
        print(f"  ---------------------------")
        print(f"  Source JSON Folders:    {metrics['source_folders']}")
        print(f"  Created KDBX Groups:    {metrics['created_groups']}")
        if metrics['source_items'] != metrics['imported_items']:
            print("  [WARNING] Item count mismatch! Some items were skipped.")
        else:
            print("  [OK] Item and folder counts match source.")

        if skipped_items:
            print("--- !!! DETAILS ON SKIPPED ITEMS !!! ---")
            for item in skipped_items:
                print(f"  - Item Name: {item['name']}\n    Error: {item['error']}\n")

        if vault_type:
            print("\n    --- Starting Cleanup ---")
            base_filename = os.getenv(f"BW_{vault_type.upper()}_OUTPUT_FILENAME", "").split('.')[0]
            if base_filename:
                glob_pattern = f"*-{base_filename}_*.kdbx"
                rotate_backups(kdbx_dir, glob_pattern, serials_to_keep)

    except Exception as e:
        print(f"FATAL Error during processing: {str(e)}", file=sys.stderr)
        sys.exit(1)
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

if __name__ == "__main__":
    main()
