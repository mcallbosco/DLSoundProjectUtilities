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
| `categories.json` (optional) | `categories.json` |
| `character-names.json` (optional, per-game) | `<game>/character-names.json` |
| `character-names-overlay.json` (optional, per-version) | `character-names.json` |
| `SharedAudio/sha256/<prefix>/<hash>.mp3` | `<game>/audio/sha256/<prefix>/<hash>.mp3` |
| `Audio/` (legacy input) | `<game>/versions/<version>/audio/` |
| `Localization/` | `localization/` |
| `FanLocalization/` | `fan-localization/` |
| `IconPacks/default/` | `icons/default/` |
| `CharacterNameImages/` (optional) | `character-name-images/` |

New Historical Content output uses `SharedAudio` and adds an `audioKey` to each
line. The publisher uploads each SHA-256 object at game scope and reuses an
object already uploaded by another version. The game manifest advertises
`sharedAudioBaseUrl`; readable `filename` values remain unchanged. Legacy
sources containing `Audio/` still publish below the version path and continue
to use the version's `audioBaseUrl`.

`CharacterNameImages/manifest.json` maps each available official language and
character alias to a hashed WebP asset. The publisher validates every manifest
reference, uploads WebP with `image/webp`, and adds
`characterNameImagesUrl` to the version catalog only when the optional folder
exists. Missing localized assets are valid and are handled by the website's
localized-text fallback.

Other icon packs, event audio, website configuration, and redirects are not
part of the initial runtime-versioned content scope.

`categories.json` is an optional per-version overlay. The website always loads
the game-level default advertised by that game's manifest first, then applies
the version document on top of it. Existing categories keep their game-level
order, listed characters are added or reassigned, and new categories are
appended. An empty version category does not erase the game-level category. If
the version document is absent, the game-level categories are used unchanged. The
GUI's **Publish game categories** action publishes the selected folder's
`categories.json` to `<game>/categories.json` and updates that game's
`defaultCategoriesUrl` without publishing or modifying a content version. This
is scoped independently for each game.

The category document contract is intentionally small:

```json
{
  "schemaVersion": 1,
  "defaultCategory": "Characters",
  "categories": [
    { "name": "Characters", "characters": [] },
    { "name": "NPCs", "characters": ["shopkeeper"] }
  ]
}
```

Category array order is display order. Characters omitted from every explicit
list go into `defaultCategory`; `hidden: true` hides an entire category.

## Per-game character display names

`character-names.json` maps canonical internal names and aliases to the names
shown by the website. It is mutable per-game control data, not a version-scoped
asset:

```json
{
  "schemaVersion": 1,
  "game": "deadlock",
  "names": {
    "forge": "McGinnis",
    "mcginnis": "McGinnis"
  }
}
```

When the source contains this file, normal publication uploads it to
`<game>/character-names.json` before updating the game manifest's
`characterNamesUrl`. **Publish game display names** performs the same update
without uploading or changing a content version. Publish this document before
the first website build that depends on it.

## Per-version character display names and aliases

`character-names-overlay.json` uses the same schema as the per-game document,
but it needs to contain only the keys that differ for this version. The
publisher uploads it to `<game>/versions/<version>/character-names.json` and
adds `characterNamesUrl` to that version's manifest entry. The website layers
the document over the game-wide names.

The Historical Content publisher also provides **Publish version display
names** for updating this small overlay, its release/inventory metadata, and
the game manifest without revalidating or republishing the version's audio.

For example, versions before Old Gods, New Blood can retain the historical
patron names without changing the current game defaults:

```json
{
  "schemaVersion": 1,
  "game": "deadlock",
  "names": {
    "patron_female": "The Sapphire Flame",
    "patron_male": "The Amber Hand"
  }
}
```

## All-version character route index

Each successful version publication also updates the small per-game document
at `<game>/characters.json`. The game manifest advertises this URL through
`charactersUrl`. The static website build uses its `characters` union to export
a route for every character that occurs in any published version, including a
hidden version.

```json
{
  "schemaVersion": 1,
  "game": "deadlock",
  "updatedAt": "2026-07-18T12:00:00+00:00",
  "characters": ["abrams", "butcher"],
  "versions": {
    "deadlock-base": ["butcher"],
    "deadlock-current": ["abrams"]
  }
}
```

The publisher replaces the list for the version being published, retains lists
for the other versions in the manifest, and recomputes the union. On the first
publication after this feature is installed, it can initialize missing lists
from the already-published conversation and voiceline JSON. The index is not a
visibility or access-control list; hidden versions are included so that their
explicit URLs work.

Use **Refresh character routes** in the version manager to initialize or repair
this document without re-uploading version assets. On an initial repair, the
publisher reads the already-published conversation and voiceline JSON for each
catalog version, writes `characters.json`, and advertises it from the game
manifest only after the route list is available.

Transcript and category corrections do not require a website deployment. A
website rebuild is required only when the union gains a character whose static
route was not in the previous export.

## Safety and caching rules

- JSON is mutable and can be corrected under an existing version ID.
- Official binary content is immutable at a published object path. Version-local
  custom audio is replaceable under the same custom version ID and uses
  revalidation caching instead of immutable caching.
- Shared audio is content-addressed and is never deleted automatically.
- New binary files can be added to an existing version.
- A non-custom binary whose bytes differ from an existing object is reported as
  a conflict and blocks publication.
- Remote binary files missing from the local source are retained.
- Remote-only JSON is reported but is not deleted automatically.
- The publisher uploads content first and updates the game manifest last.
- Changed JSON and custom-audio URLs are purged individually when cache-purge credentials are
  configured.
- A version can be marked `hidden` so normal website version selectors omit it.
  Hidden versions are still addressable through an explicit `?version=` URL;
  this flag is not authentication or access control.
- The manifest's `versions` array is the display order. The GUI's version
  manager can move entries, toggle visibility, and make any existing visible
  version latest without moving or re-uploading its content.
- Making a version latest through the manager automatically unhides it but does
  not change its chronological/display position.
- The Historical Content publication dialog can select and publish multiple
  generated versions. It validates the full selection before uploading and
  processes the batch oldest-to-newest so shared audio is reused efficiently.
- A `kind: custom` source is never eligible for `latestVersion`. It must name
  an official base version, use embedded transcripts, contain a publishable
  `custom-import-report.json` that explicitly records `speechToTextUsed: false`,
  and preserve a hash-verified pinned transcript source. Import warnings are
  printed during validation but do not block publication.
- Custom audio must live in version-local `Audio/`. `SharedAudio/`, `audioKey`,
  absolute `audioUrl`, and missing local recordings are publication errors.
  Empty embedded transcript strings are allowed for warned, untranslated lines.
  This prevents official and mod audio from mixing.
- **Clear game content...** is a guarded format-reset action. It deletes and
  verifies only the selected game's `<game>/` prefix, never the entire shared
  bucket.

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
