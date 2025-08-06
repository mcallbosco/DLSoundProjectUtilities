import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

def parse_file(filepath):
    """Parses a file to extract voice line paths and their CRCs."""
    voicelines = {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line.startswith("sounds/vo/"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                path = parts[0]
                crc_part = [p for p in parts if p.startswith("CRC:")]
                if not crc_part:
                    continue
                crc = crc_part[0][4:]
                voicelines[path] = crc
    except FileNotFoundError:
        messagebox.showerror("Error", f"File not found: {filepath}")
        return None
    except Exception as e:
        messagebox.showerror("Error", f"An error occurred while parsing {filepath}: {e}")
        return None
    return voicelines

def find_changed_voicelines(before_file, after_file):
    """
    Compares two files and finds new or changed voice lines in the after_file.
    """
    before_data = parse_file(before_file)
    # No need to parse after_data fully here, just need before_data for comparison
    # The main iteration will happen on the after_file directly.

    if before_data is None: # Error occurred in parsing before_file
        return None

    changed_lines = []
    try:
        with open(after_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line.startswith("sounds/vo/"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                path = parts[0]
                crc_part = [p for p in parts if p.startswith("CRC:")]
                if not crc_part:
                    continue
                current_crc = crc_part[0][4:]

                # Check if the path is new or if the CRC has changed
                if path not in before_data:
                    changed_lines.append(line + " ADDED")
                elif before_data[path] != current_crc:
                    changed_lines.append(line + " UPDATED")
    except FileNotFoundError:
        messagebox.showerror("Error", f"File not found during comparison: {after_file}")
        return None
    except Exception as e:
        messagebox.showerror("Error", f"An error occurred during comparison: {e}")
        return None
    return changed_lines

class VoiceLineComparerApp:
    def __init__(self, master):
        self.master = master
        master.title("Voice Line Comparer")
        master.geometry("750x650") # Adjusted size for new elements

        self.before_file_path_sv = tk.StringVar()
        self.after_file_path_sv = tk.StringVar()
        self.output_file_path_sv = tk.StringVar() # For the chosen output file path
        self.save_to_file_var = tk.BooleanVar(value=True)

        # --- File Selection Frame ---
        file_frame = tk.LabelFrame(master, text="Input Files", padx=10, pady=10)
        file_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(file_frame, text="Before File:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.before_entry = tk.Entry(file_frame, textvariable=self.before_file_path_sv, width=60, state='readonly')
        self.before_entry.grid(row=0, column=1, padx=5, pady=2)
        tk.Button(file_frame, text="Browse...", command=self.browse_before_file).grid(row=0, column=2, padx=5, pady=2)

        tk.Label(file_frame, text="After File:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.after_entry = tk.Entry(file_frame, textvariable=self.after_file_path_sv, width=60, state='readonly')
        self.after_entry.grid(row=1, column=1, padx=5, pady=2)
        tk.Button(file_frame, text="Browse...", command=self.browse_after_file).grid(row=1, column=2, padx=5, pady=2)

        # --- Output File Settings Frame ---
        output_settings_frame = tk.LabelFrame(master, text="Output File Settings", padx=10, pady=10)
        output_settings_frame.pack(fill=tk.X, padx=10, pady=5)

        self.save_checkbox = tk.Checkbutton(output_settings_frame, text="Save output to file", variable=self.save_to_file_var, command=self.toggle_output_file_options)
        self.save_checkbox.grid(row=0, column=0, columnspan=3, sticky="w", padx=5, pady=2)

        tk.Label(output_settings_frame, text="Output File:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.output_file_entry = tk.Entry(output_settings_frame, textvariable=self.output_file_path_sv, width=60, state='readonly')
        self.output_file_entry.grid(row=1, column=1, padx=5, pady=2)
        self.set_output_button = tk.Button(output_settings_frame, text="Set Output File...", command=self.set_output_file)
        self.set_output_button.grid(row=1, column=2, padx=5, pady=2)


        # --- Controls Frame ---
        controls_frame = tk.Frame(master, padx=10, pady=10)
        controls_frame.pack(fill=tk.X)

        self.compare_button = tk.Button(controls_frame, text="Compare Voice Lines", command=self.compare_files, width=20, height=2)
        self.compare_button.pack(side=tk.LEFT, padx=5)

        self.clear_button = tk.Button(controls_frame, text="Clear Output Log", command=self.clear_output_log, width=15)
        self.clear_button.pack(side=tk.RIGHT, padx=5)


        # --- Output Log Frame ---
        output_log_frame = tk.LabelFrame(master, text="Output Log", padx=10, pady=10)
        output_log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.output_text_area = scrolledtext.ScrolledText(output_log_frame, wrap=tk.WORD, height=15, width=80)
        self.output_text_area.pack(fill=tk.BOTH, expand=True)
        self.output_text_area.configure(state='disabled')

        self.toggle_output_file_options() # Initialize state of output file options


    def browse_before_file(self):
        filepath = filedialog.askopenfilename(
            title="Select Before File",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*"))
        )
        if filepath:
            self.before_file_path_sv.set(filepath)

    def browse_after_file(self):
        filepath = filedialog.askopenfilename(
            title="Select After File",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*"))
        )
        if filepath:
            self.after_file_path_sv.set(filepath)

    def set_output_file(self):
        filepath = filedialog.asksaveasfilename(
            title="Save Output As",
            defaultextension=".txt",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
            initialfile="output.txt" # Suggest a default filename
        )
        if filepath:
            self.output_file_path_sv.set(filepath)

    def toggle_output_file_options(self):
        """Enable/disable output file entry and button based on checkbox."""
        if self.save_to_file_var.get():
            self.output_file_entry.configure(state='readonly') # Keep readonly, path set by button
            self.set_output_button.configure(state='normal')
        else:
            self.output_file_entry.configure(state='disabled')
            self.set_output_button.configure(state='disabled')


    def clear_output_log(self):
        self.output_text_area.configure(state='normal')
        self.output_text_area.delete(1.0, tk.END)
        self.output_text_area.configure(state='disabled')

    def _log_message(self, message):
        self.output_text_area.configure(state='normal')
        self.output_text_area.insert(tk.END, message + "\n")
        self.output_text_area.configure(state='disabled')
        self.output_text_area.see(tk.END) # Scroll to the end

    def compare_files(self):
        before_fp = self.before_file_path_sv.get()
        after_fp = self.after_file_path_sv.get()

        if not before_fp or not after_fp:
            messagebox.showerror("Error", "Please select both 'Before' and 'After' files.")
            return

        self.clear_output_log() # Clear previous log
        self._log_message("Starting comparison...")

        changed_voicelines = find_changed_voicelines(before_fp, after_fp)

        if changed_voicelines is None: # An error occurred in parsing or comparison
            self._log_message("Comparison failed. Check error pop-ups.")
            return

        if changed_voicelines:
            output_str = "\n".join(changed_voicelines)
            self._log_message("--- Changed/New Voice Lines ---")
            self._log_message(output_str)

            if self.save_to_file_var.get():
                output_save_path = self.output_file_path_sv.get()
                if not output_save_path:
                    self._log_message("\nOutput file path not set. Prompting for location...")
                    self.set_output_file() # Prompt user to set it now
                    output_save_path = self.output_file_path_sv.get() # Get it again

                if output_save_path:
                    try:
                        with open(output_save_path, 'w', encoding='utf-8') as outfile:
                            outfile.write(output_str + "\n")
                        self._log_message(f"\n--- Output also saved to: {output_save_path} ---")
                    except Exception as e:
                        messagebox.showerror("Save Error", f"Could not save to {output_save_path}: {e}")
                        self._log_message(f"\n--- Failed to save output to: {output_save_path} ---")
                else:
                    self._log_message("\n--- Saving to file skipped (no output file selected) ---")
        else:
            self._log_message("No new or changed voice lines found.")

        self._log_message("\nComparison finished.")


if __name__ == "__main__":
    root = tk.Tk()
    app = VoiceLineComparerApp(root)
    root.mainloop()
