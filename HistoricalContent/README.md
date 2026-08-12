# VLViewer Historical Content

See [END_TO_END_DATA_FLOW.md](END_TO_END_DATA_FLOW.md) for the complete data
flow, operator process, production path, and current implementation limits.

See [VPK_TO_PUBLISH_PIPELINE_PLAN.md](VPK_TO_PUBLISH_PIPELINE_PLAN.md) for the
remaining automated verification and historical delta-import work.

This is the one operator-facing utility for historical VLViewer content. It
accepts a Deadlock VPK directly and creates:

- a persistent per-version audio extraction;
- headless conversation and voiceline data;
- a separate Git repository containing readable transcript JSON;
- a small SQLite version/recording index;
- editable per-game and per-version category JSON; and
- a complete local CDN tree that can be seeded into isolated Wrangler R2;
- a generated production source; and
- integrated validation, R2 publication, and version-catalog controls.

Production changes occur only after you open **Publish / manage versions** and
confirm the publication action.

## Input

Select the historical build's main VPK:

```text
<archived-build>/game/citadel/pak01_dir.vpk
```

Historical Content starts Source2Viewer itself. It extracts audio once into
`<data-dir>/workspaces/<game>/<version>/source/Audio`, parses conversations and
voicelines without starting the old GUIs, generates transcripts, and creates
the website preview data. A rerun with the same VPK fingerprint reuses the
existing extraction.

Audio `filename` values remain POSIX-style semantic paths for display,
localization, transcripts, and diagnostics. Each generated line also contains
an `audioKey` such as `sha256/ab/<hash>.mp3`. Playback resolves that key through
the game manifest's `sharedAudioBaseUrl`. Thus identical bytes from different
paths or versions use one shared object, while filenames remain readable.
Each line in `all_voicelines.json` and each line in `all_conversations.json`
also contains `duration`, measured in seconds and rounded to milliseconds. The
website can show this value without first downloading audio metadata.

Very old builds can use names such as `rr_test_19_angry_01.mp3` that do not
contain a speaker. Historical Content gets the speaker alias from the first
folder below `sounds/vo` and then applies `character-mappings.json`. It uses the
normal topic parser when possible. If an old event name does not fit the newer
grammar, the utility keeps the recording as a readable `Self` topic instead of
dropping it. An unknown historical speaker folder is preserved under its own
name. Add or change that alias in the per-game character mapping and process
the VPK again to rename it.

The parser uses a folder as the speaker only for an approved historical
layout, and only when normal filename parsing fails. The approved layouts are
`book/oathkeeper`, `neutral_gremlin`, `announcer/count_up`, the two patron
announcer folders, `npc_reporter`, `shopkeeper`, `dynamo`, `nano`, and
`t1_guardians/guardian_test_01` through `guardian_test_04`. Guardian speakers
remain separate. Oathkeeper scene recordings are grouped by scene. Additions
to this fallback list require a code change so an ordinary folder cannot
silently override the speaker in a valid filename.

Historical filename normalization is otherwise data driven. Character aliases
are in `character-mappings.json`, topic aliases are in `topic-aliases.json`, and
display groups are in `voiceline-groups.json`. The current rules keep `Start`
and `Start match` separate, keep ally and enemy relationship groups separate,
resolve multi-part names such as `grey_talon`, remove legacy `_old` ping markers,
and normalize the historical orange-lane, `headed_to_*`, `idols_call`,
`take_core`, `nevermind`, and `back` vocabulary.

When **Extract icons** is enabled, the utility extracts VLViewer's four
official portrait variants directly from the VPK: `*_sm` for **Minimap**,
`*_card` for **Normal**, `*_card_gloat` for **Gloat**, and
`*_card_critical` for **Critical**. A variant that did not exist in that build
is omitted. It also reads the same build's `scripts/heroes.vdata`. Thus, an
internal hero such as `atlas` can use its actual historical `bull` portrait
while the manifest also exposes the configured canonical name `abrams`. The
latest VPK's patron objective icons are also added to the Minimap variant:
`patron_hiddenking` resolves through `patron_male`/`hidden king`, and
`patron_archmother` resolves through `patron_female`/`archmother`. These use
the same alias expansion and content-addressed filenames as hero portraits.
The generated override is written to:

