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

sys.path.append(str(Path(__file__).resolve().parent))
from common_utils import rotate_backups

def load_master_env():
    root_path_str = os.getenv("AUTOMATION_ROOT")
    if root_path_str:
        root_path = Path(root_path_str)
    else:
        script_path = Path(__file__).resolve()
        root_path = script_path.parents[2]
    
    env_path = root_path / '.env'
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)
        print(f"[INFO] Loaded configuration from {env_path}")
    else:
        print(f"FATAL: Master .env file not found at expected path: {env_path}")
        sys.exit(1)

def get_config():
    required_vars = ["RAINDROP_BACKUP_DESTINATION", "RAINDROP_PERSONAL_API_TOKEN", "RAINDROP_WORK_API_TOKEN"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"FATAL: Missing env vars: {', '.join(missing_vars)}")
        sys.exit(1)
        
    backup_dest_override = os.getenv("DRY_RUN_BACKUP_DEST")
    if backup_dest_override:
        backup_dest = Path(backup_dest_override)
    else:
        backup_dest = Path(os.getenv("RAINDROP_BACKUP_DESTINATION"))
    
    backup_dest.mkdir(parents=True, exist_ok=True)

    config = {
        "backup_dest_dir": backup_dest,
        "backups_to_keep": int(os.getenv("BACKUPS_TO_KEEP", 7)),
        "accounts": [
            {'name': 'Personal', 'token': os.getenv('RAINDROP_PERSONAL_API_TOKEN'), 'folder': 'personal'},
            {'name': 'Work', 'token': os.getenv('RAINDROP_WORK_API_TOKEN'), 'folder': 'work'}
        ]
    }
    if not config["backup_dest_dir"].is_dir():
        print(f"FATAL: Backup path could not be created: {config['backup_dest_dir']}")
        sys.exit(1)
    return config

def fetch_collections_map(api_token):
    headers = {'Authorization': f'Bearer {api_token}'}
    base_url = 'https://api.raindrop.io/rest/v1/collections'
    collection_map = {}
    print("Fetching collections...")
    try:
        response = requests.get(base_url, headers=headers); response.raise_for_status()
        for c in response.json().get('items', []): collection_map[c['_id']] = c['title']
        response = requests.get(base_url + '/childrens', headers=headers); response.raise_for_status()
        for c in response.json().get('items', []): collection_map[c['_id']] = c['title']
        print(f"  - Mapped {len(collection_map)} collections.")
        return collection_map
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Could not fetch collections: {e}"); return None

def fetch_all_bookmarks(api_token):
    headers = {'Authorization': f'Bearer {api_token}'}
    api_url = 'https://api.raindrop.io/rest/v1/raindrops/0'
    all_bookmarks, page, total_api_count = [], 0, -1
    print("Fetching all bookmarks...")
    while True:
        try:
            response = requests.get(api_url, headers=headers, params={'page': page, 'perpage': 50}); response.raise_for_status()
            data = response.json()
            if page == 0 and 'count' in data:
                total_api_count = data['count']
                print(f"  - API reports a total of {total_api_count} bookmarks.")
            items = data.get('items', [])
            if not items: break
            all_bookmarks.extend(items)
            print(f"  - Fetched page {page} ({len(items)} items)..."); page += 1
            if not data.get('result', False) or len(items) < 50: break
        except requests.exceptions.RequestException as e:
            print(f"ERROR: API request failed: {e}"); return None, -1
    return all_bookmarks, total_api_count

def save_as_json(bookmarks, filepath: Path):
    try:
        with filepath.open('w', encoding='utf-8') as f: json.dump(bookmarks, f, indent=2, ensure_ascii=False)
        print(f"OK: Saved JSON.")
    except IOError as e: print(f"ERROR: Error saving JSON: {e}")

def save_as_csv(bookmarks, filepath: Path, collection_map):
    try:
        with filepath.open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Title', 'URL', 'Created Date', 'Tags', 'Collection', 'Description', 'Notes'])
            for item in bookmarks:
                writer.writerow([
                    item.get('title',''), item.get('link',''), 
                    datetime.fromisoformat(item['created'].replace('Z','+00:00')).strftime('%d-%m-%Y'), 
                    ', '.join(item.get('tags',[])), collection_map.get(item.get('collectionId',-1),'Unsorted'), 
                    item.get('excerpt',''), item.get('note','')
                ])
        print(f"OK: Saved CSV.")
    except IOError as e: print(f"ERROR: Error saving CSV: {e}")

