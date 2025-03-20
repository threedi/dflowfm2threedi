# dflowfm2threedi

#### *Workflow for converting SOBEK/D-Hydro/D-FlowFM 1D networks to 3Di schematisations*

This software can make your life much easier if converting SOBEK/D-Hydro/D-FlowFM to 3Di has become a central concern to you. However, 

**no guarantees as to the correctness of the conversion are given whatsoever.** 

Please check the result thoroughly.

Leendert van Wolfswinkel, February 2025

## Versions 

This manual is based on using the following versions:
- D-HYDRO Suite 2025.01 1D2D
- 3Di Schematisation Editor 1.16
- 3Di database schema version 219

## Installation
- Required packages: hydrolib-core (pip install hydrolib-core)
- Required packages: h5netcdf (pip install h5netcdf)

## SOBEK to D-HYDRO

- Open D-HYDRO Suite 1D2D
- New Integrated model
- Import > Sobek 2 import
- Browse to the CASELIST.CMT file > Select the case you want to work with
- Next > Next > Next
- Carefully check warnings and errors. Save them in a file. If there are any errors, the quickest fix is to skip those parts of the import and think of a custom workaround later in the process
- If D-Hydro complains about duplicates in the friction file, you can use ``sobek_utils.deduplicate_friction_file()`` to fix this and try the import again.
- Save the project. Remember where you saved it. D-Hydro saves a file "{project name}.dsproj" and a directory "{project name}.dsproj_data". This will be referred to as the "dsproj_data directory"
 

## Importing to 3Di

- You need a 3Di schematisation geopackage as created by the 3Di Schematisation Editor version 1.16. Create a new schematisation or use an existing one and open the Spatialite with the 3Di Schematisation Editor; the needed geopackage will be created.
- **REMOVE THE SCHEMATISATION FROM YOUR QGIS PROJECT BEFORE PROCEEDING**
- In the script dflowfm2threedi.py, scroll down to the section after ``if __name__ == "__main__":``
- Change the paths to your situation
- The script has three steps, that you will want to perform one by one. Comment out the other steps when running:
  1. Clear schematisation geopackage (OPTIONAL)
  2. Export DFlowFM data to 3Di
  3. BEFORE CONTINUING, RUN ALL THE VECTOR DATA IMPORTERS FIRST
  4. Replace pump-proxy orifices for real pumps
- Run steps 1 and 2 of the script
- The needed layers have been written to the 3Di schematisation geopackage as ``dhydro_{layer name}``. These layers are copies of the shapefile layers, enriched with data from the dsproj_data directory.
- Use the Open 3Di Geopackage option to add the schematisation to your QGIS project


### Culverts

It is recommended to imported D-Hydro culverts in 3 categories, based on their length:
- < 5m: short crested orifice. Energy losses due to friction in the pipe are negligible; the weir formula that is used is fast, stable, and accurate.
- 5 - 25 m: broad crested orifice. Energy losses due to friction in the pipe are not negligible; the weir formula that is used is fast, stable, and accurate and includes frictional losses.
- \> 25 m: culvert. Energy losses due to friction in the pipe are not negligible; The propagation of a discharge wave through the culvert may be relevant, so multiple calculation points are used within the culvert (calculation point distance is 25 m).

Do the following:
- Add the layer ``dhydro_culvert`` to the project
- Set a filter ``length >= 25``
- Rename the layer to ``dhydro_culvert >= 25m``
- Duplicate the layer
- Rename the copy to ``dhydro_culvert < 25m``
- Set the filter of the copy to ``length < 25``

Now you import both layers to your 3Di schematisation:
- In the 3Di Schematisation Editor toolbar, click "Import schematisation objects" > Culverts
- As Source culvert layer, choose ``dhydro_culvert >= 25m``
- Load template > ``culvert_long.json``
- Check the import settings so you understand what is going on. If you spot any mistakes, update the configuration json and commit the changes to GitHub.
- Run

- In the 3Di Schematisation Editor toolbar, click "Import schematisation objects" > Orifices
- As Source orifice layer, choose ``dhydro_culvert < 25m``
- Load template > ``culvert_short.json``
- Check the import settings so you understand what is going on. If you spot any mistakes, update the configuration json and commit the changes to GitHub.
- Run

