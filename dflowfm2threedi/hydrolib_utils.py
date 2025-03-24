import configparser
import csv
import io
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Dict, Type, SupportsRound, Tuple
from pathlib import Path

from hydrolib.core.dflowfm import Weir, Culvert, Orifice, FlowDirection, Structure, StructureModel, Compound, Pump, \
    Bridge, UniversalWeir
from hydrolib.core.dflowfm.crosssection.models import (
    CircleCrsDef,
    CrossDefModel,
    CrossSectionDefinition,
    RectangleCrsDef,
    XYZCrsDef,  # TODO add support for XYZ profiles
    YZCrsDef,
    ZWCrsDef,
    ZWRiverCrsDef,
)
from hydrolib.core.dflowfm.friction.models import (
    FrictionModel,
    FrictionType as DHydroFrictionType
)

import numpy as np

ASSUMED_WATER_DEPTH = 1
SUPPORTED_STRUCTURES = (Bridge, Weir, Culvert, Orifice, Compound, Pump, UniversalWeir)
SUPPORTED_CROSS_SECTIONS = (
    CircleCrsDef,
    RectangleCrsDef,
    YZCrsDef,
    ZWCrsDef,
    ZWRiverCrsDef
)


class CrossSectionShape(Enum):
    CLOSED_RECTANGLE = 0
    OPEN_RECTANGLE = 1
    CIRCLE = 2
    EGG = 3
    TABULATED_RECTANGLE = 5
    TABULATED_TRAPEZIUM = 6
    YZ = 7
    INVERTED_EGG = 8


TABLE_SHAPES = {
    CrossSectionShape.TABULATED_RECTANGLE,
    CrossSectionShape.TABULATED_TRAPEZIUM,
    CrossSectionShape.YZ,
}


class ThreeDiFrictionType(Enum):
    CHEZY = 1
    MANNING = 2
    CHEZY_WITH_CONVEYANCE = 3
    MANNING_WITH_CONVEYANCE = 4
    NONE = None


class ThreeDiCrossSectionData:
    fields = {
        "reference_level": float,
        "bank_level": float,
        "cross_section_shape": int,
        "cross_section_width": float,
        "cross_section_height": float,
        "cross_section_table": str,
    }

    def __init__(
        self,
        cross_section_shape: "CrossSectionShape",
        code: Optional[str] = None,
        reference_level: Optional[float] = None,
        bank_level: Optional[float] = None,
        cross_section_width: Optional[float] = None,
        cross_section_height: Optional[float] = None,
        cross_section_table: Optional[str] = None,
        friction_data: Optional["ThreeDiFrictionData"] = None,
    ):
        self.cross_section_shape = cross_section_shape
        self.code = code
        self.reference_level = reference_level
        self.bank_level = bank_level
        self.cross_section_width = cross_section_width
        self.cross_section_height = cross_section_height
        self.cross_section_table = cross_section_table
        self.friction_data = friction_data

    def _parse_cross_section_table(self) -> Tuple[List, List] | Tuple[None, None]:
        """Returns the columns in a csv-style table as lists of float"""
        if self.cross_section_shape in TABLE_SHAPES and self.cross_section_table:
            parsed_table = [row.split(",") for row in self.cross_section_table.split("\n")]
            height_values, width_values = list(zip(*parsed_table))
            return height_values, width_values
        else:
            return None, None

    @property
    def is_valid(self):
        self._parse_cross_section_table()  # if not valid, this will raise an exception
        return True

    def shift_down(self, shift: float):
        values = list(self._parse_cross_section_table())
        if values != [None, None]:
            z_column_idx = 1 if self.cross_section_shape == CrossSectionShape.YZ else 0
            values[z_column_idx] = list(np.round(np.array(values[z_column_idx]).astype(float) - shift, 4))
            self.cross_section_table = lists_to_csv([values[0], values[1]])


@dataclass
class ThreeDiFrictionData:
    fields = {
        "friction_type": int,
        "friction_value": float,
    }
    friction_type: ThreeDiFrictionType
    friction_value: float
    is_valid: bool  # we want to track validity without raising exceptions yet,
    # to prevent exceptions for data that is not eventually used in the export
    invalid_reason: str | None


