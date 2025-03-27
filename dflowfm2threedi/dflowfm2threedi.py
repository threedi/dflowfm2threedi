import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from pprint import pprint
from types import NoneType
from typing import Dict, List, Type, Optional, Tuple, Callable

import numpy as np
from hydrolib.core.dflowfm import (
    CrossLocModel,
    StructureModel,
    CrossSection,
    Culvert,
    Structure,
    Orifice,
    Weir,
    Pump,
    Bridge,
    UniversalWeir, CrossSectionDefinition, CrossDefModel
)

from hydrolib.core.dflowfm.ini.models import INIBasedModel
from netCDF4 import Dataset
from osgeo import ogr, osr
from shapely import Point
from shapely.geometry import LineString

from hydrolib_utils import check_structures, read_friction, read_cross_sections, ThreeDiCrossSectionData, \
    ThreeDiFrictionData, \
    BranchFrictionDefinition, count_structure_types, GlobalFrictionDefinition, GenericFrictionDefinition, lists_to_csv, \
    CrossSectionShape, SUPPORTED_STRUCTURES, SUPPORTED_CROSS_SECTIONS

ogr.UseExceptions()


@dataclass
class LayerMapping:
    target_layer_name: str
    field_mapping: Optional[Dict] = None


@dataclass
class ReplacementConfig:
    get_from: str
    source_field: str
    parser: Optional[Callable] = lambda x: x  # default simply returns the input value


class Proxy(str):
    pass


OGR_FIELD_TYPES = {
    str: ogr.OFTString,
    int: ogr.OFTInteger,
    float: ogr.OFTReal,
    bool: ogr.OFSTBoolean,
}


def _get_node(geometry: ogr.Geometry, index: int) -> ogr.Geometry:
    x, y, _ = geometry.GetPoint(index)  # _ for z-coordinate if present
    point_geom = ogr.Geometry(ogr.wkbPoint)
    point_geom.AddPoint(x, y)
    return point_geom


def start_node(geometry: ogr.Geometry) -> ogr.Geometry:
    return _get_node(geometry, 0)


def end_node(geometry: ogr.Geometry) -> ogr.Geometry:
    last_index = geometry.GetPointCount() - 1
    return _get_node(geometry, last_index)


def reverse_line(geometry: ogr.Geometry) -> ogr.Geometry:
    reversed_line = ogr.Geometry(ogr.wkbLineString)
    for i in range(geometry.GetPointCount() - 1, -1, -1):  # Iterate in reverse order
        reversed_line.AddPoint(*geometry.GetPoint(i))
    return reversed_line


# TODO Check if all pumps are suction side

ORIFICE_TO_POSITIVE_PUMP_REPLACEMENT_CONFIG = {
    "geometry": ReplacementConfig(get_from="delete_layer", source_field="", parser=start_node),
    "id": ReplacementConfig(get_from="delete_layer", source_field="id"),
    "code": ReplacementConfig(get_from="source", source_field="id"),
    "display_name": ReplacementConfig(get_from="delete_layer", source_field="display_name"),
    "start_level": ReplacementConfig(get_from="source", source_field="startlevelsuctionside"),
    "lower_stop_level": ReplacementConfig(get_from="source", source_field="stoplevelsuctionside"),
    "upper_stop_level": ReplacementConfig(
        get_from="source",
        source_field="stoplevelsuctionside",  # this field is not actually used, because the parser always returns None
        parser=lambda x: None
    ),
    "capacity": ReplacementConfig(get_from="source", source_field="capacity", parser=lambda x: 1000 * x),
    "type": ReplacementConfig(
        get_from="source",
        source_field="controlside",
        parser=lambda x: 1 if x == "suctionSide" else 2 if x == "deliverySide" else None
    ),
    "sewerage": ReplacementConfig(get_from="delete_layer", source_field="sewerage"),
    "zoom_category": ReplacementConfig(get_from="delete_layer", source_field="zoom_category"),
    "connection_node_id": ReplacementConfig(get_from="delete_layer", source_field="connection_node_start_id"),
}


ORIFICE_TO_NEGATIVE_PUMP_REPLACEMENT_CONFIG = ORIFICE_TO_POSITIVE_PUMP_REPLACEMENT_CONFIG.copy()
ORIFICE_TO_NEGATIVE_PUMP_REPLACEMENT_CONFIG["geometry"] = ReplacementConfig(
    get_from="delete_layer",
    source_field="",
    parser=end_node
)


connection_node_layer_mapping = LayerMapping(
    target_layer_name="connection_node",
    field_mapping={
        "node_id": "code"
    }
)


