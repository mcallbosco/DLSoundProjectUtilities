import os
import json
import shutil
import argparse
from pathlib import Path
import datetime

def get_file_date(file_path):
    """
    Get the creation or modification date of a file.
    Returns the date in ISO format (YYYY-MM-DD).
    
    Args:
        file_path (str): Path to the file
    
    Returns:
        str: Date in ISO format
    """
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
        return date_obj.strftime("%Y-%m-%d")
    except Exception as e:
        print(f"Error getting date for {file_path}: {str(e)}")
        return None

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
    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)
    
    # Set default output JSON path if not provided
    if output_json_path is None:
        input_path = Path(input_json_path)
        output_json_path = str(input_path.parent / f"{input_path.stem}_flat{input_path.suffix}")
    
    # Load the input JSON file
    with open(input_json_path, 'r') as f:
        data = json.load(f)
    
    # Create a new data structure with filenames and dates
    flat_data = {}
    
    # Keep track of copied files to avoid duplicates
    copied_files = set()
    
    # Process each speaker
    for speaker, subjects in data.items():
        flat_data[speaker] = {}
        
        # Process each subject
        for subject, topics in subjects.items():
            flat_data[speaker][subject] = {}
            
            # Process each topic
            for topic, files in topics.items():
                # Handle special case for "Pings" category
                if topic == "Pings":
                    flat_data[speaker][subject][topic] = {}
                    for ping_type, ping_files in files.items():
                        flat_data[speaker][subject][topic][ping_type] = []
                        for file_path in ping_files:
                            # Get just the filename
                            filename = os.path.basename(file_path)
                            
                            # Get the full source path
                            source_path = os.path.join(source_folder, file_path)
                            
                            # Get file date
                            file_date = get_file_date(source_path)
                            
                            # Add to the flat data with date
                            file_entry = {
                                "filename": filename,
                                "date": file_date
                            }
                            flat_data[speaker][subject][topic][ping_type].append(file_entry)
                            
                            # Copy the file if not already copied
                            if filename not in copied_files:
                                dest_path = os.path.join(output_folder, filename)
                                try:
                                    shutil.copy2(source_path, dest_path)
                                    copied_files.add(filename)
                                    print(f"Copied: {filename}")
                                except Exception as e:
                                    print(f"Error copying {filename}: {str(e)}")
                else:
                    flat_data[speaker][subject][topic] = []
                    for file_path in files:
                        # Get just the filename
                        filename = os.path.basename(file_path)
                        
                        # Get the full source path
                        source_path = os.path.join(source_folder, file_path)
                        
                        # Get file date
                        file_date = get_file_date(source_path)
                        
                        # Add to the flat data with date
                        file_entry = {
                            "filename": filename,
                            "date": file_date
                        }
                        flat_data[speaker][subject][topic].append(file_entry)
                        
                        # Copy the file if not already copied
                        if filename not in copied_files:
                            source_path = os.path.join(source_folder, file_path)
                            dest_path = os.path.join(output_folder, filename)
                            try:
                                shutil.copy2(source_path, dest_path)
                                copied_files.add(filename)
                                print(f"Copied: {filename}")
                            except Exception as e:
                                print(f"Error copying {filename}: {str(e)}")
    
    # Save the flat data to the output JSON file
    with open(output_json_path, 'w') as f:
        json.dump(flat_data, f, indent=2)
    
    print(f"\nCopied {len(copied_files)} unique files to {output_folder}")
    print(f"Generated flat JSON file with dates at {output_json_path}")

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