class GenericFrictionDefinition:
    def __init__(
            self,
            friction_type: DHydroFrictionType = None,
            friction_value: float = None,
            is_valid: Optional[bool] = None,
            invalid_reason: Optional[str] = None
    ):
        """
        Sets ``self.is_valid`` and ``self.invalid_reason`` to track validity without raising exceptions yet, to prevent exceptions
        on data that is not eventually used in the export

        If the argument is_valid = False, an empty FrictionData will be created that does contain the provided
        invalid_reason
        """
        if is_valid == False:
            self.friction_type = DHydroFrictionType(friction_type) if friction_type else None
            self.friction_value = friction_value
            self.is_valid = is_valid
            self.invalid_reason = invalid_reason
        else:
            self.friction_type = DHydroFrictionType(friction_type) if friction_type else None
            self.friction_value = friction_value
            self.is_valid = True
            self.invalid_reason = None

    def to_threedi(self) -> ThreeDiFrictionData:
        if self.is_valid:
            conversion_success = True
            failure_reason = None
            if self.friction_type == DHydroFrictionType.chezy:
                friction_type = ThreeDiFrictionType.CHEZY
                friction_value = self.friction_value
            elif self.friction_type == DHydroFrictionType.manning:
                friction_type = ThreeDiFrictionType.MANNING
                friction_value = self.friction_value
            elif self.friction_type == DHydroFrictionType.strickler:
                friction_type = ThreeDiFrictionType.MANNING
                friction_value = np.round(1 / self.friction_value, 4)
            elif self.friction_type == DHydroFrictionType.whitecolebrook:
                friction_type = ThreeDiFrictionType.MANNING
                friction_value = np.round(self.friction_value ** (1 / 6) / 21.1, 4)  # this is far from perfect
                # but gives a good approx.
            elif self.friction_type == DHydroFrictionType.debosbijkerk:
                friction_type = ThreeDiFrictionType.MANNING
                friction_value = np.round(1/(self.friction_value * ASSUMED_WATER_DEPTH ** (1 / 3)), 4)  # this
                # is far from perfect but gives a good approx.
            elif self.friction_type is None:
                friction_type = ThreeDiFrictionType.NONE
                friction_value = None
            else:
                friction_type = ThreeDiFrictionType.NONE
                friction_value = None
                conversion_success = False
                failure_reason = f"Unknown friction type {self.friction_type}"
        else:
            friction_type = None
            friction_value = None
            conversion_success = False
            failure_reason = None
        invalid_reason = []
        if self.invalid_reason:
            invalid_reason.append(self.invalid_reason)
        if failure_reason:
            invalid_reason.append(failure_reason)
        result = ThreeDiFrictionData(
            friction_type=friction_type,
            friction_value=friction_value,
            is_valid=self.is_valid and conversion_success,
            invalid_reason="; ".join(invalid_reason),
        )
        return result


class GlobalFrictionDefinition(GenericFrictionDefinition):
    def __init__(self, friction_id: str, friction_type: DHydroFrictionType = None, friction_value: float = None,
                 is_valid: Optional[bool] = None, invalid_reason: Optional[str] = None):
        """
        Friction definition with a friction_id, to be coupled with a cross-section definition

        Sets ``self.is_valid`` and ``self.invalid_reason`` to track validity without raising exceptions yet, to prevent exceptions
        on data that is not eventually used in the export

        If the argument is_valid = False, an empty FrictionData will be created that does contain the provided
        invalid_reason
        """
        super().__init__(friction_type, friction_value, is_valid, invalid_reason)
        self.friction_id = friction_id


class BranchFrictionDefinition(GenericFrictionDefinition):
    def __init__(
            self,
            branch_id: str,
            chainage: float,
            friction_type: DHydroFrictionType = None,
            friction_value: float = None,
            is_valid: Optional[bool] = None,
            invalid_reason: Optional[str] = None
    ):
        """
        Friction definition with a branch_id and chainage, to be coupled with a cross-section location

        Sets ``self.is_valid`` and ``self.invalid_reason`` to track validity without raising exceptions yet, to prevent exceptions
        on data that is not eventually used in the export

        If the argument is_valid = False, an empty FrictionData will be created that does contain the provided
        invalid_reason
        """
        super().__init__(friction_type, friction_value, is_valid, invalid_reason)
        self.branch_id = branch_id
        self.chainage = chainage