channel_layer_mapping = LayerMapping(
    target_layer_name="channel",
    field_mapping={
        "branch_id": "code",
        "branch_long_name": "display_name",
        "source_node_id": Proxy("connection_node_start_id"),
        "target_node_id": Proxy("connection_node_end_id"),
    },
)

cross_section_location_mapping = LayerMapping(
    target_layer_name="cross_section_location",
    field_mapping={
        "id": "code",
        "branchid": Proxy("channel_id"),
    }
)


def extract_branches(network_file: Path) -> Dict:
    """
    Returns a {branch_id: branch_data} dict, in which branch data is itself a dict with the following keys:

    - branch_id
    - branch_long_name
    - source_node_id
    - target_node_id
    - length
    - order
    - type
    - geometry

    """
    f = Dataset(network_file)
    keys = {key.lower(): key for key in f.variables.keys()}  # some files have keys with first letter capitalized,
                                                             # others all lower key

    # Extract branch IDs
    branch_ids = ["".join(b).strip() for b in f.variables[keys['network_branch_id']][:].astype(str)]
    branch_long_names = ["".join(b).strip() for b in f.variables[keys['network_branch_long_name']][:].astype(str)]

    # Extract branch attributes
    branch_lengths = f.variables[keys['network_edge_length']][:]
    branch_orders = f.variables[keys['network_branch_order']][:]
    branch_types = f.variables[keys['network_branch_type']][:]

    # Extract node IDs
    node_ids = ["".join(n).strip() for n in f.variables[keys['network_node_id']][:].astype(str)]

    # Extract geometry node counts per branch
    geom_node_counts = f.variables[keys['network_geom_node_count']][:]

    # Extract geometry node coordinates
    geom_x = f.variables[keys['network_geom_x']][:]
    geom_y = f.variables[keys['network_geom_y']][:]

    # Extract edge-node indices (for start and end nodes)
    edge_nodes = f.variables[keys['network_edge_nodes']][:]  # (nEdges, 2)

    # List to store branch geometries and their source/target nodes
    branch_geometries = []
    source_node_ids = []
    target_node_ids = []

    current_geom_index = 0  # Keeps track of where to read from Network_geom_x/y

    for i, count in enumerate(geom_node_counts):
        # Get the X, Y coordinates for the branch
        branch_x_coords = geom_x[current_geom_index: current_geom_index + count]
        branch_y_coords = geom_y[current_geom_index: current_geom_index + count]

        # Create LineString geometry
        line = LineString(zip(branch_x_coords, branch_y_coords))
        branch_geometries.append(line)

        # Get the source and target node indices
        source_index, target_index = edge_nodes[i]

        # Get the actual node IDs using these indices
        source_node_ids.append(node_ids[source_index])
        target_node_ids.append(node_ids[target_index])

        # Move the index forward by the number of nodes in this branch
        current_geom_index += count

    layer_dict = dict()
    for i in range(len(branch_ids)):
        feature_dict = {
            "branch_id": branch_ids[i],
            "branch_long_name": branch_long_names[i],
            "source_node_id": source_node_ids[i],
            "target_node_id": target_node_ids[i],
            "length": branch_lengths[i],
            "order": branch_orders[i],
            "type": branch_types[i],
            "geometry": branch_geometries[i],
        }
        layer_dict[branch_ids[i]] = feature_dict

    return layer_dict


def extract_nodes(network_file: Path) -> Dict:
    """
    Returns a {node_id: node_data} dict, in which node_data is itself a dict with the following keys:

    - node_id: str
    - node_long_name: str
    - geometry: Linestring
    """
    f = Dataset(network_file)
    keys = {key.lower(): key for key in f.variables.keys()}  # some files have keys with first letter capitalized,
                                                             # others all lower key

    # Extract node IDs (convert from byte strings to normal strings)
    node_ids = ["".join(n).strip() for n in f.variables[keys['network_node_id']][:].astype(str)]

    # Extract node coordinates
    node_x = f.variables[keys['network_node_x']][:]
    node_y = f.variables[keys['network_node_y']][:]

    # (Optional) Extract long names if needed
    node_long_names = ["".join(n).strip() for n in f.variables[keys['network_node_long_name']][:].astype(str)]

    node_geometries = [Point(x, y) for x, y in zip(node_x, node_y)]

    layer_dict = dict()
    for i in range(len(node_ids)):
        feature_dict = {
            "node_id": node_ids[i],
            "node_long_name": node_long_names[i],
            "geometry": node_geometries[i],
        }
        layer_dict[node_ids[i]] = feature_dict

    return layer_dict


def geometry_from_chainage(branches: Dict, branch_id: str, chainage: float) -> Point:
    branch = branches[branch_id]
    branch_geom: LineString = branch["geometry"]
    return branch_geom.interpolate(chainage)


