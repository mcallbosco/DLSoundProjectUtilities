import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os

class TranscriptionDeleterApp:
    def __init__(self, master):
        self.master = master
        master.title("Transcription File Deleter")
        master.geometry("750x600")

        self.input_text_file_path_sv = tk.StringVar()
        self.transcriptions_folder_path_sv = tk.StringVar()

        # --- Configuration Frame ---
        config_frame = tk.LabelFrame(master, text="Configuration", padx=10, pady=10)
        config_frame.pack(fill=tk.X, padx=10, pady=10)

        # Input TXT selection
        tk.Label(config_frame, text="Changed Files List (input.txt):").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self.input_text_file_entry = tk.Entry(config_frame, textvariable=self.input_text_file_path_sv, width=65, state='readonly')
        self.input_text_file_entry.grid(row=0, column=1, padx=5, pady=3)
        tk.Button(config_frame, text="Browse...", command=self.browse_input_text_file).grid(row=0, column=2, padx=5, pady=3)

        # Transcriptions folder selection
        tk.Label(config_frame, text="Transcriptions Folder:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self.transcriptions_folder_entry = tk.Entry(config_frame, textvariable=self.transcriptions_folder_path_sv, width=65, state='readonly')
        self.transcriptions_folder_entry.grid(row=1, column=1, padx=5, pady=3)
        tk.Button(config_frame, text="Browse...", command=self.browse_transcriptions_folder).grid(row=1, column=2, padx=5, pady=3)

        # Only process UPDATED entries option
        self.only_updated_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            config_frame,
            text="Only delete entries marked UPDATED",
            variable=self.only_updated_var
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=5, pady=3)

        # --- Action Frame ---
        action_frame = tk.Frame(master, padx=10, pady=10)
        action_frame.pack(fill=tk.X)

        self.process_button = tk.Button(action_frame, text="Find & Delete Transcriptions", command=self.process_and_delete_transcriptions, width=25, height=2)
        self.process_button.pack(side=tk.LEFT, padx=5)

        self.clear_log_button = tk.Button(action_frame, text="Clear Log", command=self.clear_log, width=15)
        self.clear_log_button.pack(side=tk.RIGHT, padx=5)

        # --- Log Frame ---
        log_frame = tk.LabelFrame(master, text="Log", padx=10, pady=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.log_text_area = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=15, width=88)
        self.log_text_area.pack(fill=tk.BOTH, expand=True)
        self.log_text_area.configure(state='disabled')

    def _log_message(self, message, clear_first=False):
        self.log_text_area.configure(state='normal')
        if clear_first:
            self.log_text_area.delete(1.0, tk.END)
        self.log_text_area.insert(tk.END, message + "\n")
        self.log_text_area.see(tk.END)
        self.log_text_area.configure(state='disabled')

    def browse_input_text_file(self):
        filepath = filedialog.askopenfilename(
            title="Select input.txt (Changed Files List)",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*"))
        )
        if filepath:
            self.input_text_file_path_sv.set(filepath)
            self._log_message(f"Selected input TXT file: {filepath}")

    def browse_transcriptions_folder(self):
        folderpath = filedialog.askdirectory(
            title="Select Transcriptions Folder"
        )
        if folderpath:
            self.transcriptions_folder_path_sv.set(folderpath)
            self._log_message(f"Selected transcriptions folder: {folderpath}")

    def clear_log(self):
        self._log_message("", clear_first=True)

    def process_and_delete_transcriptions(self):
        self.clear_log()
        input_txt_file = self.input_text_file_path_sv.get()
        trans_folder = self.transcriptions_folder_path_sv.get()

        if not input_txt_file:
            messagebox.showerror("Error", "Please select the input.txt file.")
            self._log_message("Error: input.txt file not selected.")
            return
        if not trans_folder:
            messagebox.showerror("Error", "Please select the transcriptions folder.")
            self._log_message("Error: Transcriptions folder not selected.")
            return

        self._log_message(f"Processing input TXT file: {input_txt_file}")
        self._log_message(f"Looking for transcriptions in: {trans_folder}")
        self._log_message(f"Filter: {'UPDATED only' if self.only_updated_var.get() else 'All entries'}")

        changed_lines_from_file = []
        try:
            with open(input_txt_file, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line:
                        changed_lines_from_file.append(stripped_line)
        except FileNotFoundError:
            messagebox.showerror("Error", f"File not found: {input_txt_file}")
            self._log_message(f"Error: File not found: {input_txt_file}")
            return
        except Exception as e:
            messagebox.showerror("Error", f"An error occurred reading {input_txt_file}: {e}")
            self._log_message(f"Error reading {input_txt_file}: {e}")
            return

        if not changed_lines_from_file:
            self._log_message("No changed file lines found in input.txt. Nothing to do.")
            messagebox.showinfo("Info", "The input.txt file is empty or contains no processable lines.")
            return

        files_to_delete = []
        self._log_message("\nIdentifying transcription files to delete:")
        for line_entry in changed_lines_from_file:
            # Optionally filter by UPDATED status
            try:
                status_token = line_entry.split()[-1]
            except Exception:
                status_token = ""
            if self.only_updated_var.get() and status_token != "UPDATED":
                continue
            parts = line_entry.split(" ", 1)
            if not parts:
                self._log_message(f"Warning: Skipping empty or invalid line in input.txt: '{line_entry}'")
                continue
            
            vsnd_path = parts[0]

            if not vsnd_path.lower().endswith(".vsnd_c"):
                 self._log_message(f"Warning: Line does not appear to start with a .vsnd_c path: '{line_entry}'")
                 continue
            
            try:
                base_vsnd_name = os.path.basename(vsnd_path)
                name_without_ext, _ = os.path.splitext(base_vsnd_name)
                transcription_filename = f"{name_without_ext}.mp3.json"
                full_transcription_path = os.path.join(trans_folder, transcription_filename)

                if os.path.exists(full_transcription_path):
                    files_to_delete.append(full_transcription_path)
                    self._log_message(f"  Found: {full_transcription_path} (from {vsnd_path})")
                else:
                    self._log_message(f"  Not found: {full_transcription_path} (from {vsnd_path})")
            except Exception as e:
                self._log_message(f"Error processing line '{line_entry}' (extracted path: '{vsnd_path}'): {e}")

        if not files_to_delete:
            self._log_message("\nNo matching transcription files found to delete.")
            messagebox.showinfo("Info", "No corresponding transcription files were found in the selected folder.")
            return

        self._log_message("\nDEBUG: Preparing confirmation dialog.")
        self._log_message(f"DEBUG: Files identified for deletion count: {len(files_to_delete)}")

        # --- MODIFIED: Shorten the confirmation message ---
        num_files_to_delete = len(files_to_delete)
        confirm_message_summary = f"{num_files_to_delete} transcription file(s) are identified for DELETION.\n\n"
        if self.only_updated_var.get():
            confirm_message_summary = "Filtering by status: UPDATED only\n" + confirm_message_summary
        if num_files_to_delete > 0:
            confirm_message_summary += "Examples:\n"
            for i, f_path in enumerate(files_to_delete):
                if i < 3: # Show up to 3 examples
                    confirm_message_summary += f"- {os.path.basename(f_path)}\n"
                else:
                    if num_files_to_delete > 3:
                        confirm_message_summary += f"...and {num_files_to_delete - 3} more.\n"
                    break
        confirm_message_summary += "\nAre you sure you want to proceed?\n(Full list is in the log above)"
        
        self._log_message(f"DEBUG: Confirmation summary to be shown: \n{confirm_message_summary}")

        user_response = None # Initialize to handle cases where dialog might not even return True/False
        try:
            # Using the shortened message for the dialog
            user_response = messagebox.askyesno("Confirm Deletion", confirm_message_summary)
            self._log_message(f"DEBUG: User response from askyesno: {user_response} (True means Yes, False means No, None means dialog closed/failed)")
        except Exception as e:
            # Catch any exception during the dialog call itself
            self._log_message(f"CRITICAL: Exception during messagebox.askyesno: {e}")
            messagebox.showerror("Dialog Error", f"Could not display confirmation dialog: {e}\n\nDeletion cancelled.")
            user_response = False # Treat as cancellation if dialog critically fails

        # Explicitly check for True. False or None (dialog closed/failed) will go to else.
        if user_response is True:
            self._log_message("\nUser confirmed deletion. Proceeding...")
            deleted_count = 0
            failed_count = 0
            for file_path_to_delete in files_to_delete:
                try:
                    os.remove(file_path_to_delete)
                    self._log_message(f"  DELETED: {file_path_to_delete}")
                    deleted_count += 1
                except OSError as e:
                    self._log_message(f"  FAILED to delete: {file_path_to_delete} - Error: {e}")
                    failed_count += 1
            self._log_message(f"\nDeletion process completed. {deleted_count} file(s) deleted, {failed_count} failed.")
            messagebox.showinfo("Deletion Complete", f"{deleted_count} file(s) deleted.\n{failed_count} deletion(s) failed (check log).")
        else:
            # This block now handles False from "No" button, or None if dialog was closed (e.g. 'X'),
            # or False if the dialog itself had an exception.
            self._log_message("\nUser cancelled deletion or dialog failed. No files were deleted.")
            # Only show the "Cancelled by user" if the dialog didn't critically fail with an exception
            # (as an error message would have already been shown in that case).
            if user_response is not None and not isinstance(user_response, Exception): # Check if it wasn't a critical failure
                 messagebox.showinfo("Cancelled", "Deletion process cancelled by user.")

if __name__ == "__main__":
    root = tk.Tk()
    app = TranscriptionDeleterApp(root)
    root.mainloop()
