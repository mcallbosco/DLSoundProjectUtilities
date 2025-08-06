# Voice Line Utilities GUI

A graphical tool for organizing, copying, and transcribing voice line files, including advanced features for updating JSON files with status information.

## Features

- Organize voice line files by speaker, subject, and topic.
- Copy voice line files based on a JSON manifest.
- Transcribe voice line files using OpenAI Whisper API.
  - Now supports arbitrarily nested categories in the JSON structure. Any topic or subcategory whose value is an object (dict) will be recursively traversed, and all nested files will be processed for transcription regardless of depth.
- Manage and store your OpenAI API key securely.
- **Apply Status to JSON:** Import a .txt file with lines like `sounds/vo/mirage/ping/mirage_ping_ignore_viscous.vsnd_c CRC:001380b678 size:20293 UPDATED` or `... ADDED` and update the loaded JSON so each file entry gets a `status` key with the value from the end of the line (e.g., "ADDED" or "UPDATED").

## How to Use the Status Update Feature

1. Go to the **Transcribe** tab in the GUI.
2. Select your input JSON file.
3. Select your status .txt file (must have lines ending with ADDED or UPDATED).
4. Click **Apply Status to JSON**.
5. Save the updated JSON when prompted.

Each file entry in the JSON whose filename matches a line in the .txt will receive a `"status"` key with the corresponding value.

---

# Voice Line Organizer

## Structure Update (May 2025)

### Ping Subject Parsing Fix

- The logic for parsing ping voicelines (e.g., `bebop_ping_cadence_on_top_of_mid.mp3`) now checks all possible trailing segments after `_ping_` for a valid hero name, not just the last segment.
- This ensures that multi-word hero names or hero names not at the end are correctly identified as the subject.
- Example:
  - `bebop_ping_cadence_on_top_of_mid.mp3` → subject: `cadence`, topic: `on_top_of_mid`
  - `bebop_ping_ignore_yamato.mp3` → subject: `yamato`, topic: `ignore`

### Self Keyword Matching Fix (May 2025)

- Self voiceline detection now requires an exact match or an underscore boundary for self keywords.
- This prevents filenames like `lash_interrupt_atlas_01.mp3` from being misclassified as a self voiceline (subject: self, topic: interrupt_atlas).
- Now, only filenames where the part after the speaker matches a self keyword exactly or starts with a self keyword followed by an underscore are treated as self voicelines.
- Example:
  - `lash_interrupt_05.mp3` → subject: self, topic: interrupt
  - `lash_interrupt_atlas_01.mp3` → subject: atlas, topic: interrupt


### Ping Subcategories (May 2025)

- Pings now support special subcategories, similar to how "subjects" have special categories.
- The `VoiceLineOrganizer` class defines a `special_ping_categories` dictionary mapping subcategory names to lists of topic keywords.
- When a ping's topic matches a keyword in `special_ping_categories`, it is grouped under that subcategory in the output JSON.
- Example structure:
  ```json
  {
    "Speaker": {
      "Subject": {
        "Pings": {
          "Objective": {
            "Capture point": [
              "relative/path/to/file1.mp3"
            ]
          },
          "Danger": {
            "Enemy spotted": [
              "relative/path/to/file2.mp3"
            ]
          },
          "Fallback": [
            "relative/path/to/file3.mp3"
          ]
        }
      }
    }
  }
  ```
- If a ping topic does not match any special category, it is stored as a regular topic under "Pings".

### Class and Method Explanations

#### `VoiceLineOrganizer`
Main class for organizing and categorizing voicelines via a Tkinter GUI.

- **special_categories**: Dict of special voiceline categories and their keywords.
- **special_ping_categories**: Dict of special ping subcategories and their keywords.
- **__init__**: Initializes the GUI, file paths, and options.
- **process_voice_lines**: Loads data, processes all mp3 files, and outputs categorized results. Handles grouping of pings into subcategories if their topic matches a special ping category.
- **_process_file**: Core logic for parsing filenames and extracting speaker, subject, topic, and relationship.
  - Handles special cases (spirit_jar, newscaster, shopkeeper_hotdog).
  - For ping voicelines, checks all trailing segments for a valid hero name and groups pings by subcategory if applicable.
  - For self voicelines, only treats as self if the keyword matches exactly or is followed by an underscore, preventing accidental matches for lines like `interrupt_atlas`.