def get_field_definitions(objects: List[INIBasedModel]) -> List[ogr.FieldDefn]:
    """
    Get a list of ogr.FieldDefn from a list of Structures, CrossSections, etc.
    Ignores all fields that are not str, float, int or bool.
    If a fields is a list of 1 value, it is converted to the type of that value

    :param objects: List of structures, all structures in the list must be of the same type
    :return: {attribute_name: ogr_field_type} dict
    """
    result = list()
    object_types = set([type(s) for s in objects])
    if len(object_types) == 0:
        return result
    elif len(object_types) > 1:
        raise ValueError(f"Objects must be of exactly 1 type, not {object_types}")

    attributes = []
    for attr in dir(objects[0]):
        if not attr.startswith("_") and not callable(getattr(objects[0], attr)):
            attributes.append(attr)

    for attribute in attributes:
        python_types = set()
        for structure in objects:
            attribute_value = getattr(structure, attribute)
            if isinstance(attribute_value, list):
                attribute_value = ",".join([str(x) for x in attribute_value])
            if type(attribute_value) in OGR_FIELD_TYPES.keys():
                python_types.add(type(attribute_value))

        if len(python_types) == 2:
            python_types.discard(NoneType)

        if len(python_types) == 0:
            pass  # we are dealing with some other type of attribute that we don't need
        elif len(python_types) == 1:
            if list(python_types)[0] == NoneType:
                result.append(ogr.FieldDefn(attribute, OGR_FIELD_TYPES[str]))
            else:
                result.append(ogr.FieldDefn(attribute, OGR_FIELD_TYPES[python_types.pop()]))
        elif len(python_types) > 1:
            raise ValueError(f"The values in field {attribute} have different types: {python_types}")
    return result


def extract_from_ini(
        ini_file: Path,
        object_type: Type[INIBasedModel],
        branches: Optional[Dict] = None
) -> Tuple[Dict, List[ogr.FieldDefn]]:
    """``branches`` is only required if objects have a branchid and chainage from which to construct a geometry"""
    if object_type == CrossSection:
        extraction_model = CrossLocModel
        attr_name = "crosssection"
    elif object_type in SUPPORTED_CROSS_SECTIONS:
        extraction_model = CrossDefModel
        attr_name = "definition"
    elif object_type in SUPPORTED_STRUCTURES:
        extraction_model = StructureModel
        attr_name = "structure"
    else:
        raise ValueError(f"Cannot extract features for object_type {object_type}")
    unfiltered_objects = getattr(extraction_model(ini_file), attr_name)
    objects = [o for o in unfiltered_objects if isinstance(o, object_type)]
    has_geometery = all([hasattr(obj, "chainage") for obj in objects])
    layer_dict = dict()
    field_definitions = get_field_definitions(objects)
    for obj in objects:
        feature_dict = dict()
        if has_geometery:
            feature_dict["geometry"] = geometry_from_chainage(
                branches=branches,
                branch_id=obj.branchid,
                chainage=obj.chainage
            )
        for field_definition in field_definitions:
            value = getattr(obj, field_definition.name)
            if isinstance(value, list):
                value = ",".join([str(x) for x in value])
            feature_dict[field_definition.name] = value
        layer_dict[obj.id] = feature_dict
    return layer_dict, field_definitions


def import_to_threedi_layer(
        source: Dict,
        target: Path,
        layer_mapping: LayerMapping,
        input_name_id_mapping: Dict = None
) -> Dict:
    """
    Import schematisation objects from a source dict that was extracted using extract_from_ini()

    :param source: Path to the source shapefile
    :param target: Path to the target 3Di schematisation Geopackage
    :param layer_mapping: LayerMapping object that contains the data needed to map source data to target data.
    :param input_name_id_mapping: Mapping of D-Hydro "Name" to 3Di "ID" in a {name: id} dict. For example, if importing
    channels, input_name_id_mapping should be a {<Node name>:<connection node id>} dict
    :returns: Mapping of D-Hydro "Name" to 3Di "ID" in a {name: id} dict
    """

    gpkg = ogr.Open(str(target), 1)  # 1 means update mode
    if gpkg is None:
        raise FileNotFoundError(f"Could not open geopackage: {target}")
    dst_layer = gpkg.GetLayerByName(layer_mapping.target_layer_name)
    if dst_layer is None:
        raise Exception(f"Layer '{layer_mapping.target_layer_name}' not found in {target}")

    dst_layer_def = dst_layer.GetLayerDefn()

    # Calculate the maximum current value for 'id' in the destination layer
    max_id = 0
    dst_layer.ResetReading()  # Ensure we read from the beginning of the layer
    for feat in dst_layer:
        current_id = feat.GetField("id")
        if current_id is not None and current_id > max_id:
            max_id = current_id

    # Set the starting value for auto-increment
    next_id = max_id + 1

    output_name_id_mapping = dict()

    for src_feat_name, src_feat in source.items():
        dst_feat = ogr.Feature(dst_layer_def)
        new_geom = ogr.CreateGeometryFromWkb(src_feat["geometry"].wkb)
        new_geom.FlattenTo2D()
        dst_feat.SetGeometry(new_geom)

        # Set the target primary key "id" with the next auto-increment value
        dst_feat.SetField("id", next_id)

        for source_field, target_field in layer_mapping.field_mapping.items():
            source_value = src_feat[source_field]
            if isinstance(target_field, Proxy):
                if input_name_id_mapping is None:
                    raise Exception("input_name_id_mapping needed but not provided")
                source_value = input_name_id_mapping[source_value]
            dst_feat.SetField(target_field, source_value)

        # Add the new feature to the destination layer
        dst_layer.CreateFeature(dst_feat)

        # Clean up
        dst_feat = None

        output_name_id_mapping[src_feat_name] = next_id
        next_id += 1

    # Cleanup and close datasets
    src_ds = None
    gpkg = None
    return output_name_id_mapping


