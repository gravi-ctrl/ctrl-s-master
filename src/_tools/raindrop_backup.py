#!/usr/bin/env python3
import os
import requests
import json
import csv
import html
import sys
import zipfile
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

# common_utils.py lives in the same directory as this script (src/_tools/)
sys.path.append(str(Path(__file__).resolve().parent))
from common_utils import rotate_backups

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
        load_dotenv(dotenv_path=env_path, override=False)
        print(f"[INFO] Loaded configuration from {env_path}")
    else:
        print(f"[INFO] No .env file on disk at {env_path}. Relying on existing environment variables.")

def get_config(account_type):
    if not os.getenv("RAINDROP_BACKUP_DESTINATION"):
        print("FATAL: RAINDROP_BACKUP_DESTINATION is not set in .env")
        sys.exit(1)

    token_var = f"RAINDROP_{account_type.upper()}_API_TOKEN"
    token = os.getenv(token_var)

    if not token:
        return None # Handled gracefully in main()

    backup_dest_override = os.getenv("DRY_RUN_BACKUP_DEST")
    if backup_dest_override:
        backup_dest = Path(backup_dest_override)
    else:
        backup_dest = Path(os.getenv("RAINDROP_BACKUP_DESTINATION"))

    backup_dest.mkdir(parents=True, exist_ok=True)

    config = {
        "backup_dest_dir": backup_dest,
        "backups_to_keep": int(os.getenv("BACKUPS_TO_KEEP", 7)),
        "account": {'name': account_type.capitalize(), 'token': token, 'folder': account_type.lower()}
    }

    if not config["backup_dest_dir"].is_dir():
        print(f"FATAL: Backup path could not be created or is not a directory: {config['backup_dest_dir']}")
        sys.exit(1)
        
    return config

def fetch_collections_map(api_token):
    headers = {'Authorization': f'Bearer {api_token}'}
    base_url = 'https://api.raindrop.io/rest/v1/collections'
    collection_map = {}
    print("Fetching collections to map IDs to names...")
    try:
        response = requests.get(base_url, headers=headers)
        response.raise_for_status()
        for c in response.json().get('items',[]):
            collection_map[c['_id']] = c['title']
        response = requests.get(base_url + '/childrens', headers=headers)
        response.raise_for_status()
        for c in response.json().get('items', []):
            collection_map[c['_id']] = c['title']
        print(f"  - Mapped {len(collection_map)} collections.")
        return collection_map
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Could not fetch collections: {e}")
        return None

def fetch_all_bookmarks(api_token):
    headers = {'Authorization': f'Bearer {api_token}'}
    api_url = 'https://api.raindrop.io/rest/v1/raindrops/0'
    all_bookmarks, page, total_api_count =[], 0, -1
    print("Fetching all bookmarks...")
    while True:
        try:
            response = requests.get(api_url, headers=headers, params={'page': page, 'perpage': 50})
            response.raise_for_status()
            data = response.json()
            if page == 0 and 'count' in data:
                total_api_count = data['count']
                print(f"  - API reports a total of {total_api_count} bookmarks.")
            items = data.get('items',[])
            if not items:
                break
            all_bookmarks.extend(items)
            print(f"  - Fetched page {page} ({len(items)} items)...")
            page += 1
            if not data.get('result', False) or len(items) < 50:
                break
        except requests.exceptions.RequestException as e:
            print(f"ERROR: An error occurred during API request: {e}")
            return None, -1
    return all_bookmarks, total_api_count

def save_as_json(bookmarks, filepath: Path):
    try:
        with filepath.open('w', encoding='utf-8') as f:
            json.dump(bookmarks, f, indent=2, ensure_ascii=False)
        print("OK: Successfully saved JSON file.")
    except IOError as e:
        print(f"ERROR: Error saving JSON file: {e}")

def save_as_csv(bookmarks, filepath: Path, collection_map):
    try:
        with filepath.open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Title', 'URL', 'Created Date', 'Tags', 'Collection', 'Description', 'Notes'])
            for item in bookmarks:
                writer.writerow([
                    item.get('title', ''), item.get('link', ''),
                    datetime.fromisoformat(item['created'].replace('Z', '+00:00')).strftime('%d-%m-%Y'),
                    ', '.join(item.get('tags',[])),
                    collection_map.get(item.get('collectionId', -1), 'Unsorted'),
                    item.get('excerpt', ''), item.get('note', '')
                ])
        print("OK: Successfully saved CSV file.")
    except IOError as e:
        print(f"ERROR: Error saving CSV file: {e}")