**NOTE:** Schematisation Editor 1.16 contains a bug in the vector data importer. The length field is ignored, and instead the fallback value is always used. A dev version in which this bug has been fixed is available **
**NOTE:** Schematisation Editor 1.16 contains a bug in the vector data importer. The FIDs for newly generated cross-section locations are not unique, and can therefore not be committed. A dev version in which this bug has been fixed is available **

### Bridges

Bridges are imported as orifices. The crest type depends on the length:
- < 5m: short crested orifice. Energy losses due to friction under the bridge are negligible; the weir formula that is used is fast, stable, and accurate.
- \> 5 - 25 m: broad crested orifice. Energy losses due to friction under the bridge are not negligible; the weir formula that is used is fast, stable, and accurate and includes frictional losses.

Do the following:
- Add the layer ``dhydro_bridge`` to the project
- In the 3Di Schematisation Editor toolbar, click "Import schematisation objects" > Orifices
- As Source culvert layer, choose ``dhydro_bridge``
- Load template > ``bridge.json``
- Check the import settings so you understand what is going on. If you spot any mistakes, update the configuration json and commit the changes to GitHub.
- Run

### Orifices

The import configuration imports the D-Hydro orifices as short-crested orifices with a closed rectangle shape in 3Di. The "gate lower edge level" is used as the height in the cross-section.  The line length of the orifice in the 3Di schematisation is 1 m.

Do the following:
- Add the layer ``dhydro_orifice`` to the project
- In the 3Di Schematisation Editor toolbar, click "Import schematisation objects" > Orifices
- As Source orifice layer, choose ``dhydro_orifice``
- Load template > ``orifice.json``
- Check the import settings so you understand what is going on. If you spot any mistakes, update the configuration json and commit the changes to GitHub.
- Run

### Weirs

The import configuration imports the D-Hydro weirs as short-crested weirs with an open rectangle shape in 3Di. The line length of the weir in the 3Di schematisation is 1 m.

Do the following:
- Add the layer ``dhydro_weir`` to the project
- In the 3Di Schematisation Editor toolbar, click "Import schematisation objects" > Weirs
- As Source orifice layer, choose ``dhydro_weir``
- Load template > ``weir.json``
- Check the import settings so you understand what is going on. If you spot any mistakes, update the configuration json and commit the changes to GitHub.
- Run

### Pumps

At the moment of writing, there is no vector data importer available for pumps. However, we can use the existing functionality of the vector data importers to cut the pumps out of the channel network. The way we do this is by importing the pumps as dummy orifices, which will subsequently be replaced by pumps and pump_maps.

Do the following: 
- Add the layer ``dhydro_pump`` to the project
- In the 3Di Schematisation Editor toolbar, click "Import schematisation objects" > Orifices
- As Source orifice layer, choose ``dhydro_pump``
- Load template > ``pump_proxy_orifice.json``
- Run
- Remove the schematisation from the project
- Run step 4 of the script (4. Replace pump-proxy orifices for real pumps)

## Checks and other actions after importing
- Carefully check the result, do not assume that the script and importers are perfect.  
- Manholes do not have a bottom level. Not sure if this is a problem, I think 3Di just uses the reference level / invert level of the adjacent channel or culvert. If it is a problem, you may want to run the "Manhole bottom level from pipes" tool with culverts as input.
- Same for drain level
- Check & fix any channels that are shorter than 5 m: see `postprocessing.py`
- Check & fix any structures that are not connected to the network. You can run this SQL in the database manager to identify culverts that are not connected to the network on one or both sides:

