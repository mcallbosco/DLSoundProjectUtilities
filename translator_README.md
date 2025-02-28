# Conversation Translator

This tool translates non-English text in conversation JSON files using the DeepL API. It's designed to work with the conversation JSON format used in the Character Conversation Player.

## Features

- Detects non-English text in conversation transcriptions (with special attention to Japanese)
- Translates detected text to English using DeepL API
- Adds translations in parentheses at the end of the original text
- Provides a selectable list of lines to translate with checkboxes
- Allows you to select/deselect specific lines for translation
- Includes a "Select Only Japanese" option for quick filtering
- Visual indicators show which lines contain Japanese text (🇯🇵) vs other non-Latin text (🌐)
- Sorts lines to prioritize Japanese text at the top of the list
- Toggle between strict and relaxed language detection modes
- Saves translations to a new file, preserving the original

## Requirements

- Python 3.6+
- DeepL API key (free or paid)
- Required Python packages (install with `pip install -r requirements_translator.txt`):
  - deepl
  - langdetect
  - tkinter (usually comes with Python)

## Installation

1. Make sure you have Python 3.6 or higher installed
2. Install the required packages:
   ```
   pip install -r requirements_translator.txt
   ```
3. Get a DeepL API key from [DeepL API](https://www.deepl.com/pro-api)

## Usage

1. Run the script:
   ```
   python translate_conversations.py
   ```

2. In the application:
   - Enter your DeepL API key and click "Save Key" (only needed once)
   - Select an input JSON file containing conversations
   - Choose your language detection mode (strict or relaxed)
   - Specify an output file path or use the auto-generated one
   - Click "Analyze File" to see which lines will be translated
   - Use the checkboxes to select/deselect specific lines
   - Use "Select All", "Deselect All", or "Select Only Japanese" buttons to quickly manage selections
   - Click "Translate Selected" to process only the selected lines

## Language Detection Modes

The application offers two language detection modes:

### Strict Mode (Default)
- Only detects text containing Japanese characters or other non-ASCII characters
- Ignores all text that only contains ASCII characters
- Best for when you only want to translate obviously foreign text

### Relaxed Mode
- Uses more sophisticated language detection to identify potential non-English text
- Filters out common English phrases and questions
- Uses language detection algorithms for longer phrases
- May include some English text that appears to be in another language
- Best when you want to catch more potential non-English content

## API Key Storage

The application stores your DeepL API key in a file named `.deepl_key` in the same directory as the script. This allows the key to be remembered between sessions.

## Output Format

The output file maintains the same structure as the input, with translations added in parentheses at the end of each non-English transcription.

Example:
```json
{
  "transcription": "こんにちは (Hello)"
}
```

## Notes

- The language detection prioritizes Japanese characters but works with other languages too
- The script uses a separate thread for translation to keep the UI responsive
- A small delay is added between translations to avoid hitting API rate limits 