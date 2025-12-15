import os
import json
import argparse
from pathlib import Path
import openai
import tqdm
import time
import datetime
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import threading

# Thread-local storage for OpenAI clients
thread_local = threading.local()

def get_openai_client():
    """Get or create a thread-local OpenAI client"""
    if not hasattr(thread_local, "client"):
        api_key = load_api_key()
        thread_local.client = openai.OpenAI(api_key=api_key)
    return thread_local.client

def load_api_key():
    """Load OpenAI API key from .open_ai_key file"""
    key_path = Path.home() / ".open_ai_key"
    if not key_path.exists():
        raise FileNotFoundError(f"API key file not found at {key_path}. Please create this file with your OpenAI API key.")
    
    with open(key_path, 'r') as f:
        api_key = f.read().strip()
    
    if not api_key:
        raise ValueError("API key is empty. Please add your OpenAI API key to the .open_ai_key file.")
    
    return api_key

def load_custom_vocabulary(vocab_file=None):
    """Load custom vocabulary from a JSON file if provided"""
    if not vocab_file or not os.path.exists(vocab_file):
        return None
    
    try:
        with open(vocab_file, 'r') as f:
            vocab_data = json.load(f)
        
        # Create a prompt from the vocabulary
        if isinstance(vocab_data, list):
            # If it's a simple list of terms
            terms = ", ".join(vocab_data)
            prompt = f"Some terms you may encounter: {terms}."
        elif isinstance(vocab_data, dict):
            # If it's a dictionary with categories
            prompt_parts = []
            for category, terms in vocab_data.items():
                if isinstance(terms, list):
                    terms_str = ", ".join(terms)
                    prompt_parts.append(f"{category}: {terms_str}")
            
            prompt = "You may encounter these terms: " + "; ".join(prompt_parts) + "."
        else:
            prompt = None
        
        return prompt
    except Exception as e:
        print(f"Error loading custom vocabulary: {str(e)}")
        return None

