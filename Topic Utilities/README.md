# Voice Line Organizer

A utility for organizing voice lines from game files by character, subject, and topic.

## Overview

This application helps you organize voice line files (MP3) by parsing their filenames and creating a JSON file that categorizes them based on:
- The character speaking
- Who they are speaking about (including the relationship)
- The topic of the voice line

## Requirements

- Python 3.6 or higher
- tkinter (usually comes with Python)

## How to Use

1. Run the application:
   ```
   python voice_line_organizer.py
   ```

2. Select the required JSON configuration files:
   - **Logic JSON**: Defines patterns for parsing filenames
   - **Alias JSON**: Maps character aliases to their proper names
   - **Topic Alias JSON**: Maps topic aliases to their proper names

3. Select the source folder containing the MP3 files.

4. Select the output JSON file where the organized data will be saved.

5. Click "Process Voice Lines" to start organizing the files.

## JSON Configuration Files

### Logic JSON

This file defines regex patterns for parsing filenames and extracting information about the speaker, subject, and topic.

Example format:
```json
{
  "^(\\w+)_ally_(\\w+)_(\\w+)_(\\d+)$": {
    "speaker": 1,
    "subject": 2,
    "topic": 3,
    "variation": 4,
    "relationship": "ally"
  }
}
```

In this example:
- The regex pattern matches filenames like "astro_ally_kelvin_clutch_heal_01"
- The numbers (1, 2, 3, 4) refer to the capture groups in the regex pattern
- The application will extract "astro" as the speaker, "kelvin" as the subject, and "clutch_heal" as the topic
- The relationship is set to "ally"

### Alias JSON

This file maps character aliases found in filenames to their proper names.

Example format:
```json
{
  "Astro": ["astro"],
  "Kelvin": ["kelvin", "kelvin_killed_in", "kelvin_pass_on"]
}
```

In this example:
- "astro" in a filename will be mapped to "Astro"
- "kelvin", "kelvin_killed_in", and "kelvin_pass_on" in a filename will all be mapped to "Kelvin"

### Topic Alias JSON

This file maps topic aliases found in filenames to their proper names.

Example format:
```json
{
  "Clutch Heal": ["clutch_heal"],
  "Burns Down Objective": ["burns_down_objective"]
}
```

## Output JSON Structure

The application will generate a JSON file with the following improved structure:

```json
{
  "Speaker Name": {
    "Subject Name (relationship)": {
      "Topic Name": [
        "relative/path/to/file1.mp3",
        "relative/path/to/file2.mp3"
      ]
    }
  }
}
```

This structure allows you to easily find all voice lines where a specific character is speaking about another character on a particular topic, with the relationship context included.

## Supported Filename Patterns

The application supports various filename patterns, including:

1. **Character-to-Character Interactions**:
   - `character_ally_subject_topic_variation.mp3`
   - `character_enemy_subject_topic_variation.mp3`

2. **Team-based Voice Lines**:
   - `character_allies_action_variation.mp3` (general team commands)
   - `character_allies_teammate_action_variation.mp3` (directed at specific teammates)

3. **Observation Voice Lines**:
   - `character_see_subject_topic_variation.mp3`

4. **Alternative Variations**:
   - `character_ally_subject_topic_variation_alt_variation.mp3`
   - `character_enemy_subject_topic_variation_alt_variation.mp3`

5. **Generic Voice Lines**:
   - `character_action_variation.mp3`

6. **Self Voicelines with Alt Versions**:
   - Filenames like `atlas_angry_01_alt_01.mp3` or `atlas_angry_01.mp3` are now correctly grouped under the base topic (e.g., "angry"), regardless of trailing `_alt_XX` or numeric suffixes. The application strips all trailing `_alt_<number>` and `_<number>` patterns from self voiceline topics to ensure proper categorization.

## Debugging Defaults

For debugging, the application sets default values for the required file paths in `voice_line_organizer.py`:

- **Alias JSON**:  
  `C:/Users/mcall/OneDrive/OLD/2023/Documents/DLSoundProject/DLSoundProject/DLSoundProjectUtilities/Topic Utilities/alias.json`
- **Topic Alias JSON**:  
  `C:/Users/mcall/OneDrive/OLD/2023/Documents/DLSoundProject/DLSoundProject/DLSoundProjectUtilities/Topic Utilities/topic_alias.json`
- **Source Folder**:  
  `C:/Users/mcall/OneDrive/OLD/2023/Documents/DLSoundProject/DLSoundProject/May Sounds/sounds/vo`
- **Output JSON**:  
  `C:/Users/mcall/OneDrive/OLD/2023/Documents/DLSoundProject/DLSoundProject/DLSoundProjectUtilities/Topic Utilities/temp.json`

These defaults are set automatically when the GUI starts, but can be changed using the file selection interface.

## Code Structure

### `VoiceLineOrganizer` class

Main GUI class for organizing voice lines.

- **__init__(parent)**: Initializes the GUI, sets up file path variables (with debugging defaults), and creates all sections.
- **create_file_selection_section(parent)**: Builds the file/folder selection UI.
- **create_options_section(parent)**: Adds options such as excluding regular pings.
- **create_processing_section(parent)**: Adds the process button and progress bar.
- **create_log_section(parent)**: Adds a log output area.
- **browse_alias_json() / browse_topic_alias_json() / browse_source_folder() / browse_output_json()**: Handlers for file/folder selection dialogs.
- **log(message)**: Appends messages to the log area in the GUI.  
  Note: Diagnostic messages such as "Could not find variation number..." are only shown in the application's log window and are not written to a file or standard output.
- **process_voice_lines()**: Main processing logic; validates inputs, loads data, processes files, and writes output.
- **_validate_inputs()**: Ensures all required paths are set.
- **_process_file(file_path, alias_data, topic_alias_data, valid_speakers)**: Parses and categorizes a single file.  
  - For self voicelines, this method strips all trailing `_alt_<number>` and `_<number>` patterns from the topic portion of the filename, so that files like `atlas_angry_01_alt_01.mp3` are grouped under the "angry" topic.
- **_get_proper_name(alias, alias_data)**: Resolves an alias to its canonical name.
- **_format_topic(topic_raw, topic_alias_data)**: Resolves a topic alias to its canonical name.

### `main()`

Entry point for running the GUI.
