#!/usr/bin/env python

import sys
import os
import math
import tempfile
from PIL import Image
from cStringIO import StringIO
from contrib.GlobalMercator import GlobalMercator

from werkzeug.serving import run_simple
from werkzeug import BaseResponse, wrap_file

try:
    from osgeo import gdal
    from osgeo import osr
except:
    import gdal
    print('You are using "old gen" bindings. gdal2tiles needs "new gen" bindings.')
    sys.exit(1)

MAXZOOMLEVEL = 32
resampling_list = ('near','bilinear','cubic','cubicspline','lanczos')

# =============================================================================
# =============================================================================
# =============================================================================

class GDAL2Tile(object):
    # -------------------------------------------------------------------------
    def error(self, msg, details = "" ):
        """Print an error message and stop the processing"""

        if details:
            self.parser.error(msg + "\n\n" + details)
        else:
            self.parser.error(msg)

    # -------------------------------------------------------------------------
    def __init__(self, arguments):
        """Constructor function - initialization"""
        self.input = None

        # Tile format
        self.tilesize = 256
        self.tiledriver = 'PNG'
        self.tileext = 'png'

        # RUN THE ARGUMENT PARSER:
        self.optparse_init()
        self.options, self.args = self.parser.parse_args(args=arguments)
        if not self.args:
            self.error("No input file specified")

        self.input = self.args[0]

        # Supported options
        self.resampling = None

        if self.options.resampling == 'near':
            self.resampling = gdal.GRA_NearestNeighbour

        elif self.options.resampling == 'bilinear':
            self.resampling = gdal.GRA_Bilinear

        elif self.options.resampling == 'cubic':
            self.resampling = gdal.GRA_Cubic

        elif self.options.resampling == 'cubicspline':
            self.resampling = gdal.GRA_CubicSpline

        elif self.options.resampling == 'lanczos':
            self.resampling = gdal.GRA_Lanczos

        if self.options.meta != 1:
            self.tilesize *= self.options.meta
        
        # Output the results
        if self.options.verbose:
            print("Options:", self.options)
            print("Input:", self.input)
            print("Cache: %s MB" % (gdal.GetCacheMax() / 1024 / 1024))
            print('')

    # -------------------------------------------------------------------------
    def optparse_init(self):
        """Prepare the option parser for input (argv)"""

        from optparse import OptionParser, OptionGroup
        usage = "Usage: %prog [options] input_file"
        p = OptionParser(usage)
        p.add_option("-r", "--resampling", dest="resampling", type='choice', choices=resampling_list,
                        help="Resampling method (%s) - default 'average'" % ",".join(resampling_list))
        p.add_option('-a', '--srcnodata', dest="srcnodata", metavar="NODATA",
                          help="NODATA transparency value to assign to the input data")
        p.add_option("-v", "--verbose",
                          action="store_true", dest="verbose",
                          help="Print status messages to stdout")
        p.add_option("-p", "--processes", action="store", type="int",
                            dest="processes", metavar="COUNT", help="Concurrent processes count")
        p.add_option("-m", "--metatile", action="store", type="int",
                            dest="meta", metavar="META", help="Meta tile size(default=1)")

        p.set_defaults(verbose=False, resampling='cubicspline', processes=1, meta=1)
        self.parser = p

    # -------------------------------------------------------------------------
    def open_input(self):
        """Initialization of the input raster, reprojection if necessary"""

        gdal.AllRegister()

        # Initialize necessary GDAL drivers

        self.out_drv = gdal.GetDriverByName(self.tiledriver)
        self.mem_drv = gdal.GetDriverByName('MEM')

        if not self.out_drv:
            raise Exception("The '%s' driver was not found, is it available in this GDAL build?", self.tiledriver)
        if not self.mem_drv:
            raise Exception("The 'MEM' driver was not found, is it available in this GDAL build?")

        # Open the input file
        if self.input:
            self.in_ds = gdal.Open(self.input, gdal.GA_ReadOnly)
        else:
            raise Exception("No input file was specified")

        if self.options.verbose:
            print("Input file:", "( %sP x %sL - %s bands)" % (self.in_ds.RasterXSize, self.in_ds.RasterYSize, self.in_ds.RasterCount))

        if not self.in_ds:
            # Note: GDAL prints the ERROR message too
            self.error("It is not possible to open the input file '%s'." % self.input )

        # Read metadata from the input file
        if self.in_ds.RasterCount == 0:
            self.error( "Input file '%s' has no raster band" % self.input )

        if self.in_ds.GetRasterBand(1).GetRasterColorTable():
            self.error( "Please convert this file to RGB/RGBA and run gdal2tiles on the result.",
            """From paletted file you can create RGBA file (temp.vrt) by:
gdal_translate -of vrt -expand rgba %s temp.vrt
then run:
gdal2tiles temp.vrt""" % self.input )

        # Get NODATA value
        self.in_nodata = []
        for i in range(1, self.in_ds.RasterCount+1):
            if self.in_ds.GetRasterBand(i).GetNoDataValue() != None:
                self.in_nodata.append( self.in_ds.GetRasterBand(i).GetNoDataValue() )
        if self.options.srcnodata:
            nds = list(map( float, self.options.srcnodata.split(',')))
            if len(nds) < self.in_ds.RasterCount:
                self.in_nodata = (nds * self.in_ds.RasterCount)[:self.in_ds.RasterCount]
            else:
                self.in_nodata = nds

        if self.options.verbose:
            print("NODATA: %s" % self.in_nodata)

        #
        # Here we should have RGBA input dataset opened in self.in_ds
        #

        if self.options.verbose:
            print("Preprocessed file:", "( %sP x %sL - %s bands)" % (self.in_ds.RasterXSize, self.in_ds.RasterYSize, self.in_ds.RasterCount))

        # Spatial Reference System of the input raster
        self.in_srs = None

        self.in_srs_wkt = self.in_ds.GetProjection()
        if not self.in_srs_wkt and self.in_ds.GetGCPCount() != 0:
            self.in_srs_wkt = self.in_ds.GetGCPProjection()
        if self.in_srs_wkt:
            self.in_srs = osr.SpatialReference()
            self.in_srs.ImportFromWkt(self.in_srs_wkt)

        # Spatial Reference System of tiles
        self.out_srs = osr.SpatialReference()
        self.out_srs.ImportFromEPSG(900913)
        
        self.ds = None

        if (self.in_ds.GetGeoTransform() == (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)) and (self.in_ds.GetGCPCount() == 0):
            self.error("There is no georeference - neither affine transformation (worldfile) nor GCPs.")
            
        if self.in_srs:
            if (self.in_srs.ExportToProj4() != self.out_srs.ExportToProj4()) or (self.in_ds.GetGCPCount() != 0):
                # Generation of VRT dataset in tile projection, default 'nearest neighbour' warping
                self.ds = gdal.AutoCreateWarpedVRT(self.in_ds, self.in_srs_wkt, self.out_srs.ExportToWkt())
                if self.options.verbose:
                    print("Warping of the raster by AutoCreateWarpedVRT (result saved into 'tiles.vrt')")
                    self.ds.GetDriver().CreateCopy("tiles.vrt", self.ds)


        if not self.in_srs:
            self.error("Input file has unknown SRS.")
            
        if self.ds and self.options.verbose:
            print("Projected file:", "tiles.vrt", "( %sP x %sL - %s bands)" % (self.ds.RasterXSize, self.ds.RasterYSize, self.ds.RasterCount))
        else:
            self.ds = self.in_ds

        self.mercator = GlobalMercator() # from globalmaptiles.py

        #
        # Here we should have a raster (ds) in the correct Spatial Reference system
        #

        # Get alpha band (either directly or from NODATA value)
        self.alphaband = self.ds.GetRasterBand(1).GetMaskBand()
        if (self.alphaband.GetMaskFlags() & gdal.GMF_ALPHA) or self.ds.RasterCount==4 or self.ds.RasterCount==2:
            self.dataBandsCount = self.ds.RasterCount - 1
        else:
            self.dataBandsCount = self.ds.RasterCount

        # Read the georeference 
        out_gt = self.ds.GetGeoTransform()

        # Report error in case rotation/skew is in geotransform (possible only in 'raster' profile)
        if (out_gt[2], out_gt[4]) != (0,0):
            self.error("Georeference of the raster contains rotation or skew. Such raster is not supported. Please use gdalwarp first.")
            
    def get_bounds(self, tx, ty, tz):
        if self.options.meta == 1:
            # invert y from TMS to Google projection
            ty = (2**tz - 1) - ty
            return self.mercator.TileBounds(tx, ty, tz)
        
        m = self.options.meta
        ty *= m
        ty = (2 ** tz - 1) - ty
        ty /= m
        minx, miny = self.mercator.PixelsToMeters(tx * self.tilesize, ty * self.tilesize, tz)
        maxx, maxy = self.mercator.PixelsToMeters((tx + 1) * self.tilesize, (ty + 1) * self.tilesize, tz)
        return (minx, miny, maxx, maxy)

    def generate_tile(self, tz, tx, ty):
        tilebands = self.dataBandsCount + 1
        buff = 2
        # Tile bounds in EPSG:900913
        b = self.get_bounds(tx, ty, tz)
        # Tile bounds in raster coordinates for ReadRaster query
        rb, wb = self.geo_query(self.ds, b[0], b[3], b[2], b[1], buff)
        rx, ry, rxsize, rysize = rb
        wx, wy, wxsize, wysize = wb

        if self.options.verbose:
            print("ReadRaster Extent: ", rb)

        # copy source pixels
        dsquery = self.copy_window(self.ds, rx, ry, rxsize, rysize, tilebands)
        # scale & clip to tile size
        dswindow = self.scale_raster(dsquery, wxsize, wysize, tilebands)
        del dsquery
        tile = self.copy_window(dswindow, wx, wy, self.tilesize, self.tilesize, tilebands)
        del dswindow

        # save copy to temp file
        temp = "/tmp/%d_%d_%d" % (tz, tx, ty)
        self.out_drv.CreateCopy(temp, tile, 0, ['ZLEVEL=3'])
        del tile
        
        with open(temp, 'r') as f:
            output = f.read()
        os.unlink(temp)
        return output

    # -------------------------------------------------------------------------
    def copy_window(self, source, rx, ry, rxsize, rysize, tilebands):
        dsquery = self.mem_drv.Create('', rxsize, rysize, tilebands)
        ox = oy = 0
        if rx < 0:
            ox = rx * -1
            rx = 0
            rxsize = rxsize - ox
        if ry < 0:
            oy = ry * -1
            ry = 0
            rysize = rysize - oy

        data = source.ReadRaster(rx, ry, rxsize, rysize, band_list=list(range(1, tilebands + 1)))
        dsquery.WriteRaster(ox, oy, rxsize, rysize, data, band_list=list(range(1, tilebands + 1)))
        del data
        return dsquery

    # -------------------------------------------------------------------------
    def geo_query(self, ds, ulx, uly, lrx, lry, buffer=1):
        """For given dataset and query in cartographic coordinates
        returns parameters for ReadRaster() in raster coordinates and
        x/y shifts (for border tiles). If the querysize is not given, the
        extent is returned in the native resolution of dataset ds."""

        geotran = ds.GetGeoTransform()
        # real offset
        rx = (ulx - geotran[0]) / geotran[1]
        ry = (uly - geotran[3]) / geotran[5]
        # diff to buffered window offset
        drx = rx - (math.floor(rx) - buffer)
        dry = ry - (math.floor(ry) - buffer)
        # final read offset
        rx = int(rx - drx)
        ry = int(ry - dry)
                
        # real size from corrected offset
        rxsize = (lrx - ulx) / geotran[1]
        rysize = (lry - uly) / geotran[5]
        # diff to buffered window offset
        drxsize = (round(rxsize) + buffer) - rxsize
        drysize = (round(rysize) + buffer) - rysize
        # one pixel scale ratio after resize
        prx = self.tilesize / rxsize
        pry = self.tilesize / rysize
        # final read size
        rxsize = int(rxsize + drx + drxsize)
        rysize = int(rysize + dry + drysize)
        
        # write offsets
        wx = int(round(buffer * prx))
        wy = int(round(buffer * pry))
        # write sizes
        wxsize = int(round(self.tilesize + prx * buffer * 2))
        wysize = int(round(self.tilesize + pry * buffer * 2))

        # Coordinates should not go out of the raster bounds
        if rx + rxsize < 0 or rx > ds.RasterXSize or ry + rysize < 0 or ry > ds.RasterYSize:
            raise Exception('Read offset is out of bounds')

        if rx+rxsize > ds.RasterXSize:
            rxsize = ds.RasterXSize - rx
            wxsize = int(wxsize * (float(ds.RasterXSize - rx) / rxsize))

        #if ry+rysize > ds.RasterYSize:
        #    wysize = int( wysize * (float(ds.RasterYSize - ry) / rysize) )
        #    rysize = ds.RasterYSize - ry

        return (rx, ry, rxsize, rysize), (wx, wy, wxsize, wysize)

    # -------------------------------------------------------------------------
    def scale_raster(self, dsquery, wxsize, wysize, tilebands):
        """Scales down query dataset to the tile dataset"""
        dstile = self.mem_drv.Create('', wxsize, wysize, tilebands)
        querysize = dsquery.RasterXSize
        d = wxsize / float(querysize)
        if d == 1.0:
            return dsquery

        dsquery.SetGeoTransform((0.0, d, 0.0, 0.0, 0.0, d))
        dstile.SetGeoTransform((0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
        if self.options.verbose:
            print ("Resampling tile from %d to %d, by factor %f" % (querysize, wxsize, d))
        res = gdal.ReprojectImage(dsquery, dstile, None, None, self.resampling)
        if res != 0:
            self.error("ReprojectImage() failed, error %d" % (res))
        return dstile
            
    def handle(self, environ, start_response):
        request = environ['PATH_INFO']
        f = temp = None
        if request == '/favicon.ico':
            start_response('404 Not Found', []);
            return ['0']
        try:
            null, z, x, y = request.split('/', 4)
            y, ext = y.split('.')
            z = int(z)
            x = int(x)
            y = int(y)
            output = self.generate_tile(z, x, y)
            headers = [
                ('Access-Control-Allow-Origin', '*'), # allow cross origin requests
                ('Cache-Control', 'max-age=30758400, public'), # allow cache for 1 year
                ('Content-Type', 'image/png'),
                ('Content-Length', len(output))]
            start_response('200 OK', headers);
            return [output]
            response = BaseResponse(output, status=200, headers=headers)
            return response(environ, start_response)
        except Exception, e:
            print e
            start_response('204 No content', [])
            return ['']

# =============================================================================
# =============================================================================
# =============================================================================

if __name__ == '__main__':
    argv = gdal.GeneralCmdLineProcessor(sys.argv)
    app = GDAL2Tile(argv[1:])
    app.open_input()
    run_simple('0.0.0.0', 8888, app.handle, processes=app.options.processes)

