"""
Post-processing: correct niche misclassifications using folder path as ground truth.
The model sometimes classifies shower photos as 'bathroom' — folder is more reliable.
"""
import json

FOLDER_NICHE_MAP = [
    ("walk ing shower", "shower"),
    ("walk-in-shower", "shower"),
    ("shower", "shower"),
    ("floring", "flooring"),
    ("flooring", "flooring"),
    ("hvac", "hvac"),
    ("siding", "siding"),
    ("security home", "security"),
    ("security", "security"),
    ("bathroom remodelin", "bathroom"),
    ("bathroom", "bathroom"),
]

with open("classifications.json", encoding="utf-8") as f:
    data = json.load(f)

corrected = 0
for r in data:
    file_path = r["file"].replace("\\", "/").lower()
    for folder_key, niche in FOLDER_NICHE_MAP:
        if folder_key in file_path:
            if r.get("niche") != niche:
                print(f"  FIX [{r['niche']} -> {niche}] {r['file'].split(chr(92))[-1]}")
                r["niche"] = niche
                r["_folder_corrected"] = True
                corrected += 1
            break  # first match wins

print(f"\nCorrected {corrected} files")

with open("classifications.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print("classifications.json updated.")