def save_as_html(bookmarks, filepath: Path, account_name, collection_map):
    html_header = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Raindrop.io Backup</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.6;color:#333;max-width:800px;margin:2rem auto;padding:0 1rem}h1{border-bottom:2px solid #eee;padding-bottom:.5rem}ul{list-style:none;padding:0}li{margin-bottom:1.2rem;border:1px solid #ddd;padding:1rem;border-radius:5px;background-color:#f9f9f9}a{font-weight:700;color:#007acc;text-decoration:none;font-size:1.1em}a:hover{text-decoration:underline}.meta{font-size:.85em;color:#666;margin-top:.5rem}.tag{background-color:#e0e0e0;padding:2px 6px;border-radius:3px;margin-right:5px}.extra-info{background-color:#f0f0f0;padding:.75rem;margin-top:.75rem;border-radius:4px;font-size:.9em;border-left:3px solid #ccc}.extra-info p{margin:0 0 .5rem 0}.extra-info p:last-child{margin-bottom:0}</style></head><body>"""
    html_footer = "</body></html>"
    try:
        with filepath.open('w', encoding='utf-8') as f:
            f.write(html_header)
            f.write(f"<h1>Raindrop.io Backup ({html.escape(account_name)})</h1>\n")
            f.write(f"<p class='meta'>Generated on: {datetime.now().strftime('%d-%m-%Y_%H-%M-%S')}</p>\n<ul>\n")
            for item in bookmarks:
                tags_html = ''.join(
                    f"<span class='tag'>{html.escape(tag)}</span>" for tag in item.get('tags',[])
                )
                f.write(
                    f'<li><a href="{html.escape(item.get("link", "#"))}" target="_blank" rel="noopener noreferrer">'
                    f'{html.escape(item.get("title", "No Title"))}</a>'
                    f'<div class="meta">'
                    f'<span><strong>Collection:</strong> {html.escape(collection_map.get(item.get("collectionId", -1), "Unsorted"))}</span> | '
                    f'<span><strong>Added:</strong> {datetime.fromisoformat(item["created"].replace("Z", "+00:00")).strftime("%d-%m-%Y")}</span>'
                )
                if tags_html:
                    f.write(f'<br><strong>Tags:</strong> {tags_html}')
                f.write('</div>')
                excerpt = html.escape(item.get('excerpt', ''))
                note    = html.escape(item.get('note', ''))
                if excerpt or note:
                    f.write('<div class="extra-info">')
                    if excerpt: f.write(f'<p><strong>Description:</strong> {excerpt}</p>')
                    if note:    f.write(f'<p><strong>Note:</strong> {note.replace(chr(10), "<br>")}</p>')
                    f.write('</div>')
                f.write('</li>\n')
            f.write("</ul>\n" + html_footer)
        print("OK: Successfully saved HTML file.")
    except IOError as e:
        print(f"ERROR: Error saving HTML file: {e}")

def process_account_backup(account_config, config):
    account_name = account_config['name']
    api_token    = account_config['token']
    output_folder = account_config['folder']

    collection_map = fetch_collections_map(api_token)
    if collection_map is None:
        print(f"FAIL: Halting backup for '{account_name}' due to collection fetch error.")
        return False

    bookmarks, total_api_count = fetch_all_bookmarks(api_token)
    if bookmarks is None:
        print(f"FAIL: Halting backup for '{account_name}' due to bookmark fetch error.")
        return False

    print("\n--- Data Integrity Audit ---")
    print(f"  Total bookmarks reported by API: {total_api_count}")
    print(f"  Total bookmarks downloaded:      {len(bookmarks)}")
    if total_api_count != -1 and len(bookmarks) != total_api_count:
        print("  [WARNING] Mismatch detected! The script may not have fetched all bookmarks.")
    else:
        print("  [OK] Downloaded count matches API count.")

    if not bookmarks:
        print(f"OK: Processed '{account_name}': No bookmarks found.")
        return True

    output_dir = config["backup_dest_dir"] / output_folder
    output_dir.mkdir(exist_ok=True)
    timestamp     = datetime.now().strftime('%d-%m-%Y_%H-%M-%S')
    base_filename = f"raindrop_backup_{timestamp}"
    json_file  = output_dir / f"{base_filename}.json"
    csv_file   = output_dir / f"{base_filename}.csv"
    html_file  = output_dir / f"{base_filename}.html"
    zip_file_path = output_dir / f"{output_folder}_{timestamp}.zip"
    files_to_zip =[json_file, csv_file, html_file]

    print(f"\nFetched {len(bookmarks)} bookmarks.\nSaving backups to: {output_dir}\n")
    save_as_json(bookmarks, json_file)
    save_as_csv(bookmarks, csv_file, collection_map)
    save_as_html(bookmarks, html_file, account_name, collection_map)

    print(f"\nCreating ZIP archive: {zip_file_path.name}")
    try:
        with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in files_to_zip:
                zf.write(file, file.name)
        print("OK: Successfully created ZIP archive.")
        print("Cleaning up individual backup files...")
        for file in files_to_zip:
            file.unlink()
    except (IOError, zipfile.BadZipFile) as e:
        print(f"ERROR: Could not create or write to ZIP file: {e}")
        return False

    print("\n    --- Starting Cleanup ---")
    rotate_backups(directory=output_dir, glob_pattern="*.zip", max_to_keep=config["backups_to_keep"])
    return True

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ['personal', 'work']:
        print("FATAL: You must provide 'personal' or 'work' as an argument.")
        sys.exit(1)

    account_type = sys.argv[1]
    print(f"--- Starting Raindrop.io backup for: {account_type.capitalize()} ---")
    
    load_master_env()
    config = get_config(account_type)
    
    if not config:
        print(f"SKIP: Raindrop {account_type.capitalize()} is not configured. Skipping.")
        sys.exit(0)

    if not process_account_backup(config["account"], config):
        sys.exit(1)
        
    print(f"--- Finished Raindrop.io backup for: {account_type.capitalize()} ---")

if __name__ == '__main__':
    main()