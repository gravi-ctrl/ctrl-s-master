# breakglass

Emergency tools for when ctrl-s-master, or the machine it runs on, isn't available.

Everything in this folder is **standalone**: plain Python 3 plus the `cryptography`
package (and `argon2-cffi` for one script). None of it imports anything from `src/`, reads `.env`, or needs the
automation to be set up. Copy this folder to a USB stick or into your encrypted
container and it works anywhere Python does.

```
pip install -U cryptography     # 44.0 or newer (Argon2id, used by the recovery tools)
pip install argon2-cffi         # only for bw-decrypt.py, and only for Argon2id exports
```

## What's here

| Script | What it's for | Reads |
|---|---|---|
| `aegis-code.py` | Show current 2FA codes (TOTP, Steam, HOTP). Read-only, nothing is written to disk. | An encrypted Aegis backup, or a folder of them |
| `aegis-decrypt.py` | Decrypt an Aegis backup to readable JSON. | An encrypted Aegis backup |
| `bw-decrypt.py` | Decrypt a password-protected Bitwarden export to readable JSON. | A password-protected Bitwarden `.json` export (not an account-protected one) |
| `recovery-codes.py` | Manage 2FA recovery codes in one encrypted file. Marks codes used, wipes the screen after `use`. | A recovery vault (`.rcv`) |
| `recovery-decrypt.py` | Last-resort decryptor for a recovery vault, in case `recovery-codes.py` is broken or lost. | A recovery vault (`.rcv`) |

Every script has `-h`, except `bw-decrypt.py`.

## Quick recipes

**I need a 2FA code and my phone is gone**
```
python aegis-code.py -i <backup file or folder> -s github
python aegis-code.py -i <backup file or folder>            # no -s: list all entries, no codes
```
If a service rejects the code, check this machine's clock first. TOTP is time-based,
so a wrong system time gives valid-looking codes that never work.

**I need one recovery code**
```
python recovery-codes.py -i <vault.rcv> list               # what's left, per service
python recovery-codes.py -i <vault.rcv> use <service>      # pick one, reveal it, mark it used
```
Put `-i` before the command. After `use` shows the code you must type `yes` to mark it
used, then the screen is wiped. Marked the wrong one? `unuse <service>` undoes it.

**I need a password and only have a Bitwarden export**
```
python bw-decrypt.py <export.json> --write <output.json>
```
Unlike the other decryptors, this one never prints to the screen: it **always writes
plaintext to disk**. Without `--write` it creates `decrypted.json` in the current
folder, and it overwrites a file of that name without asking. Run it from a throwaway
folder and delete the result afterwards.

**I made a typo in a service name or note**
```
python recovery-codes.py -i <vault.rcv> rename <old name> <new name>
python recovery-codes.py -i <vault.rcv> note <service>             # typed/pasted hidden, replaces the notes
python recovery-codes.py -i <vault.rcv> note <service> --clear
```
Codes and their used/unused history are kept. Leave the service out to pick from a list.

**I need to read everything in a vault** (the full contents print to the screen)
```
python aegis-decrypt.py   --input <aegis backup>
python recovery-decrypt.py --input <vault.rcv>
```
`--output <file>` writes the plaintext to disk. Avoid it unless you have to, and
delete the result afterwards. Deleting isn't secure erasure on an SSD.

## The recovery vault

`recovery-codes.py` keeps recovery codes in one file, Argon2id + AES-256-GCM, plaintext
only in memory. The exact file format is documented in the docstring at the top of
`recovery-codes.py`, so a decryptor can be rebuilt from that alone.

- `init` adds the `.rcv` extension if you give a bare name (`-i codes` creates `codes.rcv`),
  and later commands find `codes.rcv` when you type `-i codes`. The `.gitignore` rules for
  vaults match on that extension.
- Notes are secrets too. They are typed or pasted with nothing shown, never appear on the
  command line, and are only displayed when you pick `n` in `use`. A service can be notes
  only (a security question and answer): `add <service> -n`, then press Enter at the codes prompt.
- A service can be typed as part of its name (`use steam`). If that matches several, you pick from
  a numbered list of just those, after the single password prompt.
- Each change makes a backup first, in `<vault>.rcv.backups/`, named `recovery-backup-<date>-<time>.rcv`.
  The 15 newest are kept.
- Backups open with the password the vault had when they were made. After `passwd`,
  the tool offers to delete the old ones.
- There is no recovery if you forget the vault password. It is on the paper sheet.
- If the vault changes while a command is running (a second copy of the tool, a sync),
  the save is refused and nothing is lost. Just run the command again.

## Rules for this folder

- **Vaults, backups and decrypted output never live here.** `.gitignore` only lets the
  `.py` files and this README into git, but keep the vaults elsewhere anyway.
- A script belongs here only if it needs nothing from the project: standard library
  plus `cryptography` (`argon2-cffi` for `bw-decrypt.py`), no imports from `src/`, no `.env`.
- Prefer read-only. If a tool has to write, it says so at the top of the file.
- Changed a script? Test it against a mock vault before trusting it with real data.

## Third-party code

`bw-decrypt.py` is by Thorsten Zirwes (2023), released under GPL-3.0 and based on
BitwardenDecrypt by GurpreetKang. Keep the header at the top of the file intact.