def none_round(number: SupportsRound, ndigits: int = None):
    """
    round() that returns None if ``number`` is None
    :param decimals:
    :return:
    """
    return round(number, ndigits) if number is not None else None


def lists_to_csv(columns: List[List[float]], decimals=None) -> str:
    """
    Convert multiple lists (columns) into a CSV-style string.
    Returns an empty string if no data is provided.
    """
    if not columns:
        return ""

    # Ensure all columns have the same length
    row_count = len(columns[0])
    if any(len(col) != row_count for col in columns):
        raise ValueError("All columns must have the same length")

    if decimals:
        columns = [np.round(np.array(column, dtype=float), decimals=decimals).tolist() for column in columns]

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")

    # Write the rows (transposed data)
    for row in zip(*columns):
        writer.writerow(row)

    return output.getvalue().rstrip("\n")


def cross_section_def2threedi(
        cross_section_definition: CrossSectionDefinition,
        friction_definitions: Dict[str, GlobalFrictionDefinition]
):
    """
    Note that for tabulated cross-sections such as ZW, ZW River, YZ, etc. the flow widths are used and the total widths
    are ignored
    """

    # Friction data: CircleCrsDef, RectangleCrsDef, ZWCrsDef
    if type(cross_section_definition) in [CircleCrsDef, RectangleCrsDef, ZWCrsDef]:
        friction_definition = GlobalFrictionDefinition(
            friction_id=cross_section_definition.frictionid,
            friction_type=DHydroFrictionType(cross_section_definition.frictiontype) if
            cross_section_definition.frictiontype else None,
            friction_value=cross_section_definition.frictionvalue,
        )

    # Friction data: ZWRiverCrsDef, YZCrsDef
    elif type(cross_section_definition) in [ZWRiverCrsDef, YZCrsDef]:
        friction_id = None
        friction_type = None
        friction_value = None
        is_valid = None
        invalid_reason = None
        if cross_section_definition.frictiontypes is not None and cross_section_definition.frictionvalues is not None:
            if len(cross_section_definition.frictiontypes) == 1 and len(cross_section_definition.frictionvalues) == 1:
                friction_type = cross_section_definition.frictiontypes[0]
                friction_value = cross_section_definition.frictionvalues[0]
            else:
                is_valid = False
                invalid_reason = "Multiple friction values for one cross-section are not yet supported."
        if cross_section_definition.frictionids is not None:
            if len(cross_section_definition.frictionids) == 1:
                friction_id = cross_section_definition.frictionids[0]
            elif friction_type is None and friction_value is None:
                is_valid = False
                invalid_reason = "Multiple friction values for one cross-section are not yet supported."

        friction_definition = GlobalFrictionDefinition(
            friction_id=friction_id,
            friction_type=friction_type,
            friction_value=friction_value,
            is_valid=is_valid,
            invalid_reason=invalid_reason,
        )

    # Cross-section data
    width = None
    height = None
    table = None
    reference_level = None
    bank_level = None

    if isinstance(cross_section_definition, CircleCrsDef):
        shape = CrossSectionShape.CIRCLE
        width = cross_section_definition.diameter
    elif isinstance(cross_section_definition, RectangleCrsDef):
        width = cross_section_definition.width
        if cross_section_definition.closed:
            shape = CrossSectionShape.CLOSED_RECTANGLE
            height = cross_section_definition.height
        else:
            shape = CrossSectionShape.OPEN_RECTANGLE
    elif isinstance(cross_section_definition, ZWCrsDef):
        # TODO: remove Preismann slots (if last width is < threshold value, set to 0)
        shape = CrossSectionShape.TABULATED_TRAPEZIUM
        table = lists_to_csv(
            [
                cross_section_definition.levels,
                cross_section_definition.flowwidths
            ],
            decimals=3
        )

    elif isinstance(cross_section_definition, ZWRiverCrsDef):
        # TODO: uitzoeken hoe dit nou precies zit, klinkt behoorlijk complex
        # TODO: remove Preismann slots (if last width is < threshold value, set to 0)
        shape = CrossSectionShape.TABULATED_TRAPEZIUM
        table = lists_to_csv(
            [
                cross_section_definition.levels,
                cross_section_definition.flowwidths
            ],
            decimals=3
        )
        bank_level = cross_section_definition.leveecrestLevel

    elif isinstance(cross_section_definition, YZCrsDef):
        # TODO: remove Preismann slots (if abs(first Y - last Y) < threshold value, set them both to the average of these)
        # TODO find out what "singleValuedZ" means and if we need it somehow
        shape = CrossSectionShape.YZ
        reference_level = float(np.min(np.array(cross_section_definition.zcoordinates)))
        table = lists_to_csv(
            [
                list(np.array(cross_section_definition.ycoordinates) - cross_section_definition.ycoordinates[0]),
                list(np.array(cross_section_definition.zcoordinates) - reference_level)
            ],
            decimals=3
        )
    else:
        raise ValueError(
            f"Unknown cross-section type: {type(cross_section_definition)} "
            f"for cross-section {cross_section_definition.id}"
        )

    return ThreeDiCrossSectionData(
        code=cross_section_definition.id,
        bank_level=none_round(bank_level, 3),
        reference_level=none_round(reference_level, 3),
        cross_section_shape=shape,
        cross_section_width=none_round(width, 3),
        cross_section_height=none_round(height, 3),
        cross_section_table=table,
        friction_data=friction_definition.to_threedi()
    )


