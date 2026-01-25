import re
import argparse
from collections import Counter
import os

def load_vdf_keys(vdf_path):
    keys = []
    try:
        with open(vdf_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Match "key" "value"
                m = re.match(r'^"([^"]+)"\s+"([^"]+)"$', line)
                if m:
                    keys.append(m.group(1).lower())
    except Exception as e:
        print(f"Error reading VDF: {e}")
    return keys

def detect_suffixes(keys, min_count=10):
    suffix_counts = Counter()
    
    for key in keys:
        # Split by underscore to find potential suffix parts
        parts = key.split('_')
        if len(parts) > 1:
            # Generate cumulative suffixes from the end
            # e.g., a_b_c -> _c, _b_c
            current_suffix = ""
            for part in reversed(parts[1:]): # Skip the very first part to ensure it's a suffix
                current_suffix = "_" + part + current_suffix
                suffix_counts[current_suffix] += 1
                
    # Filter by min_count
    common_suffixes = {s: c for s, c in suffix_counts.items() if c >= min_count}
    
    return common_suffixes

def main():
    parser = argparse.ArgumentParser(description="Detect common suffixes in VDF keys")
    parser.add_argument("vdf_file", help="Path to the VDF file")
    parser.add_argument("--min", type=int, default=10, help="Minimum occurrences to report (default: 10)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.vdf_file):
        print(f"File not found: {args.vdf_file}")
        return

    print(f"Analyzing {args.vdf_file}...")
    keys = load_vdf_keys(args.vdf_file)
    print(f"Loaded {len(keys)} keys.")
    
    suffixes = detect_suffixes(keys, args.min)
    
    print(f"\nFound {len(suffixes)} suffixes occurring {args.min}+ times:")
    print("-" * 40)
    
    # Sort by length (descending) to show most specific matches first, then by count
    sorted_suffixes = sorted(suffixes.items(), key=lambda x: (-len(x[0]), -x[1]))
    
    for suffix, count in sorted_suffixes:
        print(f"{suffix:<40} : {count}")

if __name__ == "__main__":
    main()