```text
<workspace>/source/IconPacks/default/
```

Extracted portrait filenames include their SHA-256 hash. This lets a corrected
backfill publish new immutable R2 objects while the updated JSON manifest moves
clients away from the older object paths.

Icon extraction does not require loose localization files beside the VPK. It
is cached with the VPK workspace and is copied into both local preview content
and the publisher source by baseline generation.

When **Extract localized names** is enabled, the utility also looks for the
localized hero-name images in the main VPK and every available official
localization VPK. Team patron logos are included in the English asset set. A
language or individual image that did not exist in a build is skipped with a
warning, so older VPKs remain processable and the website can render localized
text in its place.

The SVG sources are rasterized as grayscale-compatible WebP files while
preserving their antialiased alpha channel. The converter tries exact lossless
and high-quality near-lossless WebP and keeps the smaller result. Images are
not enlarged and their maximum height defaults to 512 pixels; the value is
configurable in the pipeline options. The generated manifest and hashed assets
are written to:

```text
<workspace>/source/CharacterNameImages/
```

This extraction is cached using all relevant localization VPK fingerprints,
the output format version, and the configured maximum height. Baseline
generation carries the directory into both preview and publisher output.

## Launch the GUI

```powershell
HistoricalContent\run_historical_content_gui.bat
```

The launcher installs the OpenAI and R2 SDKs and the local Sharp image
dependency if they are missing. In the GUI:

1. Select the main VPK and Source2Viewer CLI.
2. Select the transcript repository and persistent workspace.
3. Optionally select a **Predefined official transcripts CSV**. Safe exact
   matches fill missing text before any OpenAI requests.
4. Enter the version ID and display label.
5. Click **Process VPK / regenerate content**.
   Wait until the utility reports **Version ready**. The preview, category, and
   publication buttons stay disabled while this operation changes generated
   files.
6. Open and edit the generated categories, voiceline groups, or character
   mappings in the transcript repository.
7. Process the VPK again after a group or mapping change. Audio extraction is
   reused.
8. Click **Apply categories to preview** and refresh the browser. This updates
   only category objects; it does not re-index audio or regenerate transcripts.
9. Click **Seed and start website preview**.

The preview selector retains every version generated in the same data
workspace. The current run becomes latest, and prior generated versions remain
available from the settings selector. A direct preview URL is:

```text
http://localhost:3000/?version=preview-deadlock-base
```

The Worker uses `.wrangler/preview-state`; it does not use Cloudflare
credentials or touch the production R2 bucket.

After previewing, the deployable source is written to:

```text
D:/VLViewerHistoricalData/generated/deadlock-base/
```

Select **Publish / manage versions** in Historical Content when you are ready.
The publication dialog reuses saved Cloudflare credentials, defaults a new
version to hidden, builds a dry-run plan, publishes, and opens the version
catalog manager. Generated `SharedAudio` objects are hard links to the
persistent game-level hash store when the filesystem supports them.

Use **Publish multiple...** to select any generated folders shown in the local
catalog. The utility validates every selection before the first upload, then
publishes oldest-to-newest so later versions reuse shared audio already in R2.
It saves the public manifest in local newest-to-oldest order after the batch.
Keep **hidden for review** selected for a staged upload, or clear it to apply
the local visibility flags and `latestVersion` after the batch succeeds.

For a deliberate format reset, **Clear game content...** deletes and verifies
only the current `<game>/` namespace. It preserves other games in the same R2
bucket and requires the operator to type the game ID. Clear immediately before
a ready bulk publication because the live game endpoint is unavailable between
the reset and the first successful publication.

## Transcript repository