def import_structures(
        source: Dict,
        epsg_code: int,
        target: Path,
        cross_section_data: Dict[str, ThreeDiCrossSectionData],
        field_definitions: List[ogr.FieldDefn],
        feature_type: str = "unknown",
        target_layer_name: str = None,
) -> None:
    """
    Writes DHydro culverts, weirs, etc. data to geopackage, adding cross-section and friction data in
    3Di format.
    Overwrites layer if exists.
    """
    target_layer_name = target_layer_name or "dhydro_" + feature_type

    # Open or create the GeoPackage
    driver = ogr.GetDriverByName("GPKG")
    gpkg = driver.Open(target, 1)  # 1 = Read/Write mode

    if gpkg is None:
        # If the GeoPackage doesn't exist, create it
        gpkg = driver.CreateDataSource(target)

    # Check if the layer already exists and remove it
    if gpkg.GetLayerByName(target_layer_name):
        gpkg.DeleteLayer(target_layer_name)

    # Create the new layer in the GeoPackage with the same geometry type and spatial reference
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(epsg_code)  # Set from EPSG code

    gpkg_layer = gpkg.CreateLayer(
        name=target_layer_name,
        srs=spatial_ref,
        geom_type=ogr.wkbPoint
    )

    # Add additional fields to the GeoPackage layer: Structure object
    for field_defn in field_definitions:
        gpkg_layer.CreateField(field_defn)

    # Add additional fields to the GeoPackage layer: cross-section
    for field_name, field_type in (ThreeDiCrossSectionData.fields | ThreeDiFrictionData.fields).items():
        ogr_field_type = OGR_FIELD_TYPES[field_type]
        field_defn = ogr.FieldDefn(field_name, ogr_field_type)
        gpkg_layer.CreateField(field_defn)

    # Create features from source
    dst_layer_defn: ogr.FeatureDefn = gpkg_layer.GetLayerDefn()
    for src_feat_name, src_feat in source.items():
        dst_feat = ogr.Feature(dst_layer_defn)
        new_geom = ogr.CreateGeometryFromWkb(src_feat["geometry"].wkb)
        new_geom.FlattenTo2D()
        dst_feat.SetGeometry(new_geom)

        for attr, value in src_feat.items():
            if attr != "geometry":
                field_index = dst_layer_defn.GetFieldIndex(attr)
                dst_feat.SetField(field_index, value)

        # data from cross-section definitions
        src_feat_id = src_feat["id"]

        if src_feat_id in cross_section_data:
            cross_section_definition = cross_section_data[src_feat_id]
            if feature_type == 'culvert':
                cross_section_definition.friction_data = GenericFrictionDefinition(
                    friction_type=src_feat["bedfrictiontype"],
                    friction_value=src_feat["bedfriction"],
                ).to_threedi()
            elif feature_type == 'bridge':
                cross_section_definition.friction_data = GenericFrictionDefinition(
                    friction_type=src_feat["frictiontype"],
                    friction_value=src_feat["friction"],
                ).to_threedi()
                cross_section_definition.shift_down(src_feat["shift"])
                cross_section_definition.reference_level = src_feat["shift"]
            add_cross_section_data_to_feature(
                cross_section_definition=cross_section_definition,
                feature=dst_feat,
                feature_type=feature_type,
            )

        if feature_type == 'universalweir':
            y_values = [float(val) for val in src_feat["yvalues"].split(",")]
            z_values = list(
                np.round(
                    np.array([float(val) for val in src_feat["zvalues"].split(",")]) - src_feat["crestlevel"],
                    4
                )
            )

            dst_feat.SetField("cross_section_shape", CrossSectionShape.YZ.value)
            dst_feat.SetField("cross_section_table", lists_to_csv([y_values, z_values]))

        gpkg_layer.CreateFeature(dst_feat)
        dst_feat = None  # Free memory

    # Cleanup
    gpkg = None

    print(f"Successfully copied {feature_type}s to '{target}' as layer '{target_layer_name}'.")


