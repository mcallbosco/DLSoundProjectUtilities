import os
import json
import shutil
import argparse
from pathlib import Path
import datetime

DEBUG_LOGGING_ENABLED = False

def log_debug(message):
	if DEBUG_LOGGING_ENABLED:
		print(message)

def get_file_date(file_path):
    """
    Get the creation or modification date of a file.
    Returns the date in ISO format (YYYY-MM-DD).
    
    Args:
        file_path (str): Path to the file
    
    Returns:
        str: Date in ISO format
    """
    log_debug(f"[DEBUG] Entering get_file_date for: {file_path}")
    try:
        # Get file stats
        stats = os.stat(file_path)
        
        # Try to get creation time first (Windows), fall back to modification time
        if hasattr(stats, 'st_birthtime'):  # macOS and some systems
            timestamp = stats.st_birthtime
        else:  # Linux and others
            timestamp = stats.st_mtime
        
        # Convert timestamp to datetime and format as ISO date
        date_obj = datetime.datetime.fromtimestamp(timestamp)
        log_debug(f"[DEBUG] Got date {date_obj.strftime('%Y-%m-%d')} for {file_path}")
        return date_obj.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Error getting date for {file_path}: {str(e)}")
        return None
    finally:
        log_debug(f"[DEBUG] Exiting get_file_date for: {file_path}")

def copy_voice_files(input_json_path, source_folder, output_folder, output_json_path=None):
    """
    Copy all MP3 files mentioned in the JSON file to a separate folder and
    generate a new version of the JSON with only the filenames and their dates.
    
    Args:
        input_json_path (str): Path to the input JSON file
        source_folder (str): Path to the source folder containing the MP3 files
        output_folder (str): Path to the output folder where files will be copied
        output_json_path (str, optional): Path to the output JSON file. If None, will use input_json_path with '_flat' suffix.
    """
    log_debug(f"[DEBUG] Entering copy_voice_files with input_json_path={input_json_path}, source_folder={source_folder}, output_folder={output_folder}, output_json_path={output_json_path}")
    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)
    log_debug(f"[DEBUG] Ensured output folder exists: {output_folder}")
    
    # Set default output JSON path if not provided
    if output_json_path is None:
        input_path = Path(input_json_path)
        output_json_path = str(input_path.parent / f"{input_path.stem}_flat{input_path.suffix}")
        log_debug(f"[DEBUG] Set default output_json_path: {output_json_path}")
    
    # Load the input JSON file
    log_debug(f"[DEBUG] Loading input JSON file: {input_json_path}")
    with open(input_json_path, 'r') as f:
        data = json.load(f)
    log_debug(f"[DEBUG] Loaded input JSON file successfully.")
    
    # Create a new data structure with filenames and dates
    flat_data = {}
    
    # Keep track of copied files to avoid duplicates
    copied_files = set()
    
    def process_and_copy(node):
        # Recursively mirror the structure, replacing file path strings with dicts containing filename/date
        if isinstance(node, dict):
            return {k: process_and_copy(v) for k, v in node.items()}
        elif isinstance(node, list):
            return [process_and_copy(item) for item in node]
        elif isinstance(node, str):
            filename = os.path.basename(node)
            source_path = os.path.join(source_folder, node)
            file_date = get_file_date(source_path)
            if filename not in copied_files:
                dest_path = os.path.join(output_folder, filename)
                try:
                    log_debug(f"[DEBUG]         Copying file {filename} to {dest_path}")
                    shutil.copy2(source_path, dest_path)
                    copied_files.add(filename)
                    print(f"Copied: {filename}")
                except Exception as e:
                    print(f"Error copying {filename}: {str(e)}")
            return {"filename": filename, "date": file_date}
        elif isinstance(node, (int, float)):
            return node
        elif isinstance(node, object) and hasattr(node, "get") and "filename" in node:
            return node
        else:
            return node

    # Process each speaker (mirror the full structure)
    flat_data = process_and_copy(data)
    
    # Save the flat data to the output JSON file
    log_debug(f"[DEBUG] Saving flat data to output JSON file: {output_json_path}")
    with open(output_json_path, 'w') as f:
        json.dump(flat_data, f, indent=2)
    log_debug(f"[DEBUG] Saved flat data to output JSON file successfully.")
    
    print(f"\nCopied {len(copied_files)} unique files to {output_folder}")
    print(f"Generated flat JSON file with dates at {output_json_path}")
    log_debug(f"[DEBUG] Exiting copy_voice_files")

def main():
    parser = argparse.ArgumentParser(description='Copy voice files and create a flat JSON structure with file dates')
    parser.add_argument('--input-json', required=True, help='Path to the input JSON file')
    parser.add_argument('--source-folder', required=True, help='Path to the source folder containing the MP3 files')
    parser.add_argument('--output-folder', required=True, help='Path to the output folder where files will be copied')
    parser.add_argument('--output-json', help='Path to the output JSON file (optional)')
    
    args = parser.parse_args()
    
    copy_voice_files(
        args.input_json,
        args.source_folder,
        args.output_folder,
        args.output_json
    )

if __name__ == "__main__":
    main()