The generated repository contains:

```text
DeadlockTranscripts/
  README.md
  schema.json
  transcripts/<audio-folder>/<audio-filename>.json
  config/deadlock/categories.json
  config/deadlock/character-names.json
  config/deadlock/character-mappings.json
  config/deadlock/topic-aliases.json
  config/deadlock/voiceline-groups.json
  config/deadlock/conversation-overrides.json
  config/deadlock/transcription-vocabulary.json
  config/deadlock/versions/deadlock-base/categories.json
  config/deadlock/versions/deadlock-base/character-names.json
```

The `transcripts` tree mirrors the relative audio tree. For example,
`forge/mcginnis_select_01.mp3` uses
`transcripts/forge/mcginnis_select_01.mp3.json`. Voicelines and individual
conversation lines use the same tree and schema. Website locations, speakers,
categories, and conversation membership are not stored in transcript files.

Each file has a `revisions` array. The utility preserves one revision for each
distinct audio SHA-256 value. To correct text, edit the matching revision's
`text`, set `source` to `manual`, and remove `model`. Git history records text
corrections; do not add another revision unless the audio hash changes.
Regenerating the preview reads the working tree directly, so a commit is not
required before testing.

Effort recordings still get a per-audio transcript JSON file. The utility leaves
their text blank, sets `source` to `skippedeffort`, and does not send them to the
transcription API. A non-empty `manual` or `official` revision is preserved.

Known non-speech recordings also get a transcript file with blank text and
`source: "skippednonspeech"`. This includes pain/emote recordings and audio in
explicit SFX paths. A blank response from the transcription model is stored with
the same terminal source, so rerunning a version does not repeatedly submit that
audio. The model name remains in the revision when the source came from a blank
model response. Non-empty `manual` and `official` revisions are always preserved.

The first run after this format change migrates the old per-speaker and
per-conversation files. It removes those legacy JSON files only after it writes
all per-audio files successfully.

VDF-only phantom lines have official text but no audio file. They remain in
generated voiceline or conversation JSON with `filename: ""` and
`is_phantom: true`. Historical Content does not create transcript files, audio
keys, durations, asset-index rows, or recording-comparison status for these
text-only entries. An empty filename without the phantom marker is rejected as
malformed content.

The game category file is always the base for the baseline preview. The version
category file is layered on top: it can add categories, add or reassign
characters, and change category visibility without copying or erasing the
game-level structure.

`config/deadlock/transcription-vocabulary.json` contains the structured names,
places, game terms, and transcription guidelines attached to every OpenAI
transcription request through the prompt field. Historical Content seeds it
from `Assets/deadlock_vocabulary.json` once, then treats the transcript
repository copy as the editable source. The pipeline does not use a separate
glossary file.

The optional predefined transcript CSV imports official text that exists in a
newer build but applies to path-stable historical recordings. The utility
converts a CSV path such as
`sounds/vo/astro/ping/astro_ping_01.vsnd_c` to the extracted audio key
`astro/ping/astro_ping_01.mp3` and requires an exact, case-insensitive
full-path match. It does not match by basename. Rows with `single_match` and
`multiple_keys_same_transcription` are safe to import. Rows with
`multiple_conflicting_transcriptions` are skipped and counted in the log.
Imported text fills only blank revisions, is stored with `source: "official"`,
and is available to both voicelines and conversation lines. Existing nonempty
text is not replaced.

`config/deadlock/character-names.json` is the editable per-game mapping from
internal names and aliases to website display names. Baseline generation copies
it to `<game>/character-names.json` in the preview tree and to
`character-names.json` in the publisher source. Use **Open display names** to
edit it, then regenerate content. Normal version publication updates this
game-level object before the game manifest. **Publish game display names** can
update only this mapping without publishing a version.

