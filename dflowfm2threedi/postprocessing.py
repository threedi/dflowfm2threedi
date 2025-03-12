from pathlib import Path
from pprint import pprint
from typing import List, Dict, Set, Tuple

from osgeo import ogr

ogr.UseExceptions()

NETWORK_OBJECTS = [
    "channel",
    "culvert",
    "orifice",
    "pipe",
    "pumpstation_map",
    "weir"
]
MAPPING_OBJECTS = [
    "impervious_surface_map",
    "surface_map",
]
POINT_OBJECTS = [
    "1d_boundary_condition",
    "1d_lateral",
    "manhole",
    "pumpstation"
]
ALL_OBJECTS = NETWORK_OBJECTS + MAPPING_OBJECTS + POINT_OBJECTS


class ShortChannelDeleter:
    def __init__(self, gpkg: str | Path, threshold: float):
        self.data_source = ogr.Open(str(gpkg), 1)
        self.short_channels = self._get_short_channels(threshold=threshold)
        self.indices = {
            layer_name: self._create_index(layer_name) for layer_name in ALL_OBJECTS
        }
        self.reference_dict = dict()
        self._update_reference_dict(self.short_channels)
        self._replaced_connection_nodes = dict()

    def _update_reference_dict(self, channels: List):
        for channel in channels:
            network_referencing_start = self.get_referencing_features(
                channel_id=channel["id"],
                connection_node_id=channel["connection_node_start_id"],
                target_object_types=NETWORK_OBJECTS
            )
            network_referencing_end = self.get_referencing_features(
                channel_id=channel["id"],
                connection_node_id=channel["connection_node_end_id"],
                target_object_types=NETWORK_OBJECTS
            )
            self.reference_dict[channel["id"]] = {
                "start": network_referencing_start,
                "end": network_referencing_end,
            }

    def _get_short_channels(self, threshold: float):
        channels = self.data_source.GetLayerByName("channel")
        return [channel for channel in channels if channel.GetGeometryRef().Length() < threshold]

    def _create_index(self, layer_name: str) -> Dict[int, Set[Tuple]]:
        """
        Returns a {connection_node_id: {(connection_node_field, feature), (connection_node_field, feature)}} dict
        """
        index = dict()
        possible_connection_node_field_names = {
            "connection_node_id",
            "connection_node_start_id",
            "connection_node_end_id"
        }
        layer = self.data_source.GetLayerByName(layer_name)
        layer_field_names = {field.name for field in layer.schema}
        connection_node_fields = possible_connection_node_field_names & layer_field_names
        for feature in layer:
            for connection_node_field in connection_node_fields:
                connection_node_id = feature[connection_node_field]
                if connection_node_id in index:
                    index[connection_node_id].add((connection_node_field, feature))
                else:
                    index[connection_node_id] = {(connection_node_field, feature)}
        return index

    def replaced_connection_node_id(self, old_connection_node_id: int):
        current_id = old_connection_node_id
        while current_id in self._replaced_connection_nodes:
            current_id = self._replaced_connection_nodes[current_id]
        return current_id

    def get_referencing_features(self, channel_id, connection_node_id, target_object_types: List = None) -> List:
        """
        Returns list of (layer_name, field_name, feature) tuples
        """
        result = list()
        for layer_name, all_references in self.indices.items():
            # all_references is a set of (field_name, feature) tuples
            if layer_name in target_object_types:
                if connection_node_id in all_references:
                    references = all_references[connection_node_id]
                    for field_name, feature in references:
                        if not (layer_name == "channel" and feature["id"] == channel_id):  # exclude channel self-references
                            result.append((layer_name, field_name, feature))
        return result

    def delete_channel(self, channel):
        channel_id = channel["id"]
        try:
            references = self.reference_dict[channel_id]
            # references is a dict that contains two lists of (layer_name, field_name, feature) tuples
            # one for "start" and one for "end"
        except KeyError:
            raise RuntimeError(
                f"Channel with ID {channel_id} is not a short channel or is not connected to anything else"
            )

        # Do not delete the channel if any other object connects its start and end
        start_referencing_features = {feature for _, _, feature in references["start"]}
        end_referencing_features = {feature for _, _, feature in references["end"]}
        if start_referencing_features & end_referencing_features:
            return

        # Find out which connection node has the least connections with:
        if len(references["start"]) > len(references["end"]):
            connection_node_id_to_delete = channel["connection_node_end_id"]
            connection_node_id_replacement = channel["connection_node_start_id"]
        else:
            connection_node_id_to_delete = channel["connection_node_start_id"]
            connection_node_id_replacement = channel["connection_node_end_id"]
        connection_node_id_replacement = self.replaced_connection_node_id(connection_node_id_replacement)
        referencing_features = self.get_referencing_features(
            channel_id=channel_id,
            connection_node_id=connection_node_id_to_delete,
            target_object_types=ALL_OBJECTS
        )
        # Update all referencing features
        for layer_name, field_name, feature in referencing_features:
            self.replace_connection_node(
                data_source=self.data_source,
                layer_name=layer_name,
                feature_fid=feature.GetFID(),
                delete_id=connection_node_id_to_delete,
                replacement_id=connection_node_id_replacement,
            )
            if layer_name == "channel":
                self._update_reference_dict(channels=[feature])

        # Delete all cross-section locations that reference this channel
        cross_section_locations = self.data_source.GetLayerByName("cross_section_location")
        cross_section_locations.SetAttributeFilter(f"channel_id = {channel_id}")
        for cross_section_location in cross_section_locations:
            cross_section_locations.DeleteFeature(cross_section_location.GetFID())

        # Delete the connection node
        connection_nodes = self.data_source.GetLayerByName("connection_node")
        connection_nodes.SetAttributeFilter(f"id = {connection_node_id_to_delete}")
        for connection_node in connection_nodes:
            connection_nodes.DeleteFeature(connection_node.GetFID())
        self._replaced_connection_nodes[connection_node_id_to_delete] = connection_node_id_replacement

        # Delete the channel
        channels = self.data_source.GetLayerByName("channel")
        channels.DeleteFeature(channel.GetFID())

        # Remove the channel from self.reference_dict
        self.reference_dict.pop(channel_id)

    def replace_connection_node(self, data_source, layer_name, feature_fid, delete_id, replacement_id):
        layer = data_source.GetLayerByName(layer_name)
        feature = layer.GetFeature(feature_fid)
        if layer_name == "channel" and feature is None:
            return  # channel has already been deleted

        field_names = [field.name for field in layer.schema]
        if "connection_node_id" in field_names:
            if feature["connection_node_id"] == delete_id:
                field_to_be_updated = "connection_node_id"
                first_or_last = "first"
            else:
                raise RuntimeError(f"This feature's connection_node_id != delete_id ({delete_id})")
        elif feature["connection_node_start_id"] == delete_id:
            field_to_be_updated = "connection_node_start_id"
            first_or_last = "first"
        elif feature["connection_node_end_id"] == delete_id:
            field_to_be_updated = "connection_node_end_id"
            first_or_last = "last"
        else:
            raise RuntimeError(
                f"This feature's connection_node_start_id and connection_node_end_id are not delete_id ({delete_id})"
            )
        feature.SetField(field_to_be_updated, replacement_id)  # Replace with the new value
        target_geom = feature.GetGeometryRef()
        geom_name = target_geom.GetGeometryName()
        geom_type = target_geom.GetGeometryType()
        connection_node_layer = data_source.GetLayerByName("connection_node")
        connection_node_layer.SetAttributeFilter(f"id={replacement_id}")
        replacement_connection_node = connection_node_layer.GetNextFeature()
        vertex_geom = replacement_connection_node.GetGeometryRef()
        move_vertex_in_geometry(target_geom=target_geom, new_vertex=vertex_geom, first_or_last=first_or_last)
        geom_name = target_geom.GetGeometryName()
        geom_type = target_geom.GetGeometryType()
        feature.SetGeometry(target_geom)

        # Save changes to the layer
        layer.SetFeature(feature)
        feature = None  # Free memory

        # Update index for this layer
        index = self.indices[layer_name]
        new_index_entry = set()
        for connection_node_field, feature in index[delete_id]:
            index_entry_item = (connection_node_field, feature)
            feature_layer_name = feature.GetDefnRef().GetName()
            if feature_layer_name == layer_name and connection_node_field == field_to_be_updated:
                if replacement_id in index:
                    index[replacement_id].add(index_entry_item)
                else:
                    index[replacement_id] = {index_entry_item}
            else:
                new_index_entry.add(index_entry_item)
        if new_index_entry:
            index[delete_id] = new_index_entry
        else:
            index.pop(delete_id)
        self.indices[layer_name] = index

    def delete_zero_length_channels(self, channel_ids: List = None):
        """Deletes all channels that have connection_node_start_id == connection_node_end_id"""
        channels = self.data_source.GetLayerByName("channel")
        cross_section_locations = self.data_source.GetLayerByName("cross_section_location")
        if channel_ids:
            channels.SetAttributeFilter(f"id in ({','.join(channel_ids)})")

        deleted_fids = []
        for channel in channels:
            if channel["connection_node_start_id"] == channel["connection_node_end_id"]:
                cross_section_locations.SetAttributeFilter(f"channel_id = {channel['id']}")
                for cross_section_location in cross_section_locations:
                    cross_section_locations.DeleteFeature(cross_section_location.GetFID())
                fid = channel.GetFID()
                channels.DeleteFeature(fid)
                deleted_fids.append(fid)

                # Update index for channel layer
                connection_node_id = channel["connection_node_start_id"]
                index = self.indices["channel"]
                new_index_entry = set()
                for connection_node_field, feature in index[connection_node_id]:
                    if not feature.GetFID() == fid:
                        index_entry_item = (connection_node_field, feature)
                        new_index_entry.add(index_entry_item)
                if new_index_entry:
                    index[connection_node_id] = new_index_entry
                else:
                    index.pop(connection_node_id)
                self.indices["channel"] = index

        # update administration
        self.short_channels = [channel for channel in self.short_channels if channel.GetFID() not in deleted_fids]

    def run(self, channel_ids: List = None):
        self.delete_zero_length_channels(channel_ids=channel_ids)
        for channel in self.short_channels:
            if channel_ids:
                if channel["id"] in channel_ids:
                    self.delete_channel(channel)
            else:
                self.delete_channel(channel)


def move_vertex_in_geometry(target_geom, new_vertex, first_or_last: str):
    new_x, new_y = new_vertex.GetX(), new_vertex.GetY()
    num_vertices = target_geom.GetPointCount()
    index = 0 if first_or_last == "first" else num_vertices - 1

    if target_geom.GetCoordinateDimension() == 3:
        # Preserve Z only if original geometry is 3D
        new_z = new_vertex.GetZ() if new_vertex.GetCoordinateDimension() == 3 else 0
        target_geom.SetPoint(index, new_x, new_y, new_z)  # Use Z-coordinate
    else:
        target_geom.SetPoint_2D(index, new_x, new_y)  # Force 2D output


if __name__ == "__main__":
    scd = ShortChannelDeleter(
        r"C:\Users\leendert.vanwolfswin\Documents\3Di\Stroink\work in progress\schematisation\Stroink.gpkg",
        threshold=5
    )
    scd.run()
    print("Done! If you have this schematisation open in the 3Di Modeller Interface, please remove it from the project "
          "and load it again.")
