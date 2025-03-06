# Character Conversation Player

This application allows you to play, transcribe, and manage audio conversations between different characters. It provides functionality to export conversations to a structured JSON format for further analysis or archiving.

## Exported JSON Format

When using the "Export All to JSON" feature, the application generates a structured JSON file with the following format:

```json
{
  "export_date": "2023-07-15T14:30:45.123456",
  "total_conversations": 25,
  "conversations": [
    {
      "conversation_id": "character1_character2_convo1_topic",
      "character1": "character1",
      "character2": "character2",
      "conversation_number": "1",
      "topic": "topic",
      "is_complete": true,
      "missing_parts": [],
      "starter": "character1",
      "summary": "Brief summary of conversation content",
      "lines": [
        {
          "part": 1,
          "variation": 1,
          "speaker": "character1",
          "filename": "character1_match_start_character1_character2_topic_convo1_1.mp3",
          "transcription": "Hello, how are you today?",
          "has_transcription": true,
          "file_creation_date": "2023-07-10T09:15:22.123456"
        },
        {
          "part": 2,
          "variation": 1,
          "speaker": "character2",
          "filename": "character2_match_start_character1_character2_topic_convo1_2.mp3",
          "transcription": "I'm doing well, thank you for asking.",
          "has_transcription": true,
          "file_creation_date": "2023-07-10T09:15:30.654321"
        }
        // Additional parts and variations...
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

- **conversation_id**: A unique identifier for the conversation (format: `character1_character2_convoNumber_topic`)
- **character1** & **character2**: The names of the characters participating in the conversation
- **conversation_number**: The numerical identifier for this conversation
- **topic**: The topic of the conversation (if available, otherwise `null`)
- **is_complete**: Boolean indicating whether the conversation has all required parts
- **missing_parts**: Array of part numbers that are missing (if conversation is incomplete)
- **starter**: The character who initiated the conversation
- **summary**: A brief summary of the conversation content (if generated)
- **lines**: Array of line objects representing each audio segment

### Line Object

Each line object represents a single audio file and contains:

- **part**: The part number within the conversation sequence
- **variation**: The variation number for this part (allows for alternative takes)
- **speaker**: The character who is speaking in this audio segment
- **filename**: The original audio filename
- **transcription**: The transcribed text content (if available)
- **has_transcription**: Boolean indicating if a transcription is available for this segment
- **file_creation_date**: ISO format timestamp of when the original audio file was created

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
        'character1': c['character1'],
        'character2': c['character2'],
        'number': c['conversation_number'],
        'topic': c['topic'],
        'complete': c['is_complete'],
        'summary': c['summary']
    }
    for c in data['conversations']
])

# Create a DataFrame for all lines/transcriptions
lines_df = pd.DataFrame([
    {
        'conversation_id': c['conversation_id'],
        'part': line['part'],
        'variation': line['variation'],
        'speaker': line['speaker'],
        'transcription': line['transcription'],
        'created_date': line['file_creation_date']
    }
    for c in data['conversations']
    for line in c['lines']
    if line['has_transcription']
])

# Example analysis: Count conversations by topic
topic_counts = conversations_df['topic'].value_counts()
print("\nConversations by topic:")
print(topic_counts)

# Example analysis: Find all conversations containing a specific word
keyword = "important"
matching_lines = lines_df[lines_df['transcription'].str.contains(keyword, case=False, na=False)]
matching_convos = conversations_df[conversations_df['id'].isin(matching_lines['conversation_id'])]
print(f"\nFound {len(matching_convos)} conversations containing '{keyword}'")
```

## Notes

- The availability of transcriptions depends on whether you chose to transcribe conversations during export
- Summaries are only generated if you selected this option during export and require an OpenAI API key
- The file creation date may not be available for all audio files depending on your system 