def import_table(
        source: Dict,
        target: Path,
        field_definitions: List[ogr.FieldDefn],
        feature_type: str = "unknown",
        target_layer_name: str = None,
) -> None:
    """
    Writes data with no geometry to geopackage
    Overwrites layer if exists.
    """
    target_layer_name = target_layer_name or "dhydro_" + feature_type

    # Open or create the GeoPackage
    driver = ogr.GetDriverByName("GPKG")
    gpkg = driver.Open(target, 1)  # 1 = Read/Write mode

    if gpkg is None:
        # If the GeoPackage doesn't exist, create it
        gpkg = driver.CreateDataSource(target)

    # Check if the layer already exists and remove it
    if gpkg.GetLayerByName(target_layer_name):
        gpkg.DeleteLayer(target_layer_name)

    gpkg_layer = gpkg.CreateLayer(
        name=target_layer_name,
        geom_type=ogr.wkbNone
    )

    # Add additional fields to the GeoPackage layer: Structure object
    for field_defn in field_definitions:
        gpkg_layer.CreateField(field_defn)

    # Create features from source
    dst_layer_defn: ogr.FeatureDefn = gpkg_layer.GetLayerDefn()
    for src_feat_name, src_feat in source.items():
        dst_feat = ogr.Feature(dst_layer_defn)

        for attr, value in src_feat.items():
            field_index = dst_layer_defn.GetFieldIndex(attr)
            dst_feat.SetField(field_index, value)

        gpkg_layer.CreateFeature(dst_feat)
        dst_feat = None  # Free memory

    # Cleanup
    gpkg = None

    print(f"Successfully copied {feature_type}s to '{target}' as layer '{target_layer_name}'.")


def get_cross_section_location_id_to_defname_mapping(
        name_id_mapping: Dict,
        cross_section_locations: Dict
):
    """
    Returns a {cross_section_location.id: DefName} mapping that can be used to connect cross-section data to
    imported cross-section locations.
    """
    name_defname_dict = {
        name: data["definitionid"]
        for name, data in cross_section_locations.items()
    }

    result = {
        id: name_defname_dict[name]
        for name, id in name_id_mapping.items()
    }
    return result


def add_cross_section_data_to_feature(
        cross_section_definition: ThreeDiCrossSectionData,
        feature: ogr.Feature,
        feature_type: str,
) -> None:
    """
    Adds values from ``cross_section_definition`` and ``cross_section_definition.friction_data`` to ``feature``
    :param cross_section_definition:
    :param feature:
    :return:
    """
    for attribute in ThreeDiCrossSectionData.fields.keys():
        new_value = getattr(cross_section_definition, attribute)
        if isinstance(new_value, Enum):
            new_value = new_value.value
        if feature[attribute] is None and new_value is not None:
            feature.SetField(attribute, new_value)
    if cross_section_definition.friction_data.is_valid:
        for attribute in ThreeDiFrictionData.fields.keys():
            new_value = getattr(cross_section_definition.friction_data, attribute)
            if isinstance(new_value, Enum):
                new_value = new_value.value
            if feature[attribute] is None and new_value is not None:
                feature.SetField(attribute, new_value)
    else:
        warnings.warn(
            f"Friction data for {feature_type} with ID {feature['id']} is not valid. "
            f"Reason: {cross_section_definition.friction_data.invalid_reason}"
        )


def enrich_cross_section_locations(
        cross_section_data: Dict[str, ThreeDiCrossSectionData],
        gpkg: Path,
        cross_section_id_to_defname_mapping: Dict = None
):
    # Open the GeoPackage
    data_source = ogr.Open(gpkg, 1)  # 1 means update mode

    if data_source is None:
        raise FileNotFoundError(f"Could not open geopackage: {gpkg}")
    else:
        layer = data_source.GetLayer("cross_section_location")
        for feature in layer:
            cross_section_location_id = feature['id']
            try:
                def_name = cross_section_id_to_defname_mapping[cross_section_location_id]
                cross_section_definition = cross_section_data[def_name]
            except KeyError:
                continue

            if not cross_section_definition.is_valid:
                warnings.warn(
                    f"cross_section_location with id {cross_section_location_id} has an invalid cross_section"
                )

            add_cross_section_data_to_feature(
                cross_section_definition=cross_section_definition,
                feature=feature,
                feature_type="cross_section_location",
            )

            layer.SetFeature(feature)
        data_source = None  # Close the data source