def save_as_html(bookmarks, filepath: Path, account_name, collection_map):
    html_header = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Raindrop.io Backup</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.6;color:#333;max-width:800px;margin:2rem auto;padding:0 1rem}h1{border-bottom:2px solid #eee;padding-bottom:.5rem}ul{list-style:none;padding:0}li{margin-bottom:1.2rem;border:1px solid #ddd;padding:1rem;border-radius:5px;background-color:#f9f9f9}a{font-weight:700;color:#007acc;text-decoration:none;font-size:1.1em}a:hover{text-decoration:underline}.meta{font-size:.85em;color:#666;margin-top:.5rem}.tag{background-color:#e0e0e0;padding:2px 6px;border-radius:3px;margin-right:5px}.extra-info{background-color:#f0f0f0;padding:.75rem;margin-top:.75rem;border-radius:4px;font-size:.9em;border-left:3px solid #ccc}.extra-info p{margin:0 0 .5rem 0}.extra-info p:last-child{margin-bottom:0}</style></head><body>"""
    html_footer = "</body></html>"
    try:
        with filepath.open('w', encoding='utf-8') as f:
            f.write(html_header + f"<h1>Raindrop.io Backup ({html.escape(account_name)})</h1>\n" + f"<p class='meta'>Generated on: {datetime.now().strftime('%d-%m-%Y_%H-%M-%S')}</p>\n<ul>\n")
            for item in bookmarks:
                tags_html = ''.join(f"<span class='tag'>{html.escape(tag)}</span>" for tag in item.get('tags', []))
                f.write(f'<li><a href="{html.escape(item.get("link", "#"))}" target="_blank" rel="noopener noreferrer">{html.escape(item.get("title", "No Title"))}</a><div class="meta"><span><strong>Collection:</strong> {html.escape(collection_map.get(item.get("collectionId",-1),"Unsorted"))}</span> | <span><strong>Added:</strong> {datetime.fromisoformat(item["created"].replace("Z","+00:00")).strftime("%d-%m-%Y")}</span>')
                if tags_html: f.write(f'<br><strong>Tags:</strong> {tags_html}')
                f.write('</div>')
                excerpt, note = html.escape(item.get('excerpt', '')), html.escape(item.get('note', ''))
                if excerpt or note:
                    f.write('<div class="extra-info">')
                    if excerpt: f.write(f'<p><strong>Description:</strong> {excerpt}</p>')
                    if note: f.write(f'<p><strong>Note:</strong> {note.replace(chr(10), "<br>")}</p>')
                    f.write('</div>')
                f.write('</li>\n')
            f.write("</ul>\n" + html_footer)
        print(f"OK: Saved HTML.")
    except IOError as e: print(f"ERROR: Error saving HTML: {e}")

def process_account_backup(account_config, config):
    account_name, api_token, output_folder = account_config['name'], account_config['token'], account_config['folder']
    print(f"\n{'='*40}\nProcessing Account: {account_name}\n{'='*40}")
    if not api_token: 
        print(f"SKIP: Skipping '{account_name}': API Token is not set.")
        return True
    
    collection_map = fetch_collections_map(api_token)
    if collection_map is None: return False
    
    bookmarks, total_api_count = fetch_all_bookmarks(api_token)
    if bookmarks is None: return False
    
    print(f"  Fetched: {len(bookmarks)} (API reported: {total_api_count})")
    if not bookmarks:
        print(f"OK: Processed '{account_name}': No bookmarks found.")
        return True

    output_dir = config["backup_dest_dir"] / output_folder
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%d-%m-%Y_%H-%M-%S')
    base_filename = f"raindrop_backup_{timestamp}"
    json_file, csv_file, html_file = output_dir / f"{base_filename}.json", output_dir / f"{base_filename}.csv", output_dir / f"{base_filename}.html"
    zip_file_path = output_dir / f"{output_folder}_{timestamp}.zip"
    
    print(f"Saving backups to: {output_dir}")
    save_as_json(bookmarks, json_file)
    save_as_csv(bookmarks, csv_file, collection_map)
    save_as_html(bookmarks, html_file, account_name, collection_map)
    
    print(f"Creating ZIP: {zip_file_path.name}")
    try:
        with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in [json_file, csv_file, html_file]: zf.write(file, file.name)
        print(f"OK: Zip created.")
        for file in [json_file, csv_file, html_file]: file.unlink()
    except (IOError, zipfile.BadZipFile) as e:
        print(f"ERROR: Zip failed: {e}"); return False
        
    print("\n    --- Starting Cleanup ---")
    rotate_backups(directory=output_dir, glob_pattern="*.zip", max_to_keep=config["backups_to_keep"])
    return True

def main():
    print("Starting Raindrop.io backup...")
    load_master_env()
    config = get_config()
    failed_accounts = []
    for account in config["accounts"]: 
        if not process_account_backup(account, config):
            failed_accounts.append(account['name'])
    if failed_accounts:
        print(f"\nERROR: Failed accounts: {', '.join(failed_accounts)}")
        sys.exit(1)

if __name__ == '__main__':
    main()