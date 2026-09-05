# VLViewer Historical Content

Turn an archived Deadlock VPK into versioned VLViewer content. Historical Content
extracts audio and images, parses voicelines and conversations, maintains editable
transcripts, previews the result locally, and publishes reviewed versions to R2.
It also imports custom voice mods against an official base version.

## Install and run

Use Python 3.12 or newer with Tk available, Node.js/npm for image conversion and
local preview, and Source2Viewer CLI for VPK extraction. From this checkout,
install into your Python environment:

```bash
python -m pip install -e .
npm ci --prefix HistoricalContent
historical-content
```

On Windows, `HistoricalContent\run_historical_content_gui.bat` remains the GUI
launcher. The application needs this repository checkout for its Node image
converters and preview resources.

Select the archived build's `game/citadel/pak01_dir.vpk`, Source2Viewer, a
transcript repository, and a persistent data directory. **Process VPK / regenerate
content** builds the version; **Seed and start website preview** opens a local
preview; **Publish / manage versions** opens the publication dialog.

## Commands and documentation

| Command | Use |
| --- | --- |
| `historical-content` | Historical Content GUI |
| `historical-baseline` | Generate or refresh content from a prepared source |
| `historical-custom-mod` | Import a custom voice VPK and pinned transcript |
| `historical-publish` | Validate, plan, and publish a generated source |

The original scripts in `HistoricalContent/` and
`ContentPublisher/publisher_cli.py` forward to the installed package for existing
launch commands. The old standalone utilities and publisher GUI have been removed.

- [Historical Content guide](HistoricalContent/README.md): processing, transcripts,
  custom mods, preview, and local version management.
- [Data flow](HistoricalContent/END_TO_END_DATA_FLOW.md): source ownership,
  generated output, publication, and recovery.
- [Publishing reference](ContentPublisher/README.md): source layout, R2 settings,
  version controls, and command-line publication.

## Development

Application code lives in `HistoricalContent/historical_content/`, grouped into
`app`, `extraction`, `parsing`, `generation`, `transcripts`, and `publishing`.
Shared settings and protected credential storage live beside those packages.
Parsing and generation run independently of Tk. Bundled JSON defaults seed
editable configuration in the transcript repository.

```bash
python -m unittest discover -s HistoricalContent/tests -v
```

Linux GUI tests can run under `xvfb-run -a`. Windows CI exercises DPAPI credential
storage. Output regression fixtures use synthetic inputs captured from the old
utilities; they do not substitute for testing a real archived VPK.