def enrich_cross_section_definition(
        cross_section_definition: ThreeDiCrossSectionData,
        cross_section_locations: Dict[str, Dict],
        branch_friction_definitions: Dict[str, List[BranchFrictionDefinition]]
) -> ThreeDiCrossSectionData:
    """
    Find branch friction data for given cross-section definition and update it accordingly
    If no branch friction data is found, cross-section definition is returned unaltered.
    """

    # Find cross-section location that has this cross-section definition
    found_xsec_loc = False
    for cross_section_location in cross_section_locations.values():
        if cross_section_location["definitionid"] == cross_section_definition.code:
            found_xsec_loc = True
            break
    if not found_xsec_loc:
        return cross_section_definition

    try:
        if cross_section_location["branchid"] == "W4890":
            a = branch_friction_definitions[cross_section_location["branchid"]]
    except KeyError:
        pass

    # Find the branch friction definition for this cross-section location
    try:
        friction_definitions = branch_friction_definitions[cross_section_location["branchid"]]
    except KeyError:
        return cross_section_definition
    for friction_definition in friction_definitions:
        if round(friction_definition.chainage, 2) == round(cross_section_location["chainage"], 2):
            # Update the cross-section definition with friction data from the BranchFrictionDefinition
            cross_section_definition.friction_data = friction_definition.to_threedi()

    return cross_section_definition


def replace_structures(
        gpkg: Path,
        source: Dict,
        delete_from_layer: str,
        add_to_layer: str,
        config: Dict[str, ReplacementConfig],
        match_field: str = "code",
        match_prefix: str = "",
        match_postfix: str = "",
) -> List[Tuple]:
    """
    Delete features from ``delete_from_layer`` in ``gpkg`` and replace them with features in ``add_to_layer``.
    ``config`` determines which attributes of the new features are copied from ``delete_from_layer`` and which from
    ``source``.

    Features in ``delete_from_layer`` are matched to those in ``source`` if the value in ``match_field`` equals
    the key of the source dict.

    :returns: a list of (delete_feature: ogr.Feature, source_feature: Dict, new_feature: ogr.Feature).
    The source_feature dict contains a key for each attribute and a "geometry" key for the (Shapely) geometry
    """
    # Open the GeoPackage
    data_source = ogr.Open(str(gpkg), 1)  # Open in update mode

    if data_source is None:
        raise RuntimeError(f"Failed to open {gpkg}")

    # Open the layers
    delete_layer = data_source.GetLayerByName(delete_from_layer)
    add_layer = data_source.GetLayerByName(add_to_layer)

    if delete_layer is None or add_layer is None:
        raise RuntimeError("One or both layers could not be found in the GeoPackage")

    add_layer_layer_definition = add_layer.GetLayerDefn()
    add_layer_field_definitions = [
        add_layer_layer_definition.GetFieldDefn(i) for i in range(add_layer_layer_definition.GetFieldCount())
    ]
    delete_fids = []
    results = []
    for code, feature_data in source.items():
        delete_layer.SetAttributeFilter(f"{match_field} = '{match_prefix}{code}{match_postfix}'")
        delete_feature = delete_layer.GetNextFeature()
        if delete_feature:
            delete_fids.append(delete_feature.GetFID())
            new_feature = ogr.Feature(add_layer_layer_definition)
            if config["geometry"].get_from == "source":
                geom = ogr.CreateGeometryFromWkb(feature_data["geometry"].wkb)
            elif config["geometry"].get_from == "delete_layer":
                geom = delete_feature.GetGeometryRef().Clone()
            geom = config["geometry"].parser(geom)
            new_feature.SetGeometry(geom)
            for field_defn in add_layer_field_definitions:
                field_config = config[field_defn.name]
                if field_config.get_from == "source":
                    value = field_config.parser(feature_data[field_config.source_field])
                elif field_config.get_from == "delete_layer":
                    value = field_config.parser(delete_feature.GetField(field_config.source_field))
                new_feature.SetField(field_defn.name, value)

            add_layer.CreateFeature(new_feature)
            results.append((
                delete_feature,
                feature_data,
                new_feature
            ))
            new_feature = None  # Dereference feature

        delete_layer.SetAttributeFilter(None)  # Reset the filter

    for fid in delete_fids:
        delete_layer.DeleteFeature(fid)

    return results