def read_friction(mdu_file: Path) -> Tuple[
    Dict[str, GlobalFrictionDefinition],
    Dict[str, List[BranchFrictionDefinition]]
]:
    """
    Returns a {friction_id: GlobalFrictionDefinition} dict and a {branch_id: List[BranchFrictionDefinition]} dict
    """
    # We read the MDU file with generic python libraries
    # because reading it with hydrolib-core raises all sorts of validation errors
    mdu = configparser.ConfigParser()
    mdu.read(mdu_file)
    frict_files = mdu["geometry"]["FrictFile"].split(";")
    friction_definitions_dict = dict()
    branch_friction_definitions_dict = dict()
    for frict_file in frict_files:
        friction_path = mdu_file.parent / frict_file
        friction_definitions = FrictionModel(friction_path)
        # get friction definitions from global entries
        for friction_definition in friction_definitions.global_:
            friction_definitions_dict[friction_definition.frictionid] = GlobalFrictionDefinition(
                friction_id=friction_definition.frictionid,
                friction_type=DHydroFrictionType(friction_definition.frictiontype) if
                friction_definition.frictiontype else None,
                friction_value=friction_definition.frictionvalue
            )

        for friction_definition in friction_definitions.branch:
            if not friction_definition.chainage:
                continue

            if friction_definition.functiontype.lower() != 'constant':
                warnings.warn(
                    f"Friction definition with a function type other than 'constant' are not supported. "
                    f"Function type: {friction_definition.functiontype}. "
                    f"Branch ID: {friction_definition.branchid}. "
                    f"Chainage: {friction_definition.chainage}."
                )

            friction_definitions_for_this_branch = []
            for i, chainage in enumerate(friction_definition.chainage):
                branch_friction_definition = BranchFrictionDefinition(
                    branch_id=friction_definition.branchid,
                    chainage=chainage,
                    friction_type=DHydroFrictionType(friction_definition.frictiontype) if
                    friction_definition.frictiontype else None,
                    friction_value=friction_definition.frictionvalues[i]
                )
                friction_definitions_for_this_branch.append(branch_friction_definition)
            branch_friction_definitions_dict[friction_definition.branchid] = friction_definitions_for_this_branch
    return friction_definitions_dict, branch_friction_definitions_dict


def count_cross_section_types(cross_def_path: Path) -> Dict[Type[CrossSectionDefinition], int]:
    cross_defs = CrossDefModel(cross_def_path)
    cross_def_type_counts = dict()
    for xsec in cross_defs.definition:
        if type(xsec) in cross_def_type_counts.keys():
            cross_def_type_counts[type(xsec)] += 1
        else:
            cross_def_type_counts[type(xsec)] = 1
    return cross_def_type_counts


def count_structure_types(structures_path: Path) -> Dict[Type[Structure], int]:
    structures = StructureModel(structures_path)
    structure_type_counts = dict()
    for xsec in structures.structure:
        if type(xsec) in structure_type_counts.keys():
            structure_type_counts[type(xsec)] += 1
        else:
            structure_type_counts[type(xsec)] = 1
    return structure_type_counts


