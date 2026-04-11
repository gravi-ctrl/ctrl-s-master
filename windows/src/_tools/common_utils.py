import os
from pathlib import Path

def rotate_backups(directory: Path, glob_pattern: str, max_to_keep: int):
    """
    A generic function to find, sort by modification time, and delete old backups.

    Args:
        directory (Path): The directory to clean up.
        glob_pattern (str): The pattern to match backup files (e.g., "*.zip").
        max_to_keep (int): The number of newest backups to keep.
    """
    if max_to_keep <= 0:
        print("    Cleanup check: Rotation is disabled (max_to_keep <= 0).")
        return
    try:
        # Get all files matching the pattern and sort them by modification time, oldest first.
        existing_files = sorted(
            [f for f in directory.glob(glob_pattern) if f.is_file()],
            key=os.path.getmtime
        )
        
        if len(existing_files) > max_to_keep:
            print(f"    Cleaning up old backups. Found {len(existing_files)}, keeping the newest {max_to_keep}.")
            # The oldest files are at the beginning of the list
            files_to_delete = existing_files[:-max_to_keep]
            for file in files_to_delete:
                try:
                    file.unlink()
                    print(f"      - Deleted old backup: {file.name}")
                except OSError as e:
                    print(f"      - ERROR: Could not delete {file.name}: {e}")
        else:
            print("    Cleanup check: No old backups to delete.")

    except Exception as e:
        print(f"    [CLEANUP] WARN: An error occurred during backup rotation: {e}")