def map_pumps(gpkg: Path, replacement_data: List[Tuple]):
    """
    ``replacement_data`` is what is returned from ``replace_structures``.
    ``orientation`` must be one of "positive", "negative"
    """
    # Open the GeoPackage
    data_source = ogr.Open(str(gpkg), 1)  # Open in update mode

    if data_source is None:
        raise RuntimeError(f"Failed to open {gpkg}")

    target_layer_name = "pumpstation_map"
    target_layer = data_source.GetLayerByName(target_layer_name)

    if target_layer is None:
        raise RuntimeError(f"Layer {target_layer_name} not found")

    layer_definition = target_layer.GetLayerDefn()

    for deleted_feature, source_feature, pump_feature in replacement_data:
        orientation = source_feature["orientation"]
        new_feature = ogr.Feature(layer_definition)
        geom = deleted_feature.GetGeometryRef().Clone()
        if orientation == "negative":
            geom = reverse_line(geom)

        new_feature.SetGeometry(geom)

        # attributes from pump feature
        new_feature.SetField("id", pump_feature.GetField("id"))
        new_feature.SetField("code", pump_feature.GetField("code"))
        new_feature.SetField("display_name", pump_feature.GetField("display_name"))
        new_feature.SetField("pumpstation_id", pump_feature.GetField("id"))

        # attributes from proxy-orifice feature
        start = "connection_node_start_id" if orientation == "positive" else "connection_node_end_id"
        end = "connection_node_end_id" if orientation == "positive" else "connection_node_start_id"
        new_feature.SetField("connection_node_start_id", deleted_feature.GetField(start))
        new_feature.SetField("connection_node_end_id", deleted_feature.GetField(end))

        target_layer.CreateFeature(new_feature)


def clear_gpkg(gpkg: Path, layers_to_clear: List[str]):
    data_source = ogr.Open(gpkg, 1)  # 1 = Read-Write mode
    if data_source is None:
        raise FileNotFoundError(f"Could not open geopackage: {gpkg}")
    else:
        for layer_name in layers_to_clear:
            layer = data_source.GetLayerByName(layer_name)
            if layer is None:
                print(f"Layer '{layer_name}' not found, skipping...")
                continue

            # Delete all features in the layer
            layer.StartTransaction()  # Start a transaction for efficiency

            for feature in layer:
                layer.DeleteFeature(feature.GetFID())

            layer.CommitTransaction()  # Commit changes

            print(f"Cleared all features from '{layer_name}'.")

        # Cleanup
        data_source = None


def dflowfm2threedi(
        target_gpkg: Path,
        mdu_path: Path,
        network_file_path: Path,
        cross_section_locations_path: Path,
        cross_def_path: Path,
        structures_path: Path,
        skip_branches: bool = False,
):
    if not skip_branches:
        print("Extracting nodes...")
        nodes = extract_nodes(network_file=network_file_path)
        print("Importing connection nodes...")
        connection_node_name_id_mapping = import_to_threedi_layer(
            source=nodes,
            target=target_gpkg,
            layer_mapping=connection_node_layer_mapping
        )
    print("Extracting branches...")
    branches = extract_branches(network_file=network_file_path)
    if not skip_branches:
        print("Importing channels...")
        channel_name_id_mapping = import_to_threedi_layer(
            source=branches,
            target=target_gpkg,
            layer_mapping=channel_layer_mapping,
            input_name_id_mapping=connection_node_name_id_mapping
        )
        print("Extracting cross-section locations...")
        cross_section_locations, cross_loc_field_definitions = extract_from_ini(
            ini_file=cross_section_locations_path,
            object_type=CrossSection,
            branches=branches
        )
        print("Importing cross-section locations...")
        cross_section_id_mapping = import_to_threedi_layer(
            source=cross_section_locations,
            target=target_gpkg,
            layer_mapping=cross_section_location_mapping,
            input_name_id_mapping=channel_name_id_mapping,
        )

    # Get cross-section definitions
    print("Reading friction definitions...")
    friction_definitions, branch_friction_definitions = read_friction(mdu_file=mdu_path)
    print("Reading cross-section definitions...")
    cross_section_definitions: Dict[str, ThreeDiCrossSectionData] = read_cross_sections(
        cross_def_path=cross_def_path,
        global_friction_definitions=friction_definitions
    )

    # Also export raw cross-section data to geopackage, for reference/checking purposes
    for cross_section_type in SUPPORTED_CROSS_SECTIONS:
        print(f"Extracting {cross_section_type.__name__.lower()}s...")
        cross_section_definitions_raw, cross_def_field_definitions = extract_from_ini(
            ini_file=cross_def_path,
            object_type=cross_section_type
        )
        print(f"Importing {cross_section_type.__name__.lower()}s...")
        import_table(
            source=cross_section_definitions_raw,
            target=target_gpkg,
            field_definitions=cross_def_field_definitions,
            feature_type=cross_section_type.__name__.lower()
        )

    if not skip_branches:
        # enrich cross_section_definitions with branch friction data
        print("Adding branch friction data to cross-section definitions...")
        cross_section_definitions = {
            id: enrich_cross_section_definition(
                cross_section_definition,
                cross_section_locations,
                branch_friction_definitions,
            )
            for id, cross_section_definition in cross_section_definitions.items()
        }

        cross_section_id_to_defname_mapping = get_cross_section_location_id_to_defname_mapping(
            name_id_mapping=cross_section_id_mapping,
            cross_section_locations=cross_section_locations
        )

        print("Enriching cross-section locations with cross-section definitions and friction data...")
        enrich_cross_section_locations(
            cross_section_data=cross_section_definitions,
            gpkg=target_gpkg,
            cross_section_id_to_defname_mapping=cross_section_id_to_defname_mapping
        )

    for structure_type in SUPPORTED_STRUCTURES:
        print(f"Extracting {structure_type.__name__.lower()}s...")
        extracted_data, field_definitions = extract_from_ini(
            ini_file=structures_path,
            object_type=structure_type,
            branches=branches
        )
        print(f"Importing {structure_type.__name__.lower()}s...")
        import_structures(
                source=extracted_data,
                epsg_code=28992,
                target=target_gpkg,
                cross_section_data=cross_section_definitions,
                field_definitions=field_definitions,
                feature_type=structure_type.__name__.lower()
        )
    pprint(count_structure_types(structures_path))
    check_structures(structures_path)