- **_get_proper_name**: Maps an alias to its canonical hero name using alias data.
- **_format_topic**: Maps a topic alias to its canonical topic name using topic alias data.
- **create_file_selection_section**, **create_options_section**, **create_processing_section**, **create_log_section**: GUI setup helpers.
- **log**: Thread-safe logging to the GUI.
- **_validate_inputs**: Ensures all required files/paths are set.

#### Ping Parsing Logic (in `_process_file`)
- Splits the string after `_ping_` by underscores.
- Checks all possible trailing segments for a valid hero name (using alias.json).
- If a match is found, assigns it as the subject and the rest as the topic.
- If no match, defaults subject to "self" and uses the whole string as the topic.
- After parsing, if the topic matches a keyword in `special_ping_categories`, the ping is grouped under that subcategory in the output JSON.

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
      ],
      "Special Topic Name": {
        "Sub Topic Key": [
          "relative/path/to/file1.mp3"
        ],
        "Another Sub Topic": [
          "relative/path/to/file2.mp3"
        ]
      }
    },
    "Self": {
      "Topic Name": [
        "relative/path/to/file1.mp3"
      ],
      "Special Topic Name": {
        "Sub Topic Key": [
          "relative/path/to/file1.mp3"
        ]
      }
    }
  }
}
```

- Any topic whose value is an object (not a list) is treated as a "Special Topic Key" and all sub-keys are processed.
- Pings where the subject is "self" or matches the speaker are also included under the `"Self"` > `"Pings"` section for that character.
- This allows easy access to all self pings for a character, in addition to their usual categorization.

This structure allows you to easily find all voice lines where a specific character is speaking about another character on a particular topic, with the relationship context included.

## Supported Filename Patterns

The application supports various filename patterns, including:

### Special NPCs: spirit_jar

- Filenames starting with `spirit_jar_` (e.g., `spirit_jar_idol_delivered_01.mp3`, `spirit_jar_idol_waiting_in_street_02_alt_01.mp3`) are handled with custom logic:
  - The speaker is always `"spirit_jar"`.
  - All lines are grouped under the `"self"` key.
  - The subject/topic is everything after `spirit_jar_`, with underscores replaced by spaces and the first letter capitalized (e.g., `spirit_jar_idol_waiting_in_street_02_alt_01.mp3` → `"Idol waiting in street"`).
  - Trailing `_alt_<number>` and `_<number>` suffixes are stripped from the subject/topic.

### Special NPCs: newscaster

- Filenames starting with `newscaster_` are handled with custom logic:
  - `newscaster_headline_01`, `newscaster_headline_01_alt_01`, etc.:
    - Speaker: `"newscaster"`
    - Subject: `"self"`
    - Topic: `"headline"`
  - `newscaster_seasonal_headline_05`, `newscaster_seasonal_headline_06_alt_01`, etc.:
    - Speaker: `"newscaster"`
    - Subject: `"self"`
    - Topic: `"seasonal headline"`
  - `newscaster_seasonal_{character}_unlock_01`, `newscaster_seasonal_bebop_unlock_01`, etc.:
    - Speaker: `"newscaster"`
    - Subject: `{character}` (the referenced character)
    - Topic: `"seasonal unlock"`
  - All trailing `_alt_<number>` and `_<number>` suffixes are stripped from the topic/subject as appropriate.

### Special NPCs: shopkeeper_hotdog

- Filenames starting with `shopkeeper_hotdog_` are handled with custom logic:
  - `shopkeeper_hotdog_buy_armor_01`, `shopkeeper_hotdog_buy_early_11_alt_01`, etc.:
    - Speaker: `"shopkeeper_hotdog"`
    - Subject: `"self"`
    - Topic: `"Buy ..."` (the rest after `buy_`, with underscores replaced by spaces and first word capitalized, e.g., `"Buy armor"`, `"Buy early"`)
  - `shopkeeper_hotdog_call_out_09`, `shopkeeper_hotdog_close_shop_02`, `shopkeeper_hotdog_open_spirit_01`, etc.:
    - Speaker: `"shopkeeper_hotdog"`
    - Subject: `"self"`
    - Topic: The rest after `shopkeeper_hotdog_`, with underscores replaced by spaces and first word capitalized (e.g., `"Call out"`, `"Close shop"`, `"Open spirit"`)
  - `shopkeeper_hotdog_t4_astro_02`, `shopkeeper_hotdog_t4_mirage_buys_carpet`, etc.:
    - Speaker: `"shopkeeper_hotdog"`
    - Subject: `{character}` (the referenced character)
    - Topic: `"t4 ..."` (the rest after the character, with underscores replaced by spaces and first word capitalized, e.g., `"t4 buys carpet"`, `"t4 02"`)
  - All trailing `_alt_<number>` and `_<number>` suffixes are stripped from the topic/subject as appropriate.


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
   - Filenames ending with either `_<number>_alt` (e.g., `upgrade_power2_01_alt.mp3`) or `_alt_<number>` (e.g., `upgrade_power2_alt_01.mp3`) are both recognized and stripped for topic grouping. This ensures lines like "Upgrade power2 01 alt" and "Use power2 01 alt" are handled correctly.
   - **Self Keyword Matching Update:** Self voiceline detection now fully supports keywords with underscores (e.g., `close_call`, `last_one_standing`). The parser matches the longest possible self keyword at the start of the subject line, so filenames like `atlas_close_call_01.mp3` or `atlas_last_one_standing_02.mp3` are correctly categorized under their respective topics.

## Debugging

### Voice Line Organizer Debug Output

The application now provides detailed debug output for both processing and sorting phases:

- **Processing Debug Output:**  
  All major steps in the processing pipeline (input validation, file scanning, loading configuration, saving output, and exception handling) are logged to `self.processing_debug_log`. This includes entry/exit points for main methods and key branches in the logic. These debug messages are displayed in the log area after processing is complete.

- **Sorting Debug Output:**  
  During the sorting phase, detailed debug information about the sorting logic is collected internally. This debug output is **not** shown in the log text box while sorting is in progress. Instead, all debug messages related to sorting are stored in memory and only displayed in the log area after sorting is fully complete. This ensures the log remains uncluttered during processing and provides a clear, consolidated view of the sorting steps once finished.

Typical debug output includes:
- Which topics were found in the priority list and their assigned sort order.
- Which topics were not in the priority list.
- The order of topics before and after sorting.
- When special categories or "Pings" are added to the sorted topics.
- Entry and exit points for processing and file parsing functions.
- Any exceptions or errors encountered during processing.

### copy_voice_files.py Debugging

The `copy_voice_files.py` script now includes extensive debug print statements:

- Entry and exit points for all major functions (e.g., `get_file_date`, `copy_voice_files`).
- Before and after major operations (loading JSON, copying files, saving output).
- Before copying each file, including the source and destination paths.
- On all exceptions, with error details.

Debug output is printed directly to the terminal/console when running the script, making it easier to trace the flow of execution and diagnose issues.

### Debugging Defaults

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

#### Methods

- **__init__(parent)**: Initializes the GUI, sets up file path variables (with debugging defaults), and creates all UI sections.
- **create_file_selection_section(parent)**: Builds the file/folder selection UI.
- **create_options_section(parent)**: Adds options such as excluding regular pings.
- **create_processing_section(parent)**: Adds the process button and progress bar.
- **create_log_section(parent)**: Adds a log output area.
- **browse_alias_json() / browse_topic_alias_json() / browse_source_folder() / browse_output_json()**: Handlers for file/folder selection dialogs.
- **log(message)**: Appends messages to the log area in the GUI.  
  Note: Diagnostic messages such as "Could not find variation number..." are only shown in the application's log window and are not written to a file or standard output.
- **process_voice_lines()**: Main entry point for processing all MP3 files. Handles validation, loads configuration, processes files, applies sorting, and writes output JSON. Collects sorting debug output in a temporary list and only displays it in the log area after sorting is complete.
- **custom_self_sort(topics, debug_log)**: Sorts the "Self" topics for each speaker, prioritizing "Select", "Unselect", "Pre game", and "Post game" topics. Appends detailed debug messages to the provided debug_log list during sorting.
- **_validate_inputs()**: Checks that all required files and folders are selected before processing.
- **_process_file(file_path, alias_data, topic_alias_data, valid_speakers)**: Parses a single MP3 filename and returns structured information for categorization.
- **_get_proper_name(alias, alias_data)**: Maps an alias to its proper character name using the alias JSON.
- **_format_topic(topic_raw, topic_alias_data)**: Maps a topic alias to its proper name using the topic alias JSON.

#### Debugging Behavior

- All debug output related to sorting is stored in a temporary list (`self.sort_debug_log`) during sorting and only displayed in the log area after sorting is finished. This prevents clutter and provides a clear, consolidated view of the sorting process.


---

### Output JSON Structure from copy_voice_files.py

The output JSON produced by `copy_voice_files.py` mirrors the full nested structure of the input JSON, but replaces each file path string with an object containing the filename and the file's date. All subcategories, topics, and nested groupings are preserved exactly as in the input.

#### Structure

- Every string representing a file path is replaced with:
  ```json
  {
    "filename": "the_file_name.mp3",
    "date": "YYYY-MM-DD"
  }
  ```
- All dictionaries and lists in the input are preserved in the output, so subcategories (such as "Killstreaks", "Movement", or custom groupings) remain nested as in the original.

#### Example

Input:
```json
{
  "Atlas": {
    "Self": {
      "Killstreaks": {
        "Killing streak high": [
          "atlas/killstreak_high_01.mp3",
          "atlas/killstreak_high_02.mp3"
        ],
        "Nested Category": {
          "Ultra streak": [
            "atlas/ultra_streak_01.mp3"
          ]
        }
      },
      "Movement": [
        "atlas/leave_base_01.mp3"
      ]
    }
  }
}
```

Output:
```json
{
  "Atlas": {
    "Self": {
      "Killstreaks": {
        "Killing streak high": [
          {
            "filename": "killstreak_high_01.mp3",
            "date": "2025-05-14"
          },
          {
            "filename": "killstreak_high_02.mp3",
            "date": "2025-05-14"
          }
        ],
        "Nested Category1": {
          "Nested Category2": {
            "Ultra streak": [
              {
                "filename": "ultra_streak_01.mp3",
               "date": "2025-05-14"
             }
           ]
          }
        }
      },
      "Movement": [
        {
          "filename": "leave_base_01.mp3",
          "date": "2025-05-14"
        }
      ]
    }
  }
}
```

#### Notes

- The output preserves all subcategories and nested groupings from the input JSON.
- If a file cannot be found, its "date" value will be `null`.
- The output is suitable for further processing, archiving, or UI display, as it retains the full organizational hierarchy of the original voice line data.

---

### `transcribe_voice_files.py` Methods and Structure

- **transcribe_voice_files(...)**: Main entry point for batch transcription. Loads the input JSON, detects all MP3 files (including those under any nested category), and manages parallel transcription and consolidated output.
  - Uses a recursive helper (`collect_mp3_files`) to traverse the JSON structure, supporting arbitrarily deep nesting of categories and subcategories.
  - Any topic or subcategory whose value is a dictionary will be recursively traversed, and all files found at any depth will be processed.
  - For standard topics (list of files), processes as before.
  - Consolidated output preserves the full nested structure in the output JSON.

#### Recursive Category Support

The transcription script now supports input JSON structures with any level of nested categories. For example:

```json
{
  "Atlas": {
    "Self": {
      "Killstreaks": {
        "Killing streak high": [
          {
            "filename": "killstreak_high_01.mp3",
            "date": "2025-05-14"
          },
          {
            "filename": "killstreak_high_02.mp3",
            "date": "2025-05-14"
          }
        ],
        "Nested Category": {
          "Ultra streak": [
            {
              "filename": "ultra_streak_01.mp3",
              "date": "2025-05-14"
            }
          ]
        }
      },
      "Movement": [
        {
          "filename": "leave_base_01.mp3",
          "date": "2025-05-14"
        }
      ]
    }
  }
}
```

All files, regardless of how deeply they are nested, will be discovered and transcribed. The recursive logic ensures that any new subcategory structure is automatically supported without code changes.

- **process_file(args)**: Handles the transcription of a single file, including metadata extraction and output formatting.
- **get_openai_client()**: Thread-local OpenAI API client loader.
- **load_api_key()**: Loads the OpenAI API key from the user's home directory.
- **load_custom_vocabulary(vocab_file)**: Loads a custom vocabulary prompt for improved transcription accuracy.

This update ensures the transcription utility is fully compatible with flexible, deeply nested JSON structures.
- **process_file(args)**: Handles the transcription of a single file, including metadata extraction and output formatting.
- **get_openai_client()**: Thread-local OpenAI API client loader.
- **load_api_key()**: Loads the OpenAI API key from the user's home directory.
- **load_custom_vocabulary(vocab_file)**: Loads a custom vocabulary prompt for improved transcription accuracy.

This update ensures the transcription utility is fully compatible with the flexible JSON structure described above, including all special topic keys.

---

# Technical Specification: Consolidated Transcription Output Format

## Overview

The consolidated transcription output produced by `transcribe_voice_files.py` is designed to exactly mirror the structure of the input JSON, preserving all nested categories, subcategories, and intermediate keys at any depth. The only transformation is that each file entry (at the leaves) is replaced with a transcription result object containing metadata and the transcription text.

## Structure

- The output is a recursive structure of dictionaries and lists, matching the input.
- Every dictionary key and subcategory in the input is preserved in the output, regardless of depth or whether it contains files directly.
- At the leaves (where the input contains a list of file entries), each file entry is replaced by a transcription object.

### Transcription Object

Each file entry in the output is an object with the following fields:

- `filename`: The name of the audio file (e.g., `"astro_ping_ability1_almost_ready.mp3"`).
- `date`: The date the file was last modified or created (format: `"YYYY-MM-DD"`).
- `voiceline_id`: The base filename without extension.
- `transcription`: The transcribed text for the audio file.

### Example

#### Input

```json
{
  "Atlas": {
    "Self": {
      "Killstreaks": {
        "Killing streak high": [
          { "filename": "killstreak_high_01.mp3", "date": "2025-05-14" }
        ],
        "Nested Category1": {
          "Nested Category2": {
            "Ultra streak": [
              { "filename": "ultra_streak_01.mp3", "date": "2025-05-14" }
            ]
          }
        }
      }
    }
  }
}
```

#### Output

```json
{
  "Atlas": {
    "Self": {
      "Killstreaks": {
        "Killing streak high": [
          {
            "filename": "killstreak_high_01.mp3",
            "date": "2025-05-14",
            "voiceline_id": "killstreak_high_01",
            "transcription": "Transcribed text here"
          }
        ],
        "Nested Category1": {
          "Nested Category2": {
            "Ultra streak": [
              {
                "filename": "ultra_streak_01.mp3",
                "date": "2025-05-14",
                "voiceline_id": "ultra_streak_01",
                "transcription": "Transcribed text here"
              }
            ]
          }
        }
      }
    }
  }
}
```

## Rules

- **Preservation of Structure:** All intermediate and leaf category names, and their hierarchy, are preserved exactly as in the input.
- **Replacement at Leaves:** Only the file entries at the leaves are replaced; all other structure is untouched.
- **Partial Transcription:** If a file entry does not have a transcription result (e.g., missing or failed), the original entry is retained in the output.
- **Arbitrary Depth:** The format supports any level of nesting, including categories that contain only subcategories and no files directly.

## Use Cases

- Enables downstream tools or UIs to display or process the full organizational hierarchy of voice lines, with all metadata and transcriptions attached.
- Ensures compatibility with any input JSON structure, including future expansions with deeper or more complex nesting.

## Implementation Notes

- The transformation is performed by recursively walking the input JSON and replacing each file entry with its transcription result, using a filename-to-transcription mapping.
- No flattening or restructuring is performed; the output is a true mirror of the input with only the leaves transformed.