An optional `config/<game>/versions/<version>/character-names.json` contains
only display-name or alias mappings that differ for that version. Generation
copies it to the version preview and to `character-names-overlay.json` in the
publisher source. Publication stores it at
`<game>/versions/<version>/character-names.json` and advertises it through the
version's `characterNamesUrl`. Versions without this file inherit the game map.

## Advanced baseline-only CLI

The CLI remains available for tests or migration of an already prepared source
folder. It is not part of the normal operator process.

```powershell
python HistoricalContent\baseline_cli.py create `
  D:\path\to\HistoricalBaseExport `
  --transcript-repo D:\Projects\DLSoundProject\DeadlockTranscripts `
  --data-dir D:\VLViewerHistoricalData `
  --version deadlock-base `
  --label "Historical baseline" `
  --predefined-transcripts `
    D:\Git\GameTracking-Deadlock\vo_shared_2025-02-25_to_2026-07-14_transcripts.csv
```

Use `--no-transcribe` to generate a preview while leaving missing transcript
text empty. Use `--no-audio` for a fast data-only preview.

OpenAI credentials are resolved in this order:

1. Key entered in the GUI.
2. `OPENAI_API_KEY` environment variable.
3. Windows DPAPI credential saved by the GUI.
4. Existing `~/.open_ai_key` file used by the older utilities.

`Workers` controls the number of simultaneous transcription requests.
Completed results are atomically checkpointed to their per-audio transcript
JSON files as the batch runs. If a later request fails or the process stops,
the next run reuses those completed files instead of waiting for the entire
batch to finish before any transcript is saved.

## Local version order and comparisons

After generating at least one version, select **Manage local versions...** in
the Historical Content utility. The local catalog is stored at
`<data-dir>/catalogs/<game>.json`; preview regeneration does not replace it.
Order entries newest-to-oldest, then use **Save and recalculate**. The order is
used by the local selector and by adjacent-version voiceline comparisons.
`latestVersion` is independent: making an older version the default does not
move it or change the comparison timeline. Hidden versions remain in the
timeline even though the normal selector omits them.

The comparison engine uses the normalized relative filename as line identity
and the audio SHA-256 value as recording identity:

- A filename absent from the adjacent older version is `new`.
- A filename with a different audio hash is `modified`.
- A filename absent from the adjacent newer version is marked
  `removedInNextVersion` while viewing the historical version that contains it.
- The oldest version is not treated as entirely new, and the newest version has
  no next-version removal comparison.
- Transcript and category corrections do not modify recording status. A rename
  is a removal at the old filename and an addition at the new filename.

The utility writes `versionStatus` into the preview `voicelines.json` and the
publisher's `all_voicelines.json`. Reordering recalculates all available local
versions. If already-published chronology changes, republish the affected JSON;
shared audio does not need to be uploaded again.

The model selector supports `gpt-4o-transcribe`,
`gpt-4o-mini-transcribe`, and `whisper-1`. The default remains
`gpt-4o-transcribe`; selecting the mini model affects new generated
transcripts only.

## Local preview details

The GUI bulk-seeds the generated tree into local R2 and launches:

- the content Worker at `http://127.0.0.1:8787`; and
- the VLViewer project at `D:/Projects/VLViewer` on
  `http://localhost:3000` with the local content-base override.

Existing configurations that still point to the former ConvoWebsite project
are migrated to the VLViewer path automatically. Before starting its local
Next.js CLI with `--turbopack`, Historical Content runs VLViewer's lightweight
game-config preparation script for the selected game. It does not run the full
asset refresh or production build.

Large local seeds use bounded batches and can resume. If seeding stops because
of a local process or network-stack error, click **Seed and start website
preview** again. Historical Content reuses unchanged objects already present in
`.wrangler/preview-state` and completes the remaining objects.

Baseline generation also writes `<game>/characters.json` and
`<game>/character-names.json`, then places both URLs in the local game manifest.
These are the same per-game contracts used in production. A local baseline
route index maintains the union across every generated preview version. The
Next.js static build reads both documents so historical-only characters have
exported pages and names are not baked into website source.
