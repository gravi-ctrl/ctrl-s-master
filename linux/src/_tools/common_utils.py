#!/usr/bin/env python3
import os
from pathlib import Path

def rotate_backups(directory: Path, glob_pattern: str, max_to_keep: int):
    if max_to_keep <= 0: return
    try:
        existing_files = sorted(
            [f for f in directory.glob(glob_pattern) if f.is_file()],
            key=os.path.getmtime
        )
        if len(existing_files) > max_to_keep:
            for file in existing_files[:-max_to_keep]:
                try:
                    file.unlink()
                    print(f"      - Deleted old backup: {file.name}")
                except OSError as e:
                    print(f"      - ERROR deleting {file.name}: {e}")
    except Exception as e:
        print(f"    [CLEANUP] WARN: {e}")