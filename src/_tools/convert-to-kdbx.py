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

sys.path.append(str(Path(__file__).resolve().parent))
from common_utils import rotate_backups

DEFAULT_ARGON2_PARAMS = {'memory': 256, 'iterations': 4, 'parallelism': 4}

def load_master_env():
    root_path_str = os.getenv("AUTOMATION_ROOT")
    if root_path_str:
        root_path = Path(root_path_str)
    else:
        script_path = Path(__file__).resolve()
        root_path = script_path.parents[2]
    env_path = root_path / '.env'
    if env_path.is_file(): load_dotenv(dotenv_path=env_path)
    return root_path

def get_keys(data, passphrase):
    if not (data.get("encrypted") and data.get("passwordProtected")): sys.exit("Error: Not encrypted!")
    try:
        salt = data["salt"].encode("utf-8")
        if data["kdfType"] == 0:
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=data["kdfIterations"], backend=default_backend())
            key = kdf.derive(passphrase)
        elif data["kdfType"] == 1:
            import argon2
            digest = hashes.Hash(hashes.SHA256()); digest.update(salt); salt_hash = digest.finalize()
            key = argon2.low_level.hash_secret_raw(passphrase, salt=salt_hash, time_cost=data.get("kdfIterations", 4), memory_cost=data.get("kdfMemory", 256) * 1024, parallelism=data.get("kdfParallelism", 4), hash_len=32, type=argon2.low_level.Type.ID)
        else: raise ValueError("Unsupported KDF type")
        enc_key = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=b"enc", backend=default_backend()).derive(key)
        mac_key = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=b"mac", backend=default_backend()).derive(key)
        return enc_key, mac_key
    except Exception as e: sys.exit(f"Error: Key derivation failed: {str(e)}")

