# VLViewer Content Publisher

This utility validates an existing game-version folder and publishes it to
Cloudflare R2. It provides both a Tkinter GUI and a command-line interface.

## Current source layout

The first implementation consumes the existing website layout and maps it to
the CDN layout without making another multi-gigabyte copy:

| Local path | Published path |
| --- | --- |
| `all_conversations.json` | `conversations.json` |
| `all_voicelines.json` | `voicelines.json` |
| `coverage.json` | `coverage.json` |
| `Audio/` | `audio/` |
| `Localization/` | `localization/` |
| `FanLocalization/` | `fan-localization/` |
| `IconPacks/default/` | `icons/default/` |

Other icon packs, event audio, website configuration, and redirects are not
part of the initial runtime-versioned content scope.

## Safety and caching rules

- JSON is mutable and can be corrected under an existing version ID.
- Binary content is immutable at a published object path.
- New binary files can be added to an existing version.
- A binary whose bytes differ from an existing object is reported as a conflict
  and blocks publication.
- Remote binary files missing from the local source are retained.
- Remote-only JSON is reported but is not deleted automatically.
- The publisher uploads content first and updates the game manifest last.
- Changed JSON URLs are purged individually when cache-purge credentials are
  configured.
- A version can be marked `hidden` so normal website version selectors omit it.
  Hidden versions are still addressable through an explicit `?version=` URL;
  this flag is not authentication or access control.
- The manifest's `versions` array is the display order. The GUI's version
  manager can move entries, toggle visibility, and make any existing visible
  version latest without moving or re-uploading its content.
- Making a version latest through the manager automatically unhides it and moves
  it to the first display position.

## Setup

On Windows, launch with `ContentPublisher/run_publisher_gui.bat`. The launcher
checks for the publisher's R2 dependencies and installs missing packages from
`ContentPublisher/requirements.txt` before opening the GUI:

```powershell
ContentPublisher\run_publisher_gui.bat
```

To install or repair them manually:

```powershell
python -m pip install -r ContentPublisher/requirements.txt
```

Launching `publisher_gui.py` directly still works. If its R2 dependencies are
missing, use the GUI's **Install/repair requirements** button.

```powershell
python ContentPublisher/publisher_gui.py
```

The GUI stores paths and non-secret settings in `ContentPublisher/config.json`.
Credentials are never written there. By default, credentials remain in the
running process only. On Windows, selecting **Remember credentials securely for
this Windows user** and then **Save settings** writes them to
`ContentPublisher/credentials.dpapi`, encrypted with Windows DPAPI for the
current Windows user. The encrypted file is gitignored and is not portable to a
different Windows user or computer. **Forget saved credentials** deletes it and
clears the current credential fields.

Required R2 credentials:

- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

For targeted CDN purging, also provide:

- a Cloudflare Zone ID in the GUI; and
- `CLOUDFLARE_API_TOKEN` with cache-purge permission.

These values can be placed in environment variables before launching, or typed
into the GUI's session-only credential fields.

## Command line

Local validation does not require Cloudflare credentials:

```powershell
python ContentPublisher/publisher_cli.py `
  D:\path\to\DeadlockJan2026 `
  --game deadlock `
  --version deadlock-2026-07-14 `
  --label "July 14, 2026" `
  validate
```

Use `plan` to compare with R2 and `publish` to upload the safe differential
plan. Hashes are cached under `ContentPublisher/.state/` using file size and
nanosecond modification time, so unchanged local files do not need to be read
again on every run.

To publish a hidden version from the CLI, combine `--hidden` with
`--no-promote`. The GUI enforces this automatically.
