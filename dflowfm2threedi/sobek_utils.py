import csv
import re
from dataclasses import dataclass, fields
from pathlib import Path
from pprint import pprint
from typing import List, get_type_hints


class Table(str):
    pass


@dataclass
class TimeController:
    id: str
    nm: str = None
    ct: str = None
    ca: int = None
    ac: str = None
    cf: str = None
    ta: str = None
    gi: str = None
    ao: str = None
    mc: str = None
    bl: str = None
    ti9tv: str = None  # 9 is interpreted as space
    pdin: Table = None
    tble: Table = None


@dataclass
class HydraulicController:
    id: str
    nm: str = None
    ta: str = None
    gi: str = None
    ao: str = None
    ct: str = None
    ac: str = None
    ca: int = None
    cf: str = None
    ml: str = None
    mp: str = None
    cb: str = None
    cl: str = None
    cp: str = None
    b1: str = None
    hc9ht91: Table = None  # 9 is interpreted as space
    bl: str = None
    ci: str = None
    ps: str = None
    ns: str = None


@dataclass
class IntervalController:
    id: str
    nm: str = None
    ta: str = None
    gi: str = None
    ao: str = None
    ct: str = None
    ac: str = None
    ca: int = None
    cf: str = None
    cb: str = None
    cl: str = None
    ml: str = None
    cp: str = None
    ui: str = None
    ua: str = None
    cn: str = None
    du: str = None
    cv: str = None
    dt: str = None
    d_: str = None
    pe: str = None
    di: str = None
    da: str = None
    bl: str = None
    sp9tc90: str = None  # 9 is interpreted as space
    sp9tc91: str = None  # 9 is interpreted as space

@dataclass
class PIDController:
    id: str
    nm: str = None
    ta: str = None
    gi: str = None
    ao: str = None
    ct: str = None
    ac: str = None
    ca: int = None
    cf: str = None
    cb: str = None
    cl: str = None
    ml: str = None
    cp: str = None
    ui: str = None
    ua: str = None
    u0: str = None
    pf: str = None
    if9: str = None  # 9 is interpreted as space
    df: str = None
    va: str = None
    bl: str = None
    sp9tc90: str = None  # 9 is interpreted as space
    sp9tc91: str = None  # 9 is interpreted as space


@dataclass
class RelativeTimeController:
    id: str
    nm: str = None
    ta: str = None
    gi: str = None
    ao: str = None
    ct: str = None
    ac: str = None
    ca: int = None
    cf: str = None
    mc: str = None
    mp: str = None
    ti9vv: str = None


@dataclass
class RelativeFromValueController:
    id: str
    nm: str = None
    ta: str = None
    gi: str = None
    ao: str = None
    ct: str = None
    ac: str = None
    ca: int = None
    cf: str = None
    mc: str = None
    mp: str = None
    ti9vv: str = None


CONTROL_MODELS = {
    0: TimeController,
    1: HydraulicController,
    2: IntervalController,
    3: PIDController,
    4: RelativeTimeController,
    5: RelativeFromValueController
}


def get_value(text, key):
    pattern = r"\b" + re.escape(key) + r"\b\s+(?:['\"])?([^\s'\"]+)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else None


def get_table(text, key):
    """
    """
    if key == "hc ht 1":
        pass
    table_lines = list()
    start_pattern = "TBLE"
    end_pattern = "tble"
    table = None
    start_reading = False
    for line in text.split("\n"):
        if start_reading:
            if len(table_lines) == 0 and not line.startswith(start_pattern):
                raise Exception("Expected start of a TBLE but did not find it")
            line = line.strip("\n").strip("<").strip()
            line = ",".join(line.split(" "))
            table_lines.append(line)
            if end_pattern in line:
                table = "\n".join(table_lines[1:-1])
                break
        else:
            if line.endswith(key):
                start_reading = True
    return table


def get_last_value_from_table(table):
    lines = [line for line in table.split("\n")]
    return lines[-1].split(",")[-1]


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
                record_id = get_value(record, "id")
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


def populate_control(control, record):
    type_hints = get_type_hints(type(control))  # get field types from the dataclass

    for field in fields(control):
        field_name = field.name.replace("9", " ")
        field_type = type_hints.get(field.name)

        if field_type is Table:
            # Custom logic for Table-typed fields
            table_data = get_table(record, field_name)
            setattr(control, field.name, table_data)
        else:
            value = get_value(record, field_name)
            setattr(control, field.name, value)


def parse_control_def(input_file, exclude=None):
    """
    Parses CONTROL.DEF file into python objects
    """
    exclude = exclude or []
    with open(input_file, "r", encoding="utf-8") as f:
        content = f.readlines()

    controls = dict()
    record_lines = list()
    start_pattern = r"CNTL"
    end_pattern = "*******"
    for line in content:
        line = line.strip("\n")
        if len(record_lines) == 0 and re.match(start_pattern, line):
            end_pattern = line[:4].lower()
        record_lines.append(line)
        if line.endswith(end_pattern):
            record = "\n".join(record_lines)
            control_id = get_value(record, "id")
            control_type = get_value(record, "ct")
            control_model = CONTROL_MODELS[int(control_type)]
            if control_model not in exclude:
                control = control_model(id=control_id)
                populate_control(control, record)
                controls[control_id] = control
            record_lines = list()
    return controls


# Example usage
if __name__ == "__main__":
    # case_dir = Path(r"D:\Sobek216\MySobekModel.lit\2")
    # deduplicate_friction_file(case_dir / "FRICTION.DAT", case_dir / "FRICTION_deduplicated.DAT")
    # # Don't forget the rename or delete FRICTION.DAT and than rename FRICTION_deduplicated.DAT to FRICTION.DAT

    case_dir = Path(r"C:\Users\leendert.vanwolfswin\Documents\overijssel\P1337def.lit\5")
    control_dict = parse_control_def(case_dir / "CONTROL.DEF")
    for control in control_dict.values():
        if isinstance(control, HydraulicController):
            print(control.hc9ht91)
    control_dict = parse_control_def(case_dir / "CONTROL.DEF", exclude=[TimeController])
    with open(case_dir / "control.csv", "w", newline="") as csvfile:
        writer = csv.writer(csvfile)

        # Write the header
        writer.writerow(["id", "name", "ca", "ua"])

        # Write each row
        for id, control in control_dict.items():
            ua = get_last_value_from_table(control.hc9ht91) if isinstance(control, HydraulicController) else control.ua

            writer.writerow([control.id, control.nm, control.ca, ua])
            # ml = id of measurement node
            # cp = type of measured parameter; 0 = water level, 1 = discharge
            # sp tc 0 = constant setpoint
