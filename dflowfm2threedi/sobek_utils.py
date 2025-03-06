import re
from pathlib import Path
from typing import List


def get_id_value(text):
    match = re.search(r"\bid\b\s*['\"]?([^'\"]+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def deduplicate_friction_file(input_file, output_file):
    """
    Only keeps one friction entry for each ID.
    If there are multiple entries for 1 ID, only the last entry in the file is preserved.
    """

    with open(input_file, "r", encoding="utf-8") as f:
        content = f.readlines()

    records = dict()
    record_lines = list()
    glfr = list()
    start_pattern = r"^(BDFR|GLFR|STFR|CRFR)"
    end_pattern = "*******"
    for line in content:
        line = line.strip("\n")
        if len(record_lines) == 0 and re.match(start_pattern, line):
            end_pattern = line[:4].lower()
        record_lines.append(line)
        if line.endswith(end_pattern):
            record = "\n".join(record_lines)
            if end_pattern == "GLFR":
                glfr.append(end_pattern)
            else:
                record_id = get_id_value(record)
                records[record_id] = record
            record_lines = list()

    # Write cleaned file with unique records
    with open(output_file, "w", encoding="utf-8") as f:
        for record in glfr:
            f.write(record)
            f.write("\n")
        for record in records.values():
            f.write(record)
            f.write("\n")


# Example usage
if __name__ == "__main__":
    case_dir = Path(r"D:\Sobek216\MySobekModel.lit\2")
    deduplicate_friction_file(case_dir / "FRICTION.DAT", case_dir / "FRICTION_deduplicated.DAT")
    # Don't forget the rename or delete FRICTION.DAT and than rename FRICTION_deduplicated.DAT to FRICTION.DAT