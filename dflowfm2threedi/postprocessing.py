from pathlib import Path
from typing import List, Dict, Set, Tuple

from osgeo import ogr

ogr.UseExceptions()

NETWORK_OBJECTS = [
    "channel",
    "culvert",
    "orifice",
    "pipe",
    "pump_map",
    "weir"
]
MAPPING_OBJECTS = [
    "surface_map",
]
POINT_OBJECTS = [
    "boundary_condition_1d",
    "lateral_1d",
    "pump"
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
                channel_id=channel.GetFID(),
                connection_node_id=channel["connection_node_id_start"],
                target_object_types=NETWORK_OBJECTS
            )
            network_referencing_end = self.get_referencing_features(
                channel_id=channel.GetFID(),
                connection_node_id=channel["connection_node_id_end"],
                target_object_types=NETWORK_OBJECTS
            )
            self.reference_dict[channel.GetFID()] = {
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
            "connection_node_id_start",
            "connection_node_id_end"
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

    def _reconnect(self):
        gpkg = self.data_source.GetName()
        self.data_source = None
        self.data_source = ogr.Open(gpkg, 1)

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
                        if not (layer_name == "channel" and feature.GetFID() == channel_id):  # exclude channel self-references
                            result.append((layer_name, field_name, feature))
        return result

    def delete_channel(self, channel):
        channel_id = channel.GetFID()
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
            connection_node_id_to_delete = channel["connection_node_id_end"]
            connection_node_id_replacement = channel["connection_node_id_start"]
        else:
            connection_node_id_to_delete = channel["connection_node_id_start"]
            connection_node_id_replacement = channel["connection_node_id_end"]
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

    def replace_connection_node(
            self,
            data_source,
            layer_name,
            feature_fid,
            delete_id,
            replacement_id
    ):
        """
        Update the attribute of a feature in ``layer_name`` that refers to a deleted connection node
        And update the geometry of that feature
        """
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
        elif feature["connection_node_id_start"] == delete_id:
            field_to_be_updated = "connection_node_id_start"
            first_or_last = "first"
        elif feature["connection_node_id_end"] == delete_id:
            field_to_be_updated = "connection_node_id_end"
            first_or_last = "last"
        else:
            raise RuntimeError(
                f"This feature's connection_node_id_start and connection_node_id_end are not delete_id ({delete_id})"
            )
        feature.SetField(field_to_be_updated, replacement_id)  # Replace with the new value
        target_geom = feature.GetGeometryRef()
        connection_node_layer = data_source.GetLayerByName("connection_node")
        connection_node_layer.SetAttributeFilter(f"id={replacement_id}")
        replacement_connection_node = connection_node_layer.GetNextFeature()
        vertex_geom = replacement_connection_node.GetGeometryRef()
        move_vertex_in_geometry(target_geom=target_geom, new_vertex=vertex_geom, first_or_last=first_or_last)
        feature.SetGeometry(target_geom)

        # Save changes to the layer
        layer.SetFeature(feature)
        feature_id = feature.GetFID()
        feature = None  # Free memory

        # Update index for this layer
        index = self.indices[layer_name]
        if delete_id in index:  # TODO fix this properly.
            old_index_entry = index[delete_id]
            new_index_entry = set()
            for connection_node_field, feature in old_index_entry:
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
        else:
            print(f"Connection node {delete_id} not found in index for {layer_name} when updating feature {feature_id}. "
                  f"Not updating its index!")

    def delete_zero_length_channels(self, channel_ids: List = None):
        """Deletes all channels that have connection_node_id_start == connection_node_id_end"""
        channels = self.data_source.GetLayerByName("channel")
        cross_section_locations = self.data_source.GetLayerByName("cross_section_location")
        if channel_ids:
            channels.SetAttributeFilter(f"id in ({','.join(channel_ids)})")

        deleted_fids = []
        for channel in channels:
            if channel["connection_node_id_start"] == channel["connection_node_id_end"]:
                # cross_section_locations.SetAttributeFilter(f"channel_id = {channel['id']}")
                cross_section_locations.SetAttributeFilter(f"channel_id = {channel.GetFID()}")
                for cross_section_location in cross_section_locations:
                    cross_section_locations.DeleteFeature(cross_section_location.GetFID())
                fid = channel.GetFID()
                channels.DeleteFeature(fid)
                deleted_fids.append(fid)

                # Update index for channel layer
                connection_node_id = channel["connection_node_id_start"]
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

    def update_pump_map_geometries(self):
        self._reconnect()
        # Get layers
        pumps = self.data_source.GetLayer("pump")
        connection_nodes = self.data_source.GetLayer("connection_node")
        pump_maps = self.data_source.GetLayer("pump_map")

        # Build pump and connection_node geometry dictionaries
        pump_geom_dict = {}
        for feature in pumps:
            pump_id = feature.GetFID()
            geom = feature.GetGeometryRef().Clone()
            pump_geom_dict[pump_id] = geom
        pumps.ResetReading()

        connection_node_geom_dict = {}
        for feature in connection_nodes:
            connection_node_id = feature.GetFID()
            geom = feature.GetGeometryRef().Clone()
            connection_node_geom_dict[connection_node_id] = geom
        connection_nodes.ResetReading()

        # Update each pump_map feature
        for feature in pump_maps:
            pump_id = feature.GetField("pump_id")
            connection_node_id = feature.GetField("connection_node_id_end")

            pump_geom = pump_geom_dict.get(pump_id)
            connection_node_geom = connection_node_geom_dict.get(connection_node_id)

            if pump_geom is not None and connection_node_geom is not None:
                line = ogr.Geometry(ogr.wkbLineString)
                line.AddPoint(*pump_geom.GetPoint_2D())
                line.AddPoint(*connection_node_geom.GetPoint_2D())

                feature.SetGeometry(line)
                pump_maps.SetFeature(feature)

    def run(self, channel_ids: List = None):
        self.delete_zero_length_channels(channel_ids=channel_ids)
        for channel in self.short_channels:
            if channel_ids:
                if channel.GetFID() in channel_ids:
                    self.delete_channel(channel)
            else:
                self.delete_channel(channel)
        self.update_pump_map_geometries()


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
        r"C:\Users\leendert.vanwolfswin\Documents\3Di\Vector data importers test schematisation\work in progress\schematisation\Vector data importers test schematisation.gpkg",
        threshold=5
    )
    scd.run()
    print("Done! If you have this schematisation open in the 3Di Modeller Interface, please remove it from the project "
          "and load it again.")
