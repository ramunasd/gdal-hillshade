gdal-hillshade
==============

Scripts for creating hillshade layers from SRTM data

Files
=====
* index.html - preview hllshade from local tile server
* metatile4.xml - 4x4 metatile TMS service definition for GDAL driver
* process - process raw data and create "big" GeoTIFF
* putalpha.py - convert grayscale hillshade image to PNG tile
* server.py - serve tiles/metatiles from GDAL source
* split - split "big" GeoTIFF file to smaller 1024x1024 tiles
* tiles.xml - standard TMS service definition for GDAL driver
