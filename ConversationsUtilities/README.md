# Character Conversation Player

This application allows you to play, transcribe, and manage audio conversations between different characters. It provides functionality to export conversations to a structured JSON format for further analysis or archiving.

## Exported JSON Format

When using the "Export All to JSON" feature, the application generates a structured JSON file with the following format:

```json
{
  "export_date": "2025-05-29T21:55:00.000000",
  "total_conversations": 25,
  "conversations": [
    {
      "conversation_id": "holliday_ivy_convo01",
      "speakers": ["holliday", "ivy"],
      "convo_id": "01",
      "is_complete": true,
      "missing_parts": [],
      "status": ["UPDATED"],
      "lines": [
        {
          "part": 1,
          "speaker": "holliday",
          "filename": "2-Winston-000000060EAE.0B2-Almond butter_.mp3",
          "transcript": "Hello, how are you today?",
          "has_transcript": true,
          "file_creation_date": "2025-05-29T09:15:22.123456"
        }
        // Additional parts...
      ]
    }
    // Additional conversations...
  ]
}
```

### Top-Level Structure

- **export_date**: ISO format timestamp of when the export was created
- **total_conversations**: The total number of conversations in the file
- **conversations**: An array of conversation objects

### Conversation Object

Each conversation object contains:

- **conversation_id**: A unique identifier for the conversation (format: `character1_character2_convoNumber`)
- **speakers**: The names of the characters participating in the conversation
- **convo_id**: The identifier for this conversation
- **is_complete**: Boolean indicating whether the conversation has all required parts
- **missing_parts**: Array of part numbers that are missing (if conversation is incomplete)
- **status**: List of unique statuses for the conversation, as determined from the status file (empty if none)
- **lines**: Array of line objects representing each audio segment

### Line Object

Each line object represents a single audio file and contains:

- **part**: The part number within the conversation sequence
- **speaker**: The character who is speaking in this audio segment
- **filename**: The audio filename (exported as .mp3)
- **transcript**: The transcribed text content (if available)
- **has_transcript**: Boolean indicating if a transcription is available for this segment
- **file_creation_date**: ISO format timestamp of when the original audio file was created

## Status File Support

You can now add a "status" field to each conversation in the exported JSON by providing a status file. The status file should be a plain text file with each line containing a filename (relative path or just the filename), CRC, size, and a status at the end. Lines starting with `#` are treated as comments and ignored.

**Example status file:**
```
# This is a comment
HeroConvo/Athena/000000000B72.0D0/2-Winston-000000060EAE.0B2-Almond butter_.ogg CRC:6162b0ab size:22121 UPDATED
```

If any line in a conversation matches a filename in the status file, the conversation's "status" field will include the corresponding status (e.g., "UPDATED"). If multiple statuses are found for a conversation, all unique statuses are included.

### How to Use

1. Click the **Select Status File** button in the application and choose your status text file.
2. Load your audio files and export as usual.
3. The exported JSON will include a "status" field for each conversation.

## Working with the JSON Data

### Use Cases

1. **Analysis**: The structured format allows for analyzing conversations between specific characters or about particular topics
2. **Visualization**: Create conversation flow diagrams or charts
3. **Search**: Build a searchable database of character dialogues
4. **Training**: Use as training data for AI models to generate new conversations
5. **Archiving**: Preserve conversations in a structured format for long-term storage

### Processing Examples

#### Python Example

```python
import json
import pandas as pd

# Load the exported JSON file
with open('all_conversations.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Print basic statistics
print(f"Total conversations: {data['total_conversations']}")
print(f"Export date: {data['export_date']}")

# Create a DataFrame for conversation metadata
conversations_df = pd.DataFrame([
    {
        'id': c['conversation_id'],
        'speakers': c['speakers'],
        'convo_id': c['convo_id'],
        'status': c.get('status', []),
        'complete': c['is_complete'],
    }
    for c in data['conversations']
])

# Create a DataFrame for all lines/transcriptions
lines_df = pd.DataFrame([
    {
        'conversation_id': c['conversation_id'],
        'part': line['part'],
        'speaker': line['speaker'],
        'transcript': line['transcript'],
        'created_date': line['file_creation_date']
    }
    for c in data['conversations']
    for line in c['lines']
    if line['has_transcript']
])

# Example analysis: Count conversations by status
from collections import Counter
status_counts = Counter(s for c in data['conversations'] for s in c.get('status', []))
print("\nConversations by status:")
print(status_counts)
```

## Notes

- The availability of transcriptions depends on whether you chose to transcribe conversations during export
- The file creation date may not be available for all audio files depending on your system
- The "status" field is only present if you select a status file before export
- The JSON structure may change as new features are added; see this README for the latest format