def orifices_to_pumps(gpkg: Path, network_file: Path, structures_file: Path):
    """
    Import pumps as orifices using the vector data importer, then run this function to replace them with pumps
    (including pump map).

    """
    branches = extract_branches(network_file=network_file)

    extracted_data, field_definitions = extract_from_ini(
        ini_file=structures_file,
        object_type=Pump,
        branches=branches
    )

    positive_pumps = {
        code: feature_data for code, feature_data in extracted_data.items() if feature_data["orientation"] == "positive"
    }
    negative_pumps = {
        code: feature_data for code, feature_data in extracted_data.items() if feature_data["orientation"] == "negative"
    }

    for pump_data, config in [
        (positive_pumps, ORIFICE_TO_POSITIVE_PUMP_REPLACEMENT_CONFIG),
        (negative_pumps, ORIFICE_TO_NEGATIVE_PUMP_REPLACEMENT_CONFIG)
    ]:
        replacement_data = replace_structures(
            gpkg=gpkg,
            source=pump_data,
            delete_from_layer="orifice",
            add_to_layer="pumpstation",
            config=config,
            match_field="code",
            match_prefix="Pump ",
        )
        map_pumps(gpkg=gpkg, replacement_data=replacement_data)


if __name__ == "__main__":
    dsproj_data_dir = Path(r"G:\Projecten Z (2024)\Z0252 - Bovenregionale stresstest wateroverlast OV\Gegevens\Bewerking\6_Omzetting Sobek naar 3Di\Meppelerdiep\Mepperldiep aka Zedemuden saved from GUI\Meppelerdiep.dsproj_data")
    flow_fm_input_path = dsproj_data_dir / "FlowFM" / "input"
    network_file_path = flow_fm_input_path / "FlowFM_net.nc"
    mdu_path = flow_fm_input_path / "FlowFM.mdu"
    cross_section_locations_path = flow_fm_input_path / "crsloc.ini"
    cross_def_path = flow_fm_input_path / "crsdef.ini"
    structures_path = flow_fm_input_path / "structures.ini"

    target_gpkg = Path(
        r"C:\Users\leendert.vanwolfswin\Documents\3Di\Mepperldiep\work in progress\schematisation\Mepperldiep.gpkg"
    )

    # Clear schematisation geopackage (OPTIONAL)
    clear_gpkg(
        gpkg=target_gpkg,
        layers_to_clear=[
            "connection_node",
            "channel",
            "cross_section_location",
            "culvert",
            "orifice",
            "weir",
            "pumpstation",
            "pumpstation_map",
        ]
    )

    # Export DFlowFM data to 3Di
    dflowfm2threedi(
        target_gpkg=target_gpkg,
        mdu_path=mdu_path,
        network_file_path=network_file_path,
        cross_section_locations_path=cross_section_locations_path,
        cross_def_path=cross_def_path,
        structures_path=structures_path,
        # skip_branches=True,
    )

    ##############################################################
    # BEFORE CONTINUING, RUN ALL THE VECTOR DATA IMPORTERS FIRST #
    ##############################################################

    # Replace pump-proxy orifices for real pumps
    # orifices_to_pumps(gpkg=target_gpkg, network_file=network_file_path, structures_file=structures_path)

    print("Klaar")