def check_structures(path: Path):
    structure_counts = count_structure_types(path)
    for structure_type, count in structure_counts.items():
        if structure_type not in SUPPORTED_STRUCTURES:
            warnings.warn(
                f"Source data contains {count} structures of type {structure_type.__name__}, which are not supported!"
            )


def read_cross_sections(
        cross_def_path: Path,
        global_friction_definitions: Dict[str, GlobalFrictionDefinition],
) -> Dict[str, ThreeDiCrossSectionData]:
    """
    Reads cross-section definitions from DHydro, combines them with global friction data, and returns them in a 3Di
    compatible format
    """
    cross_defs = CrossDefModel(cross_def_path)
    threedi_xsecs_list = [
        cross_section_def2threedi(cross_section_definition=xsec, friction_definitions=global_friction_definitions)
        for xsec in cross_defs.definition
    ]
    threedi_xsecs = {xsec.code: xsec for xsec in threedi_xsecs_list}
    return threedi_xsecs


if __name__ == "__main__":
    flow_fm_input_path = Path(
        # r"C:\Users\leendert.vanwolfswin\Documents\overijssel\P1337 DHydro\Overijssel - P1337.dsproj_data\FlowFM\input"
        r"C:\Users\leendert.vanwolfswin\Documents\overijssel\P1337def_case4.dsproj_data\FlowFM\input"
    )
    mdu_path = flow_fm_input_path / "FlowFM.mdu"
    cross_def_path = flow_fm_input_path / "crsdef.ini"
    cross_defs = CrossDefModel(cross_def_path)

    friction_definitions, branch_friction_definitions = read_friction(mdu_file=mdu_path)
    cross_sections = read_cross_sections(
        cross_def_path=cross_def_path,
        global_friction_definitions=friction_definitions
    )

    no_chainage = list()

    mdu = configparser.ConfigParser()
    mdu.read(mdu_path)
    # frict_files = mdu["geometry"]["FrictFile"].split(";")
    # friction_definitions_dict = dict()
    # branch_friction_definitions_dict = dict()
    # for frict_file in frict_files:
    #     friction_path = mdu_path.parent / frict_file
    #     friction_definitions = FrictionModel(friction_path)
    #     # get friction definitions from global entries
    #     for friction_definition in friction_definitions.global_:
    #         friction_definitions_dict[friction_definition.frictionid] = GlobalFrictionDefinition(
    #             friction_id=friction_definition.frictionid,
    #             friction_type=DHydroFrictionType(friction_definition.frictiontype) if
    #             friction_definition.frictiontype else None,
    #             friction_value=friction_definition.frictionvalue
    #         )
    #
    #     for friction_definition in friction_definitions.branch:
    #         if not friction_definition.chainage:
    #             no_chainage.append(friction_definition)
    #             continue
    #
    #         if friction_definition.functiontype.lower() != 'constant':
    #             warnings.warn(
    #                 f"Friction definition with a function type other than 'constant' are not supported. "
    #                 f"Function type: {friction_definition.functiontype}. "
    #                 f"Branch ID: {friction_definition.branchid}. "
    #                 f"Chainage: {friction_definition.chainage}."
    #             )
    #
    #         friction_definitions_for_this_branch = []
    #         for i, chainage in enumerate(friction_definition.chainage):
    #             branch_friction_definition = BranchFrictionDefinition(
    #                 branch_id=friction_definition.branchid,
    #                 chainage=chainage,
    #                 friction_type=DHydroFrictionType(friction_definition.frictiontype) if
    #                 friction_definition.frictiontype else None,
    #                 friction_value=friction_definition.frictionvalues[i]
    #             )
    #             friction_definitions_for_this_branch.append(branch_friction_definition)
    #         branch_friction_definitions_dict[friction_definition.branchid] = friction_definitions_for_this_branch
    #
    # a = branch_friction_definitions_dict["W4890"]

    structures_path = flow_fm_input_path / "structures.ini"
    s = StructureModel(structures_path)
    # orifices = [struct for struct in s.structure if struct.type == "orifice"]
    compounds = [struct for struct in s.structure if struct.type == "compound"]
    real_compounds = [c for c in compounds if c.numstructures > 1]

    print("Klaar")
