gdal-hillshade
==============

Scripts for creating hillshade layers from SRTM data

Files
=====
index.html      - hillshade preview from local tile server
metatile4.xml   - 4x4 metatile TMS service definition for GDAL driver
process         - Process raw data and create "big" GeoTIFF
putalpha.py     - Convert grayscale hillshade image to PNG tile
server.py       - Serve tiles/metatiles from GDAL source
split           - split "big" GeoTIFF file to smaller 1024x1024 tiles
tiles.xml       - Standard TMS service definition for GDAL driver