**Culverts**
    
    with cono_ids as (
        select connection_node_start_id as id from channel where connection_node_start_id != connection_node_end_id
        union
        select connection_node_end_id as id from channel where connection_node_start_id != connection_node_end_id
        union
        select connection_node_start_id as id from weir where connection_node_start_id != connection_node_end_id
        union
        select connection_node_end_id as id from weir where connection_node_start_id != connection_node_end_id
        union
        select connection_node_start_id as id from orifice where connection_node_start_id != connection_node_end_id
        union
        select connection_node_end_id as id from orifice where connection_node_start_id != connection_node_end_id
		union
        select connection_node_start_id as id from pumpstation_map where connection_node_start_id != connection_node_end_id
        union
        select connection_node_end_id as id from pumpstation_map where connection_node_start_id != connection_node_end_id
    )
    SELECT DISTINCT culvert.* 
    FROM culvert
    where 
        connection_node_start_id not in (select id from cono_ids)
        or
        connection_node_end_id not in (select id from cono_ids)
    ;

**Orifices**

    with cono_ids as (
            select connection_node_start_id as id from channel where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from channel where connection_node_start_id != connection_node_end_id
            union
            select connection_node_start_id as id from weir where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from weir where connection_node_start_id != connection_node_end_id
            union
            select connection_node_start_id as id from culvert where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from culvert where connection_node_start_id != connection_node_end_id
			union
			select connection_node_start_id as id from pumpstation_map where connection_node_start_id != connection_node_end_id
			union
			select connection_node_end_id as id from pumpstation_map where connection_node_start_id != connection_node_end_id
        )
    SELECT DISTINCT orifice.* 
    FROM orifice
    where 
        connection_node_start_id not in (select id from cono_ids)
        or
        connection_node_end_id not in (select id from cono_ids)
    ;

**Weirs**

    with cono_ids as (
            select connection_node_start_id as id from channel where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from channel where connection_node_start_id != connection_node_end_id
            union
            select connection_node_start_id as id from orifice where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from orifice where connection_node_start_id != connection_node_end_id
            union
            select connection_node_start_id as id from culvert where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from culvert where connection_node_start_id != connection_node_end_id
			union
			select connection_node_start_id as id from pumpstation_map where connection_node_start_id != connection_node_end_id
			union
			select connection_node_end_id as id from pumpstation_map where connection_node_start_id != connection_node_end_id
        )
    SELECT DISTINCT weir.* 
    FROM weir
    where 
        connection_node_start_id not in (select id from cono_ids)
        or
        connection_node_end_id not in (select id from cono_ids)
    ;

**Pumpstation map**


    with cono_ids as (
            select connection_node_start_id as id from channel where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from channel where connection_node_start_id != connection_node_end_id
            union
            select connection_node_start_id as id from weir where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from weir where connection_node_start_id != connection_node_end_id
            union
            select connection_node_start_id as id from culvert where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from culvert where connection_node_start_id != connection_node_end_id
            union
            select connection_node_start_id as id from orifice where connection_node_start_id != connection_node_end_id
            union
            select connection_node_end_id as id from orifice where connection_node_start_id != connection_node_end_id

    )
    SELECT DISTINCT pumpstation_map.* 
    FROM pumpstation_map
    where 
        connection_node_start_id not in (select id from cono_ids)
        or
        connection_node_end_id not in (select id from cono_ids)
    ;


- Run the schematisation checker and try to fix all errors and warnings.


## Technical reference

### Translation of D-Hydro friction types to 3Di

The following logic is applied:

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
        # is far from perfect but gives a good approx. ASSUMED_WATER_DEPTH = 1
    elif self.friction_type is None:
        friction_type = ThreeDiFrictionType.NONE
        friction_value = None
    else:
        friction_type = ThreeDiFrictionType.NONE
        friction_value = None
        conversion_success = False
        failure_reason = f"Unknown friction type {self.friction_type}"


## Troubleshooting
You might run into errors. This is a list of possible errors

### Unknown keywords are detected

Example:
```
pydantic.v1.error_wrappers.ValidationError: 1 validation error for CrossLocModel
crsloc.ini -> crosssection -> 8862 -> __root__
  Unknown keywords are detected in section: 'CrossSection', '['name']' (type=value_error)
```

To fix this: look for the keyword (in this case 'name') in the file crsloc and delete the line.

You can delete all such lines using regex-find and replace in Notepad++.

- Find what: `^\s*name\s*=.*\r?\n?`
- Replace with: *leave empty*
- Check the "wrap around" checkbox
- Search mode: regular expression
- Replace all

