import os
import json
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import deepl
from pathlib import Path
import langdetect
from langdetect import detect, DetectorFactory
import time
import string

# Set seed for consistent language detection
DetectorFactory.seed = 0

# Constants
DEEPL_KEY_FILE = ".deepl_key"
DEFAULT_OUTPUT_SUFFIX = "_translated"

class TranslationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Conversation Translator")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        # Variables
        self.input_file_path = tk.StringVar()
        self.output_file_path = tk.StringVar()
        self.deepl_api_key = tk.StringVar()
        self.progress_var = tk.DoubleVar(value=0)
        self.status_var = tk.StringVar(value="Ready")
        self.strict_mode = tk.BooleanVar(value=True)  # Default to strict mode
        self.translator = None
        self.lines_to_translate = []
        self.selected_lines = []  # To track which lines are selected for translation
        
        # Load DeepL API key if exists
        self.load_api_key()
        
        # Create UI
        self.create_ui()
    
    def load_api_key(self):
        """Load DeepL API key from file if it exists"""
        key_path = Path(DEEPL_KEY_FILE)
        if key_path.exists():
            try:
                with open(key_path, 'r') as f:
                    self.deepl_api_key.set(f.read().strip())
            except Exception as e:
                print(f"Error loading API key: {e}")
    
    def save_api_key(self):
        """Save DeepL API key to file"""
        key = self.deepl_api_key.get().strip()
        if not key:
            messagebox.showerror("Error", "API key cannot be empty")
            return
        
        try:
            with open(DEEPL_KEY_FILE, 'w') as f:
                f.write(key)
            messagebox.showinfo("Success", "API key saved successfully")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save API key: {e}")
    
    def create_ui(self):
        """Create the user interface"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Configure grid
        main_frame.columnconfigure(0, weight=0)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(6, weight=1)  # Preview area
        
        # File selection section
        ttk.Label(main_frame, text="Input JSON File:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.input_file_path, width=50).grid(row=0, column=1, sticky=tk.EW, pady=5)
        ttk.Button(main_frame, text="Browse", command=self.browse_input_file).grid(row=0, column=2, padx=5, pady=5)
        
        ttk.Label(main_frame, text="Output JSON File:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.output_file_path, width=50).grid(row=1, column=1, sticky=tk.EW, pady=5)
        ttk.Button(main_frame, text="Browse", command=self.browse_output_file).grid(row=1, column=2, padx=5, pady=5)
        
        # DeepL API key section
        ttk.Label(main_frame, text="DeepL API Key:").grid(row=2, column=0, sticky=tk.W, pady=5)
        api_key_entry = ttk.Entry(main_frame, textvariable=self.deepl_api_key, width=50, show="*")
        api_key_entry.grid(row=2, column=1, sticky=tk.EW, pady=5)
        ttk.Button(main_frame, text="Save Key", command=self.save_api_key).grid(row=2, column=2, padx=5, pady=5)
        
        # Detection mode toggle
        detection_frame = ttk.Frame(main_frame)
        detection_frame.grid(row=3, column=0, columnspan=3, pady=5, sticky=tk.W)
        
        ttk.Label(detection_frame, text="Language Detection Mode:").pack(side=tk.LEFT, padx=(0, 10))
        
        strict_rb = ttk.Radiobutton(
            detection_frame, 
            text="Strict (Japanese & non-ASCII only)", 
            variable=self.strict_mode, 
            value=True
        )
        strict_rb.pack(side=tk.LEFT, padx=5)
        
        relaxed_rb = ttk.Radiobutton(
            detection_frame, 
            text="Relaxed (Detect more languages)", 
            variable=self.strict_mode, 
            value=False
        )
        relaxed_rb.pack(side=tk.LEFT, padx=5)
        
        # Action buttons
        action_frame = ttk.Frame(main_frame)
        action_frame.grid(row=4, column=0, columnspan=3, pady=10)
        
        ttk.Button(action_frame, text="Analyze File", command=self.analyze_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Translate Selected", command=self.start_translation).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Select All", command=self.select_all_lines).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Deselect All", command=self.deselect_all_lines).pack(side=tk.LEFT, padx=5)
        ttk.Button(action_frame, text="Select Only Japanese", command=self.select_only_japanese).pack(side=tk.LEFT, padx=5)
        
        # Progress bar
        ttk.Label(main_frame, text="Progress:").grid(row=5, column=0, sticky=tk.W, pady=5)
        ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100).grid(row=5, column=1, sticky=tk.EW, pady=5)
        ttk.Label(main_frame, textvariable=self.status_var).grid(row=5, column=2, padx=5, pady=5)
        
        # Preview area with checkboxes
        ttk.Label(main_frame, text="Lines to Translate:").grid(row=6, column=0, sticky=tk.NW, pady=5)
        
        # Create a frame for the preview with scrollbars
        preview_frame = ttk.Frame(main_frame)
        preview_frame.grid(row=6, column=1, columnspan=2, sticky=tk.NSEW, pady=5)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        
        # Create a canvas for scrolling
        self.preview_canvas = tk.Canvas(preview_frame)
        self.preview_canvas.grid(row=0, column=0, sticky=tk.NSEW)
        
        # Add scrollbar
        preview_scrollbar = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.preview_canvas.yview)
        preview_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.preview_canvas.configure(yscrollcommand=preview_scrollbar.set)
        
        # Create a frame inside the canvas for the checkboxes
        self.checkbox_frame = ttk.Frame(self.preview_canvas)
        self.checkbox_window = self.preview_canvas.create_window((0, 0), window=self.checkbox_frame, anchor=tk.NW)
        
        # Configure canvas scrolling
        self.checkbox_frame.bind("<Configure>", self.on_frame_configure)
        self.preview_canvas.bind("<Configure>", self.on_canvas_configure)
    
    def on_frame_configure(self, event):
        """Reset the scroll region to encompass the inner frame"""
        self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))
    
    def on_canvas_configure(self, event):
        """When the canvas changes size, update the window size"""
        self.preview_canvas.itemconfig(self.checkbox_window, width=event.width)
    
    def browse_input_file(self):
        """Open file dialog to select input JSON file"""
        file_path = filedialog.askopenfilename(
            title="Select Conversation JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if file_path:
            self.input_file_path.set(file_path)
            # Auto-generate output path
            input_path = Path(file_path)
            output_path = input_path.with_stem(f"{input_path.stem}{DEFAULT_OUTPUT_SUFFIX}")
            self.output_file_path.set(str(output_path))
    
    def browse_output_file(self):
        """Open file dialog to select output JSON file"""
        file_path = filedialog.asksaveasfilename(
            title="Save Translated JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            defaultextension=".json"
        )
        if file_path:
            self.output_file_path.set(file_path)
    
    def contains_japanese(self, text):
        """Check if text contains Japanese characters"""
        # Japanese character ranges:
        # Hiragana: U+3040-U+309F
        # Katakana: U+30A0-U+30FF
        # Kanji: U+4E00-U+9FFF
        return bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text))
    
    def is_non_english(self, text):
        """Detect if text contains non-English content with improved accuracy"""
        if not text or len(text.strip()) < 3:
            return False
            
        try:
            # First check for Japanese characters (more reliable than langdetect for short phrases)
            if self.contains_japanese(text):
                return True
                
            # Check for other non-Latin scripts
            non_latin_pattern = re.compile(r'[^\x00-\x7F\s]')
            if non_latin_pattern.search(text):
                return True
            
            # If strict mode is enabled, only consider Japanese and non-ASCII as non-English
            if self.strict_mode.get():
                return False
            
            # In relaxed mode, use additional detection methods
            # Skip common English phrases that might be misidentified
            common_english_phrases = [
                "yeah", "no", "yes", "ok", "okay", "sure", "good", "nice", "indeed", 
                "excuse me", "what", "why", "how", "when", "where", "who", "which",
                "hello", "hi", "hey", "bye", "goodbye", "thanks", "thank you",
                "sorry", "please", "welcome", "good luck", "good talk", "good job",
                "i see", "i know", "i don't", "i'm", "you're", "we're", "they're",
                "let's", "it's", "that's", "there's", "here's", "what's", "who's",
                "how's", "when's", "where's", "why's", "i'll", "you'll", "we'll",
                "they'll", "it'll", "that'll", "there'll", "here'll", "what'll",
                "who'll", "how'll", "when'll", "where'll", "why'll"
            ]
            
            # Check if the text is just a common English phrase
            if text.lower().strip() in common_english_phrases or text.lower().strip().endswith('?') or text.lower().strip().endswith('!'):
                return False
                
            # For longer phrases, use language detection
            if len(text.strip()) > 10:
                lang = detect(text)
                return lang != 'en'
            
            # For short phrases, check if it contains mostly non-English characters
            # Count alphabetic characters and check if they're all English
            alpha_chars = [c for c in text.lower() if c.isalpha()]
            if alpha_chars and all(c in string.ascii_lowercase for c in alpha_chars):
                # All alphabetic chars are ASCII, likely English
                return False
            
            # If we can't determine, assume it might be non-English in relaxed mode
            return True
            
        except Exception:
            # If detection fails, assume it's English
            return False
    
    def select_all_lines(self):
        """Select all lines in the preview"""
        for var in self.selected_lines:
            var.set(1)
    
    def deselect_all_lines(self):
        """Deselect all lines in the preview"""
        for var in self.selected_lines:
            var.set(0)
    
    def select_only_japanese(self):
        """Select only lines containing Japanese text"""
        for i, item in enumerate(self.lines_to_translate):
            if i < len(self.selected_lines):
                if self.contains_japanese(item['text']):
                    self.selected_lines[i].set(1)
                else:
                    self.selected_lines[i].set(0)
    
    def analyze_file(self):
        """Analyze the input file to find lines that need translation"""
        input_path = self.input_file_path.get()
        if not input_path:
            messagebox.showerror("Error", "Please select an input file")
            return
            
        try:
            # Clear previous results
            self.lines_to_translate = []
            self.selected_lines = []
            
            # Clear the checkbox frame
            for widget in self.checkbox_frame.winfo_children():
                widget.destroy()
            
            # Load the JSON file
            with open(input_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Find lines that need translation
            for convo_idx, conversation in enumerate(data.get('conversations', [])):
                convo_id = conversation.get('conversation_id', f"Conversation {convo_idx}")
                
                for line_idx, line in enumerate(conversation.get('lines', [])):
                    transcription = line.get('transcription', '')
                    
                    if self.is_non_english(transcription):
                        self.lines_to_translate.append({
                            'convo_idx': convo_idx,
                            'line_idx': line_idx,
                            'convo_id': convo_id,
                            'speaker': line.get('speaker', 'Unknown'),
                            'text': transcription,
                            'has_japanese': self.contains_japanese(transcription)
                        })
            
            # Create checkboxes for each line
            if self.lines_to_translate:
                # Sort lines to put Japanese first
                self.lines_to_translate.sort(key=lambda x: (not x['has_japanese'], x['convo_id']))
                
                for i, item in enumerate(self.lines_to_translate):
                    # Create a variable for the checkbox
                    var = tk.IntVar(value=1)  # Default to selected
                    self.selected_lines.append(var)
                    
                    # Create a frame for this item
                    item_frame = ttk.Frame(self.checkbox_frame)
                    item_frame.pack(fill=tk.X, padx=5, pady=2)
                    
                    # Add the checkbox
                    cb = ttk.Checkbutton(item_frame, variable=var)
                    cb.pack(side=tk.LEFT, padx=(0, 5))
                    
                    # Add language indicator
                    lang_indicator = "🇯🇵" if item['has_japanese'] else "🌐"
                    lang_label = ttk.Label(item_frame, text=lang_indicator, width=2)
                    lang_label.pack(side=tk.LEFT, padx=(0, 5))
                    
                    # Add the text
                    text_label = ttk.Label(
                        item_frame, 
                        text=f"[{item['convo_id']}] {item['speaker']}: {item['text']}",
                        wraplength=600
                    )
                    text_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
                
                mode_text = "strict" if self.strict_mode.get() else "relaxed"
                self.status_var.set(f"Found {len(self.lines_to_translate)} lines to translate ({mode_text} mode)")
            else:
                # No lines found
                no_lines_label = ttk.Label(
                    self.checkbox_frame, 
                    text="No non-English text found in the file.",
                    padding=10
                )
                no_lines_label.pack()
                self.status_var.set("No translation needed")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to analyze file: {e}")
            self.status_var.set("Analysis failed")
    
    def start_translation(self):
        """Start the translation process in a separate thread"""
        input_path = self.input_file_path.get()
        output_path = self.output_file_path.get()
        api_key = self.deepl_api_key.get()
        
        if not input_path or not output_path:
            messagebox.showerror("Error", "Please select input and output files")
            return
            
        if not api_key:
            messagebox.showerror("Error", "Please enter your DeepL API key")
            return
            
        if not self.lines_to_translate:
            self.analyze_file()
            if not self.lines_to_translate:
                messagebox.showinfo("Info", "No lines need translation")
                return
        
        # Check if any lines are selected
        selected_count = sum(var.get() for var in self.selected_lines)
        if selected_count == 0:
            messagebox.showinfo("Info", "No lines selected for translation")
            return
        
        # Initialize the translator
        try:
            self.translator = deepl.Translator(api_key)
            # Test the API key with a simple translation
            self.translator.translate_text("test", target_lang="EN-US")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to initialize DeepL translator: {e}")
            return
        
        # Start translation in a separate thread
        self.progress_var.set(0)
        self.status_var.set("Starting translation...")
        
        translation_thread = threading.Thread(target=self.perform_translation)
        translation_thread.daemon = True
        translation_thread.start()
    
    def perform_translation(self):
        """Perform the actual translation (run in a separate thread)"""
        try:
            # Load the JSON file
            with open(self.input_file_path.get(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Filter lines based on checkbox selection
            selected_lines = [
                item for i, item in enumerate(self.lines_to_translate)
                if i < len(self.selected_lines) and self.selected_lines[i].get() == 1
            ]
            
            total_lines = len(selected_lines)
            
            for i, item in enumerate(selected_lines):
                # Update progress
                progress = (i / total_lines) * 100 if total_lines > 0 else 100
                self.root.after(0, lambda p=progress: self.progress_var.set(p))
                self.root.after(0, lambda s=f"Translating {i+1}/{total_lines}": self.status_var.set(s))
                
                # Get the line to translate
                convo_idx = item['convo_idx']
                line_idx = item['line_idx']
                text = item['text']
                
                # Translate the text
                try:
                    translation = self.translator.translate_text(text, target_lang="EN-US").text
                    
                    # Update the transcription with the translation
                    original_text = data['conversations'][convo_idx]['lines'][line_idx]['transcription']
                    data['conversations'][convo_idx]['lines'][line_idx]['transcription'] = f"{original_text} ({translation})"
                    
                    # Small delay to avoid hitting API rate limits
                    time.sleep(0.5)
                except Exception as e:
                    print(f"Translation error for '{text}': {e}")
            
            # Save the updated data
            with open(self.output_file_path.get(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Update UI
            self.root.after(0, lambda: self.progress_var.set(100))
            self.root.after(0, lambda: self.status_var.set("Translation complete"))
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Translation complete. Saved to {self.output_file_path.get()}"))
            
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set("Translation failed"))
            self.root.after(0, lambda: messagebox.showerror("Error", f"Translation failed: {e}"))

def main():
    root = tk.Tk()
    app = TranslationApp(root)
    root.mainloop()

if __name__ == "__main__":
    main() 