def process_file(args):
    """Process a single file for transcription - designed for parallel execution"""
    filename, source_folder, output_folder, force_reprocess, reprocess_statuses, reprocess_status_map, file_index, total_files, progress_callback, custom_vocab_prompt, file_metadata = args
    
    # Extract metadata
    speaker = file_metadata.get("speaker")
    subject = file_metadata.get("subject")
    topic = file_metadata.get("topic")
    ping_type = file_metadata.get("ping_type")
    
    # Get the full path to the MP3 file
    full_path = os.path.join(source_folder, filename)
    
    # Determine output JSON path
    if output_folder:
        output_json_path = os.path.join(output_folder, f"{filename}.json")
    else:
        output_json_path = os.path.join(source_folder, f"{filename}.json")
    
    # Update progress
    if progress_callback:
        progress_callback(
            file=filename, 
            current=file_index, 
            total=total_files,
            status=f"Processing {file_index+1}/{total_files}: {filename}"
        )
    else:
        print(f"Processing {file_index+1}/{total_files}: {filename}")
    
    # Get file stats for date
    file_date = None
    try:
        stats = os.stat(full_path)
        if hasattr(stats, 'st_birthtime'):  # macOS and some systems
            timestamp = stats.st_birthtime
        else:  # Linux and others
            timestamp = stats.st_mtime
        file_date = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except:
        pass
    
    # Determine whether to force based on file status selection
    file_status = file_metadata.get("status")
    # If provided, derive status from map by filename stem
    mapped_status = None
    try:
        mapped_status = reprocess_status_map.get(os.path.splitext(os.path.basename(filename))[0].lower()) if reprocess_status_map else None
    except Exception:
        mapped_status = None
    effective_status = mapped_status if mapped_status is not None else file_status
    should_force = bool(
        force_reprocess or (
            reprocess_statuses and effective_status in reprocess_statuses
        )
    )

    # Check if we can use an existing transcription
    if os.path.exists(output_json_path) and not should_force:
        try:
            with open(output_json_path, 'r') as f:
                existing_transcription = json.load(f)
            
            # Extract the transcription text from the existing file
            transcription_text = ""
            
            # Try to get text from different possible formats
            if "text" in existing_transcription:
                transcription_text = existing_transcription["text"]
            elif "segments" in existing_transcription:
                # Concatenate all segment texts
                segments = existing_transcription["segments"]
                if isinstance(segments, list):
                    transcription_text = " ".join([segment.get("text", "") for segment in segments])
            
            # Return data for consolidated JSON
            result_data = {
                "status": "skipped",
                "filename": filename,
                "transcription_data": {
                    "date": file_date,
                    "voiceline_id": existing_transcription.get("voiceline_id", os.path.splitext(filename)[0]),
                    "transcription": transcription_text
                },
                "metadata": file_metadata
            }
            
            if progress_callback:
                progress_callback(status=f"Skipping {filename} (already transcribed)")
            else:
                print(f"Skipping {filename} (already transcribed)")
            
            return result_data
            
        except Exception as e:
            # If there's an error reading the existing file, we'll reprocess it
            if progress_callback:
                progress_callback(status=f"Error reading existing transcription for {filename}, will reprocess: {str(e)}")
            else:
                print(f"Error reading existing transcription for {filename}, will reprocess: {str(e)}")
    
    try:
        # Check if the file exists
        if not os.path.exists(full_path):
            error_msg = f"Error: File not found: {full_path}"
            if progress_callback:
                progress_callback(error=error_msg)
            else:
                print(error_msg)
            return {
                "status": "failed", 
                "filename": filename, 
                "error": error_msg,
                "metadata": file_metadata
            }
        
        # Get thread-local OpenAI client
        client = get_openai_client()
        
        # Transcribe the audio using OpenAI API
        with open(full_path, "rb") as audio_file:
            # Call Whisper API with the thread-local client
            # Add the custom vocabulary prompt if available
            transcription_args = {
                "model": "whisper-1",
                "file": audio_file,
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment"],
                "language": "en"
            }
            
            # Add prompt if we have custom vocabulary
            if custom_vocab_prompt:
                transcription_args["prompt"] = custom_vocab_prompt
                
            response = client.audio.transcriptions.create(**transcription_args)
            
            # Convert response to dictionary if it's not already
            if not isinstance(response, dict):
                response = response.model_dump()
        
        # Extract filename without extension to use as voiceline_id
        filename_without_ext = os.path.splitext(filename)[0]
        
        # Format the result in the requested structure
        output_data = {
            "voiceline_id": filename_without_ext,
            "timestamp": datetime.datetime.now().isoformat(),
            "segments": []
        }
        
        # Add segments with simplified structure
        for idx, segment in enumerate(response.get("segments", [])):
            output_data["segments"].append({
                "start": segment.get("start", 0),
                "end": segment.get("end", 0),
                "text": segment.get("text", ""),
                "part": idx + 1
            })
        
        # Save the transcription to a JSON file
        with open(output_json_path, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        # Return success with data for consolidated JSON
        return {
            "status": "success", 
            "filename": filename,
            "transcription_data": {
                "date": file_date,
                "voiceline_id": filename_without_ext,
                "transcription": response.get("text", "")
            },
            "metadata": file_metadata
        }
        
    except Exception as e:
        error_msg = f"Error transcribing {filename}: {str(e)}"
        if progress_callback:
            progress_callback(error=error_msg)
        else:
            print(error_msg)
        return {
            "status": "failed", 
            "filename": filename, 
            "error": error_msg,
            "metadata": file_metadata
        }

def transcribe_voice_files(input_json_path, source_folder, force_reprocess=False, progress_callback=None, output_folder=None, consolidated_json_path=None, max_workers=5, custom_vocab_file=None, reprocess_statuses=None, reprocess_status_map=None):
    """
    Transcribe all MP3 files mentioned in the JSON file using OpenAI Whisper API.
    Creates a JSON file for each MP3 with the transcription results in the specified format.
    
    Args:
        input_json_path (str): Path to the input JSON file
        source_folder (str): Path to the source folder containing the MP3 files
        force_reprocess (bool): Whether to reprocess files that already have transcriptions
        progress_callback (function): Optional callback function for progress updates
        output_folder (str, optional): Path to the output folder for transcription JSON files
        consolidated_json_path (str, optional): Path to save a consolidated JSON file with all transcriptions
        max_workers (int): Maximum number of parallel workers for transcription (default: 5)
        custom_vocab_file (str, optional): Path to a JSON file containing custom vocabulary
    
    Returns:
        dict: Statistics about the transcription process
    """
    try:
        # Validate API key first
        api_key = load_api_key()
    except Exception as e:
        error_msg = f"Error loading API key: {str(e)}"
        if progress_callback:
            progress_callback(error=error_msg)
        else:
            print(error_msg)
        return {"error": error_msg}
    
    # Load custom vocabulary if provided
    custom_vocab_prompt = load_custom_vocabulary(custom_vocab_file)
    if custom_vocab_prompt and progress_callback:
        progress_callback(status=f"Loaded custom vocabulary prompt: {custom_vocab_prompt[:100]}...")
    elif custom_vocab_prompt:
        print(f"Loaded custom vocabulary prompt: {custom_vocab_prompt[:100]}...")

    # Debug: log status map info
    status_map_size = len(reprocess_status_map) if reprocess_status_map else 0
    if progress_callback:
        progress_callback(status=f"[Debug] Received reprocess_status_map with {status_map_size} entries")
    else:
        print(f"[Debug] Received reprocess_status_map with {status_map_size} entries")
    
    # Create output folder if specified and it doesn't exist
    if output_folder:
        os.makedirs(output_folder, exist_ok=True)
        if progress_callback:
            progress_callback(status=f"Created output folder: {output_folder}")
        else:
            print(f"Created output folder: {output_folder}")
    
    # Load the input JSON file
    try:
        with open(input_json_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        error_msg = f"Error loading JSON file: {str(e)}"
        if progress_callback:
            progress_callback(error=error_msg)
        else:
            print(error_msg)
        return {"error": error_msg}
    
    # Collect all unique MP3 files with their metadata using recursive traversal

    def collect_mp3_files(node, path_keys, metadata_base):
        mp3s = []
        if isinstance(node, list):
            for file_entry in node:
                if isinstance(file_entry, dict) and 'filename' in file_entry:
                    filename = file_entry['filename']
                    original_path = file_entry.get('file_path', '')
                    status = file_entry.get('status')
                else:
                    filename = file_entry
                    original_path = ''
                    status = None
                metadata = metadata_base.copy()
                metadata["original_path"] = original_path
                if status is not None:
                    metadata["status"] = status
                mp3s.append({
                    "filename": filename,
                    "metadata": metadata,
                    "full_path_keys": list(path_keys)
                })
        elif isinstance(node, dict):
            for k, v in node.items():
                mp3s.extend(collect_mp3_files(v, path_keys + [k], metadata_base))
        return mp3s

    mp3_files_with_metadata = []
    for speaker, subjects in data.items():
        for subject, topics in subjects.items():
            mp3_files_with_metadata.extend(
                collect_mp3_files(topics, [speaker, subject], {})
            )
    
    total_files = len(mp3_files_with_metadata)
    status_msg = f"Found {total_files} unique MP3 files to transcribe"
    if progress_callback:
        progress_callback(status=status_msg, total=total_files)
    else:
        print(status_msg)
    
    # Statistics counters
    successful = 0
    failed = 0
    skipped = 0
    
    # Create a thread pool for parallel processing
    status_msg = f"Starting transcription with {max_workers} parallel workers"
    if progress_callback:
        progress_callback(status=status_msg)
    else:
        print(status_msg)
    
    # Prepare arguments for each file
    file_args = [
        (
            file_info["filename"], 
            source_folder, 
            output_folder, 
            force_reprocess,
            set(reprocess_statuses) if reprocess_statuses else None,
            reprocess_status_map if reprocess_status_map else None,
            i, 
            total_files, 
            progress_callback, 
            custom_vocab_prompt,
            file_info["metadata"]
        )
        for i, file_info in enumerate(mp3_files_with_metadata)
    ]
    
    # Process files in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(process_file, file_args))
    
    # Helper to convert all-caps to sentence case
    def to_sentence_case_if_all_caps(text):
        if not isinstance(text, str):
            return text
        stripped = text.lstrip()
        leading = text[:len(text) - len(stripped)]
        if stripped.isupper():
            # Convert to sentence case, preserve leading whitespace
            stripped = stripped.capitalize()
            if len(stripped) > 1:
                stripped = stripped[0] + stripped[1:].lower()
            return leading + stripped
        return text

    # Build a mapping from filename to transcription result
    filename_to_transcription = {}
    for result in results:
        if result["status"] == "success":
            successful += 1
        elif result["status"] == "skipped":
            skipped += 1
        else:
            failed += 1
            continue

        if consolidated_json_path and "transcription_data" in result:
            transcription_text = result["transcription_data"]["transcription"]
            transcription_text = to_sentence_case_if_all_caps(transcription_text)
            entry = {
                "filename": result["filename"],
                "date": result["transcription_data"]["date"],
                "voiceline_id": result["transcription_data"]["voiceline_id"],
                "transcription": transcription_text
            }
            # Add status from reprocess_status_map if available
            if reprocess_status_map:
                stem = os.path.splitext(os.path.basename(result["filename"]))[0].lower()
                if stem in reprocess_status_map:
                    entry["status"] = reprocess_status_map[stem]
            filename_to_transcription[result["filename"]] = entry

    # Debug: count entries with status
    entries_with_status = sum(1 for e in filename_to_transcription.values() if "status" in e)
    if progress_callback:
        progress_callback(status=f"[Debug] Added status to {entries_with_status}/{len(filename_to_transcription)} entries")
    else:
        print(f"[Debug] Added status to {entries_with_status}/{len(filename_to_transcription)} entries")

    # Recursively walk the input JSON and replace file dicts with transcription dicts
    def merge_structure_with_transcriptions(node):
        if isinstance(node, dict):
            return {k: merge_structure_with_transcriptions(v) for k, v in node.items()}
        elif isinstance(node, list):
            new_list = []
            for file_entry in node:
                if isinstance(file_entry, dict) and "filename" in file_entry:
                    fname = file_entry["filename"]
                else:
                    fname = file_entry
                if fname in filename_to_transcription:
                    new_list.append(filename_to_transcription[fname])
                else:
                    # If not found, keep the original entry
                    new_list.append(file_entry)
            return new_list
        else:
            return node

    consolidated_data = merge_structure_with_transcriptions(data)
    
    # Save consolidated JSON if requested
    if consolidated_json_path and consolidated_data:
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(os.path.abspath(consolidated_json_path)), exist_ok=True)
            
            # Save the consolidated data
            with open(consolidated_json_path, 'w') as f:
                json.dump(consolidated_data, f, indent=2)
            
            # Count total entries
            total_entries = 0
            for speaker, subjects in consolidated_data.items():
                for subject, topics in subjects.items():
                    for topic, files in topics.items():
                        if isinstance(files, dict):
                            for sub_key, sub_files in files.items():
                                total_entries += len(sub_files)
                        else:
                            total_entries += len(files)
            
            if progress_callback:
                progress_callback(status=f"Saved consolidated transcriptions to {consolidated_json_path} with {total_entries} entries in original structure")
            else:
                print(f"Saved consolidated transcriptions to {consolidated_json_path} with {total_entries} entries in original structure")
        except Exception as e:
            error_msg = f"Error saving consolidated JSON: {str(e)}"
            if progress_callback:
                progress_callback(error=error_msg)
            else:
                print(error_msg)
    
    # Final statistics
    stats = {
        "successful": successful,
        "failed": failed,
        "skipped": skipped,
        "total": total_files
    }
    
    summary = (
        f"\nTranscription complete:\n"
        f"  - Successfully transcribed: {successful}\n"
        f"  - Failed: {failed}\n"
        f"  - Skipped (already transcribed): {skipped}\n"
        f"  - Total: {total_files}"
    )
    
    if progress_callback:
        progress_callback(status=summary, complete=True, stats=stats)
    else:
        print(summary)
    
    return stats

def main():
    parser = argparse.ArgumentParser(description='Transcribe voice files using OpenAI Whisper API')
    parser.add_argument('--input-json', required=True, help='Path to the input JSON file')
    parser.add_argument('--source-folder', required=True, help='Path to the source folder containing the MP3 files')
    parser.add_argument('--output-folder', help='Path to the output folder for transcription JSON files')
    parser.add_argument('--consolidated-json', help='Path to save a consolidated JSON file with all transcriptions')
    parser.add_argument('--force', action='store_true', help='Force reprocessing of files that already have transcriptions')
    parser.add_argument('--workers', type=int, default=5, help='Number of parallel workers for transcription (default: 5)')
    parser.add_argument('--custom-vocab', help='Path to a JSON file containing custom vocabulary for better recognition')
    
    args = parser.parse_args()
    
    transcribe_voice_files(
        args.input_json,
        args.source_folder,
        args.force,
        output_folder=args.output_folder,
        consolidated_json_path=args.consolidated_json,
        max_workers=args.workers,
        custom_vocab_file=args.custom_vocab
    )

if __name__ == "__main__":
    main()