def decrypt(inp, enc_key, mac_key):
    try:
        parse = inp.split("|")
        if len(parse) != 3 or len(parse[0]) < 3 or parse[0][:2] != "2.": sys.exit("Error: Invalid format!")
        print("OK: Decrypting data...")
        iv = base64.b64decode(parse[0][2:])
        encrypted_data = base64.b64decode(parse[1])
        mac = base64.b64decode(parse[2])
        h = hmac.HMAC(mac_key, hashes.SHA256(), backend=default_backend()); h.update(iv + encrypted_data); h.verify(mac)
        cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv), backend=default_backend()).decryptor()
        decryptor = cipher.update(encrypted_data) + cipher.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        decrypted = (unpadder.update(decryptor) + unpadder.finalize()).decode('utf-8')
        return decrypted
    except Exception as e: sys.exit(f"Error decryption: {e}")

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
        try:
            parent_group_element = folder_map.get(item.get('folderId'), root_group_element)
            title, username, password, url, totp = item.get('name') or 'No Title', '', '', '', ''
            notes_parts = []
            extra_uris = []
            custom_fields = item.get('fields', [])

            # 1. Handle Types
            if item.get('type') == 1: # Login
                login = item.get('login', {})
                username = login.get('username') or ''
                password = login.get('password') or ''
                totp = login.get('totp')
                if login.get('uris'): 
                    url = login['uris'][0].get('uri', '')
                    # Capture extra URLs
                    if len(login['uris']) > 1:
                        extra_uris = [u.get('uri') for u in login['uris'][1:] if u.get('uri')]
            elif item.get('type') == 3: # Card
                notes_parts.append("--- Credit Card ---")
                for k,v in item.get('card', {}).items(): notes_parts.append(f"{k}: {v}")
            elif item.get('type') == 4: # Identity
                notes_parts.append("--- Identity ---")
                for k,v in item.get('identity', {}).items(): notes_parts.append(f"{k}: {v}")
            
            if item.get('notes'): notes_parts.append(item['notes'])
            final_notes = "\n\n".join(notes_parts)

            # 2. Build Entry XML
            entry_element = etree.Element('Entry')
            etree.SubElement(entry_element, 'UUID').text = base64.b64encode(uuid.uuid4().bytes).decode('utf-8')
            
            def create_string(key, val, prot=False):
                s = etree.Element('String'); etree.SubElement(s, 'Key').text = key
                v = etree.SubElement(s, 'Value'); v.text = val if val else ""; 
                if prot: v.set('Protected', 'True')
                return s
            
            # Standard Fields
            entry_element.append(create_string('Title', title))
            entry_element.append(create_string('UserName', username))
            entry_element.append(create_string('Password', password, True))
            entry_element.append(create_string('URL', url))
            entry_element.append(create_string('Notes', final_notes))
            
            # TOTP
            if totp: entry_element.append(create_string('otp', f"otpauth://totp/?secret={totp.replace(' ','')}", True))
            
            # Extra URLs
            for i, extra_url in enumerate(extra_uris, 1):
                entry_element.append(create_string(f'URL {i+1}', extra_url))

            # Custom Fields
            for field in custom_fields:
                f_name = field.get('name', 'Unknown')
                f_val = field.get('value')
                f_type = field.get('type', 0)
                # Type 0=Text, 1=Hidden, 2=Boolean
                is_protected = (f_type == 1)
                if f_type == 2: f_val = "True" if f_val else "False" # Handle booleans
                if f_val is not None:
                    entry_element.append(create_string(f_name, str(f_val), is_protected))

            parent_group_element.append(entry_element)
            imported += 1
        except Exception as e: skipped_items.append({'name': item.get('name'), 'error': str(e)})
    return {"source_items": len(bw_data.get('items', [])), "imported_items": imported, "skipped_items": len(skipped_items), "source_folders": len(bw_data.get('folders', [])), "created_groups": len(folder_map)}, skipped_items

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if len(sys.argv) < 2: sys.exit("ERROR: No file provided")
    project_root = load_master_env()
    input_full_path = Path(sys.argv[1])
    
    vaults_dir_override = os.getenv("DRY_RUN_VAULTS_DIR")
    kdbx_dir = (Path(vaults_dir_override) if vaults_dir_override else project_root / "vaults") / "kdbx"
    kdbx_dir.mkdir(exist_ok=True, parents=True)
    
    final_output_path = kdbx_dir / f"{input_full_path.stem}.kdbx"
    if final_output_path.exists():
        print(f"SKIP: '{final_output_path.name}' already exists.")
        sys.exit(0)

    temp_file_path = None
    try:
        print(f"Processing: {input_full_path.name}")
        with open(input_full_path, 'r', encoding="utf-8") as f: data = json.load(f)
        
        if 'personal' in input_full_path.name.lower():
            bw_pass = os.getenv("BITWARDEN_PERSONAL_PASSWORD")
            kdbx_pass = os.getenv("KDBX_PASSWORD_OVERRIDE") or os.getenv("KDBX_PERSONAL_PASSWORD")
            vault_type = "PERSONAL"
        elif 'work' in input_full_path.name.lower():
            bw_pass = os.getenv("BITWARDEN_WORK_PASSWORD")
            kdbx_pass = os.getenv("KDBX_PASSWORD_OVERRIDE") or os.getenv("KDBX_WORK_PASSWORD")
            vault_type = "WORK"
        else: sys.exit("FATAL: Unknown vault type")
        
        enc_key, mac_key = get_keys(data, bw_pass.encode("utf-8"))
        vault_data = json.loads(decrypt(data["data"], enc_key, mac_key))
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".kdbx") as temp_f: temp_file_path = temp_f.name
        print(f"Creating temp DB: {temp_file_path}")
        
        kp = create_database(filename=temp_file_path, password=kdbx_pass)
        metrics, skipped = import_bitwarden_data(kp, vault_data)
        print(f"Import Stats: {metrics}")
        kp.save()
        
        shutil.copy2(temp_file_path, final_output_path)
        os.remove(temp_file_path)
        temp_file_path = None
        
        if final_output_path.exists():
            print(f"✅ Created: {final_output_path.name}")
        
        base_filename = os.getenv(f"BW_{vault_type}_OUTPUT_FILENAME", "").split('.')[0]
        serials_to_keep = int(os.getenv("BW_SERIALS_TO_KEEP", 0) or 0)
        if base_filename:
            rotate_backups(kdbx_dir, f"*-{base_filename}_*.kdbx", serials_to_keep)

    except Exception as e:
        print(f"FATAL Error: {e}")
        sys.exit(1)
    finally:
        if temp_file_path and os.path.exists(temp_file_path): os.remove(temp_file_path)

if __name__ == "__main__":
    main()