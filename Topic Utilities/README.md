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

## Improvements

The latest version includes several improvements:
- Better handling of character names with multiple aliases
- Improved topic formatting with proper capitalization
- Inclusion of relationship information (ally/enemy/allies) in the subject key
- Case-insensitive matching for aliases
- Better handling of multi-part topics
- Special handling for team-based voice lines (allies patterns)
- Support for alternative variations of voice lines (with _alt_ in the filename)
- Support for more generic voice line patterns

## Example Files

The repository includes example JSON files to help you get started:
- `example_logic.json`
- `example_logic_complex.json` - For more complex filename patterns
- `example_alias.json`
- `example_topic_alias.json`

You can use these as templates for creating your own configuration files. 