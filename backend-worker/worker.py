import argparse
import json
import logging
import math
import os
import random
import shutil
import tempfile
import time
import unittest

import numpy
import numpy.testing
import pygeoprocessing
import requests
import shapely.geometry
import shapely.wkt
from osgeo import gdal
from osgeo import ogr
from osgeo import osr
from PIL import Image

from natcap.invest import carbon
from natcap.invest import urban_cooling_model
from natcap.invest import urban_nature_access
from natcap.invest import utils

import invest_args
import invest_results

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


POLLING_INTERVAL_S = 3

DEFAULT_GTIFF_CREATION_TUPLE_OPTIONS = ('GTIFF', (
    'TILED=YES', 'BIGTIFF=YES', 'COMPRESS=LZW',
    'BLOCKXSIZE=256', 'BLOCKYSIZE=256'))

_WEB_MERCATOR_SRS = osr.SpatialReference()
_WEB_MERCATOR_SRS.ImportFromEPSG(3857)
_WEB_MERCATOR_SRS.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
WEB_MERCATOR_SRS_WKT = _WEB_MERCATOR_SRS.ExportToWkt()
_ALBERS_EQUAL_AREA_SRS = osr.SpatialReference()
_ALBERS_EQUAL_AREA_SRS.ImportFromProj4(  # more terse than WKT
    '+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 '
    '+datum=WGS84 +units=m +no_defs')
_ALBERS_EQUAL_AREA_SRS.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

LULC_FILENAME = 'lulc_overlay_3857.tif'
LULC_RASTER_PATHS = {
    'vsigs': f'/vsigs/natcap-urban-online-datasets-public/{LULC_FILENAME}',  # TODO: does not work
    'docker': f'/opt/appdata/{LULC_FILENAME}',
    'local': os.path.join(os.path.dirname(__file__), '..', 'appdata',
                          LULC_FILENAME)
}
_LULC_RASTER_INFO = None
for LULC_RASTER_PATH in LULC_RASTER_PATHS.values():
    try:
        _LULC_RASTER_INFO = pygeoprocessing.get_raster_info(LULC_RASTER_PATH)
    except ValueError:
        LOGGER.info(f"Could not open raster path {LULC_RASTER_PATH}")
if _LULC_RASTER_INFO is None:
    raise AssertionError(
        f"Could not open {LULC_FILENAME} at any known locations")
LOGGER.info(f"Using LULC at {LULC_RASTER_PATH}")

LULC_SRS_WKT = _LULC_RASTER_INFO['projection_wkt']
_LULC_SRS = osr.SpatialReference()
_LULC_SRS.ImportFromWkt(LULC_SRS_WKT)
assert _LULC_SRS.IsSame(_WEB_MERCATOR_SRS), (
    "LULC must have been reprojected to web mercator")

LULC_NODATA = _LULC_RASTER_INFO['nodata'][0]
LULC_DTYPE = _LULC_RASTER_INFO['datatype']
WEB_MERCATOR_TO_ALBERS_EQ_AREA = osr.CreateCoordinateTransformation(
    _WEB_MERCATOR_SRS, _ALBERS_EQUAL_AREA_SRS)
ALBERS_EQ_AREA_TO_WEB_MERCATOR = osr.CreateCoordinateTransformation(
    _ALBERS_EQUAL_AREA_SRS, _WEB_MERCATOR_SRS)
OGR_GEOMETRY_TYPES = {
    'Polygon': ogr.wkbPolygon,
    'MultiPolygon': ogr.wkbMultiPolygon,
}

# LULC raster attributes copied in by hand from gdalinfo
LULC_ORIGIN_X, _, _, LULC_ORIGIN_Y, _, _ = _LULC_RASTER_INFO['geotransform']
PIXELSIZE_X, PIXELSIZE_Y = _LULC_RASTER_INFO['pixel_size']

CARBON = 'carbon'
URBAN_COOLING = 'urban_cooling_model'
URBAN_NATURE_ACCESS = 'urban_nature_access'
INVEST_MODELS = {
    URBAN_COOLING: {
        "api": urban_cooling_model,
        "build_args": invest_args.urban_cooling,
        "derive_results": invest_results.urban_cooling,
    },
    CARBON: {
        "api": carbon,
        "build_args": invest_args.carbon,
        "derive_results": invest_results.carbon,
    },
    URBAN_NATURE_ACCESS: {
        "api": urban_nature_access,
        "build_args": invest_args.urban_nature_access,
        "derive_results": invest_results.urban_nature_access
    }
}

# The largest extent LULC needed by invest models is
# 2x the 800m search radius used by UNA.
LARGEST_SERVICESHED = 1600

# Quiet logging
logging.getLogger(f'pygeoprocessing').setLevel(logging.WARNING)
logging.getLogger(f'taskgraph').setLevel(logging.WARNING)


STATUS_SUCCESS = 'success'
STATUS_FAILED = 'failed'
JOBTYPE_FILL = 'lulc_fill'
JOBTYPE_WALLPAPER = 'wallpaper'
JOBTYPE_CROP = 'lulc_crop'
JOBTYPE_PARCEL_STATS = 'stats_under_parcel'
JOBTYPE_PATTERN_THUMBNAIL = 'pattern_thumbnail'
JOBTYPE_INVEST = 'invest'
ENDPOINTS = {
    JOBTYPE_FILL: 'scenario',
    JOBTYPE_WALLPAPER: 'scenario',
    JOBTYPE_CROP: 'scenario',
    JOBTYPE_PARCEL_STATS: 'parcel_stats',
    JOBTYPE_PATTERN_THUMBNAIL: 'pattern',
    JOBTYPE_INVEST: 'invest',
}


class Tests(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace_dir)

    def test_pixelcounts_under_parcel(self):
        # University of Texas: San Antonio, selected by hand in QGIS
        # Coordinates are in EPSG:3857 "Web Mercator"
        point_over_san_antonio = shapely.geometry.Point(
            -10965275.57, 3429693.30)
        parcel = point_over_san_antonio.buffer(100)

        pygeoprocessing.geoprocessing.shapely_geometry_to_vector(
            [point_over_san_antonio, parcel],
            os.path.join(self.workspace_dir, 'parcel.fgb'),
            _WEB_MERCATOR_SRS.ExportToWkt(), 'FlatGeoBuf')

        pixelcounts = pixelcounts_under_parcel(
            parcel.wkt, LULC_RASTER_PATH)

        expected_values = {
            262: 40,
            321: 1,
        }
        self.assertEqual(pixelcounts, expected_values)

    def test_new_lulc(self):
        gtiff_path = os.path.join(self.workspace_dir, 'raster.tif')

        # University of Texas: San Antonio, selected by hand in QGIS
        # Coordinates are in EPSG:3857 "Web Mercator"
        point_over_san_antonio = shapely.geometry.Point(
            -10965275.57, 3429693.30)

        # Raster units are in meters (mercator)
        parcel = point_over_san_antonio.buffer(100)
        pygeoprocessing.geoprocessing.shapely_geometry_to_vector(
            [point_over_san_antonio, parcel],
            os.path.join(self.workspace_dir, 'parcel.shp'),
            _WEB_MERCATOR_SRS.ExportToWkt(), 'ESRI Shapefile')

        _create_new_lulc(parcel.wkt, gtiff_path, include_pixel_values=True)

        raster_info = pygeoprocessing.get_raster_info(gtiff_path)

        raster_bbox = shapely.geometry.box(*raster_info['bounding_box'])
        epsg3857_raster_bbox = ogr.CreateGeometryFromWkt(raster_bbox.wkt)
        epsg3857_raster_bbox.Transform(ALBERS_EQ_AREA_TO_WEB_MERCATOR)
        epsg3857_raster_bbox_shapely = shapely.wkt.loads(
            epsg3857_raster_bbox.ExportToWkt())

        self.assertTrue(epsg3857_raster_bbox_shapely.contains(parcel))

    def test_fill(self):
        # University of Texas: San Antonio, selected by hand in QGIS
        # Coordinates are in EPSG:3857 "Web Mercator"
        point_over_san_antonio = shapely.geometry.Point(
            -10965275.57, 3429693.30)
        parcel = point_over_san_antonio.buffer(100)

        target_raster_path = os.path.join(self.workspace_dir, 'raster.tif')
        fill_parcel(parcel.wkt, 15, target_raster_path)

        result_array = pygeoprocessing.raster_to_numpy_array(
                target_raster_path)
        self.assertEqual(
            numpy.sum(result_array[result_array != LULC_NODATA]), 600)
        self.assertEqual(numpy.sum(result_array == 15), 40)

    def test_wallpaper(self):
        # University of Texas: San Antonio, selected by hand in QGIS
        # Coordinates are in EPSG:3857 "Web Mercator"
        point_over_san_antonio = shapely.geometry.Point(
            -10965275.57, 3429693.30)
        parcel = point_over_san_antonio.buffer(100)

        # Apache Creek, urban residential area, San Antonio, TX.
        # Selected by hand in QGIS.  Coordinates are in EPSG:3857 "Web
        # Mercator"
        pattern = shapely.geometry.box(
            *shapely.geometry.Point(
                -10968418.16, 3429347.98).buffer(100).bounds)

        target_raster_path = os.path.join(
            self.workspace_dir, 'wallpapered_raster.tif')

        wallpaper_parcel(parcel.wkt, pattern.wkt, LULC_RASTER_PATH,
                         target_raster_path, self.workspace_dir)

    def test_get_bioregion(self):
        # University of Texas: San Antonio, selected by hand in QGIS
        # Coordinates are in EPSG:3857 "Web Mercator"
        point_over_san_antonio = shapely.geometry.Point(
            -10965275.57, 3429693.30)
        region = invest_args.get_bioregion(point_over_san_antonio)
        self.assertEqual(region, 'NA28')

    def test_get_bioregion_out_of_bounds(self):
        # Outside North America; flip of San Antonio coords
        point = shapely.geometry.Point(
            10965275.57, -3429693.30)
        with self.assertRaises(ValueError):
            region = invest_args.get_bioregion(point)

    def test_extract_from_census(self):
        # University of Texas: San Antonio, selected by hand in QGIS
        # Coordinates are in EPSG:3857 "Web Mercator"
        point_over_san_antonio = shapely.geometry.Point(
            -10965275.57, 3429693.30)
        parcel = point_over_san_antonio.buffer(100)
        aoi_vector_path = os.path.join(
            self.workspace_dir, 'parcel_webmercator.geojson')
        pygeoprocessing.geoprocessing.shapely_geometry_to_vector(
            [parcel], aoi_vector_path, _WEB_MERCATOR_SRS.ExportToWkt(),
            'GeoJSON')
        census_dict = invest_results._extract_census_from_aoi(aoi_vector_path)
        expected_dict = {
            'race': {
                'White (Not Hispanic or Latino)': 727.0,
                'Black': 710.0,
                'American Indian': 14.0,
                'Asian': 5.0,
                'Hawaiian': 0.0,
                'Other': 0.0,
                'Two or more races': 59.0,
                'Hispanic or Latino': 4130.0
            },
            'poverty': {           
                'Household received Food Stamps or SNAP in the past 12 months': 696.0,
                'Household received Food Stamps or SNAP in the past 12 months | Income in the past 12 months below poverty level': 424.0,
                'Household received Food Stamps or SNAP in the past 12 months | Income in the past 12 months at or above poverty level': 272.0,
                'Household did not receive Food Stamps or SNAP in the past 12 months': 745.0,
                'Household did not receive Food Stamps or SNAP in the past 12 months | Income in the past 12 months below poverty level':199.0,
                'Household did not receive Food Stamps or SNAP in the past 12 months | Income in the past 12 months at or above poverty level': 546.0
            }
        }
        self.assertEqual(census_dict, expected_dict)


def _warp_raster_to_web_mercator(source_albers_raster_path,
                                 target_web_mercator_raster_path):
    """Warp an Albers Equal Area-projected raster to Web Mercator.

    Args:
        source_albers_raster_path (str): The source raster, assumed to be in
            Albers Equal Area.
        target_web_mercator_raster_path (str): The target raster path, which
            will be projected in Web Mercator.

    Returns:
        ``None``
    """
    pygeoprocessing.geoprocessing.warp_raster(
        source_albers_raster_path, (PIXELSIZE_X, PIXELSIZE_Y),
        target_web_mercator_raster_path, 'near',
        target_projection_wkt=WEB_MERCATOR_SRS_WKT)


def _reproject_to_nlud(parcel_wkt_epsg3857):
    """Reproject a WKT polygon to the LULC projection.

    Args:
        parcel_wkt_epsg3857 (string): A WKT polygon projected in epsg 3857 "Web
            Mercator".

    Returns:
        parcel (shapely.geometry): A Shapely geometry of the input parcel where
            the geometry has been transformed to the LULC's projection.
    """
    ogr_geom = ogr.CreateGeometryFromWkt(parcel_wkt_epsg3857)
    err_code = ogr_geom.Transform(WEB_MERCATOR_TO_ALBERS_EQ_AREA)
    if err_code:
        LOGGER.warning(
            "Transformation failed on parcel; continuing with "
            f"{parcel_wkt_epsg3857}")
    assert ogr_geom.ExportToWkt() != parcel_wkt_epsg3857
    parcel_geom = shapely.wkt.loads(ogr_geom.ExportToWkt())
    return parcel_geom


def _create_new_lulc(parcel_wkt_epsg3857, target_local_gtiff_path,
                     include_pixel_values=False):
    """Create an LULC raster in the LULC projection covering the parcel.

    Args:
        parcel_wkt_epsg3857 (str): The parcel WKT in EPSG:3857 (Web Mercator)
        target_local_gtiff_path (str): Where the target raster should be saved
        include_pixel_values=False (bool): Whether to include the underlying
            raster's pixel values in the new, cropped LULC.

    Returns:
        ``None``
    """
    parcel_geom = shapely.wkt.loads(parcel_wkt_epsg3857)
    parcel_min_x, parcel_min_y, parcel_max_x, parcel_max_y = parcel_geom.bounds
    buffered_parcel_geom = parcel_geom.buffer(LARGEST_SERVICESHED)
    buf_minx, buf_miny, buf_maxx, buf_maxy = buffered_parcel_geom.bounds

    # Round "up" to the nearest pixel, sort of the pixel-math version of
    # rasterizing the bounding box with "ALL_TOUCHED=TRUE".
    buf_minx -= abs((buf_minx - LULC_ORIGIN_X) % PIXELSIZE_X)
    buf_miny -= abs((buf_miny - LULC_ORIGIN_Y) % PIXELSIZE_Y)
    buf_maxx += PIXELSIZE_X - abs((buf_maxx - LULC_ORIGIN_X) % PIXELSIZE_X)
    buf_maxy += PIXELSIZE_Y - abs((buf_maxy - LULC_ORIGIN_Y) % PIXELSIZE_Y)

    pygeoprocessing.geoprocessing.warp_raster(
        LULC_RASTER_PATH, (PIXELSIZE_X, PIXELSIZE_Y),
        target_local_gtiff_path,
        resample_method='near',
        target_bb=[buf_minx, buf_miny, buf_maxx, buf_maxy])

    if not include_pixel_values:
        raster = gdal.OpenEx(target_local_gtiff_path, gdal.GA_Update)
        band = raster.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            band.Fill(nodata)
        else:
            LOGGER.warning("LULC does not have a defined nodata value; "
                           "cannot fill with None.")
        band = None
        raster = None


def fill_parcel(parcel_wkt_epsg3857, fill_lulc_class,
                target_lulc_path, working_dir=None):
    """Fill (rasterize) a parcel with a landcover code.

    This function writes a new raster that:

        * Is aligned to the grid of the source lulc
        * Is filled with nodata except for the parcel
        * Is filled with ``fill_lulc_class`` where the parcel is present

    Args:
        parcel_wkt_epsg3857 (str): The WKT of the parcel to fill,
            projected in EPSG:3857 (Web Mercator)
        fill_lulc_class (int): The lulc class to fill the parcel with.
        target_lulc_path (str): Where the target lulc raster should be saved.
        working_dir (str): The path to where working files should be stored.
            If ``None``, then the default temp dir will be used.

    Returns:
        ``None``
    """
    parcel_geom = shapely.wkt.loads(parcel_wkt_epsg3857)
    working_dir = tempfile.mkdtemp(prefix='fill-parcel-', dir=working_dir)

    parcel_vector_path = os.path.join(working_dir, 'parcel.fgb')
    pygeoprocessing.geoprocessing.shapely_geometry_to_vector(
        [parcel_geom], parcel_vector_path, LULC_SRS_WKT, 'FlatGeoBuf',
        ogr_geom_type=OGR_GEOMETRY_TYPES[parcel_geom.type]
    )

    _create_new_lulc(
        parcel_wkt_epsg3857, target_lulc_path, include_pixel_values=True)
    pygeoprocessing.geoprocessing.rasterize(
        parcel_vector_path, target_lulc_path, [fill_lulc_class],
        option_list=['ALL_TOUCHED=TRUE'])

    shutil.rmtree(working_dir)


def wallpaper_parcel(parcel_wkt_epsg3857, pattern_wkt_epsg3857,
                     source_nlud_raster_path, target_raster_path,
                     working_dir=None):
    """Wallpaper a region.

    This function is adapted from
    https://github.com/natcap/wallpaper-scenarios/blob/main/wallpaper_raster.py#L100

    Args:
        parcel_wkt_epsg3857 (str): The WKT of the parcel to wallpaper over,
            projected in EPSG:3857 (Web Mercator)
        pattern_wkt_epsg3857 (str): The WKT of the pattern geometry, projected
            in EPSG:3857 (Web Mercator)
        source_nlud_raster_path (str): The GDAL-compatible URI to the source
            LULC raster, projected in Web Mercator.
        target_raster_path (str): Where the output raster should be written on
            disk.
        working_dir (str): Where temporary files should be stored.  If
            ``None``, then the default temp dir will be used.

    Returns:
        ``None``
    """
    nlud_raster_info = pygeoprocessing.geoprocessing.get_raster_info(
        source_nlud_raster_path)

    working_dir = tempfile.mkdtemp(prefix='wallpaper-parcel-', dir=working_dir)
    parcel_mask_raster_path = os.path.join(working_dir, 'mask.tif')
    fill_parcel(parcel_wkt_epsg3857, 1, parcel_mask_raster_path)
    parcel_raster_info = pygeoprocessing.get_raster_info(
        parcel_mask_raster_path)

    nlud_under_parcel_path = os.path.join(working_dir, 'nlud_under_parcel.tif')
    pygeoprocessing.geoprocessing.warp_raster(
        source_nlud_raster_path, nlud_raster_info['pixel_size'],
        nlud_under_parcel_path, 'near',
        target_bb=parcel_raster_info['bounding_box'])
    nlud_under_parcel_raster_info = pygeoprocessing.get_raster_info(
        nlud_under_parcel_path)

    nlud_under_pattern_path = os.path.join(
        working_dir, 'nlud_under_pattern.tif')
    pattern_bbox = shapely.wkt.loads(pattern_wkt_epsg3857).bounds
    pygeoprocessing.geoprocessing.warp_raster(
        source_nlud_raster_path, nlud_raster_info['pixel_size'],
        nlud_under_pattern_path, 'near',
        target_bb=pattern_bbox)
    wallpaper_array = pygeoprocessing.raster_to_numpy_array(
        nlud_under_pattern_path)

    # Sanity check to catch programmer error early
    for attr in ('raster_size', 'pixel_size', 'bounding_box'):
        assert nlud_under_parcel_raster_info[attr] == parcel_raster_info[attr]

    pygeoprocessing.new_raster_from_base(
        parcel_mask_raster_path, target_raster_path,
        LULC_DTYPE, [LULC_NODATA])
    target_raster = gdal.OpenEx(
        target_raster_path, gdal.OF_RASTER | gdal.GA_Update)
    target_band = target_raster.GetRasterBand(1)
    parcel_mask_raster = gdal.OpenEx(parcel_mask_raster_path, gdal.OF_RASTER)
    parcel_mask_band = parcel_mask_raster.GetRasterBand(1)

    for offset_dict, base_array in pygeoprocessing.iterblocks(
            (nlud_under_parcel_path, 1)):
        parcel_mask_array = parcel_mask_band.ReadAsArray(**offset_dict)
        assert parcel_mask_array is not None

        xoff = offset_dict['xoff']
        yoff = offset_dict['yoff']

        wallpaper_x = xoff % wallpaper_array.shape[1]
        wallpaper_y = yoff % wallpaper_array.shape[0]

        win_ysize = offset_dict['win_ysize']
        win_xsize = offset_dict['win_xsize']
        wallpaper_x_repeats = (
            1 + ((wallpaper_x+win_xsize) // wallpaper_array.shape[1]))
        wallpaper_y_repeats = (
            1 + ((wallpaper_y+win_ysize) // wallpaper_array.shape[0]))
        wallpaper_tiled = numpy.tile(
            wallpaper_array,
            (wallpaper_y_repeats, wallpaper_x_repeats))[
            wallpaper_y:wallpaper_y+win_ysize,
            wallpaper_x:wallpaper_x+win_xsize]

        target_array = numpy.where(
            parcel_mask_array == 1,
            wallpaper_tiled,
            base_array)

        target_band.WriteArray(target_array, xoff=xoff, yoff=yoff)

    target_raster.BuildOverviews()  # default settings for overviews

    # clean up mask raster before rmtree
    parcel_mask_band = None
    parcel_mask_raster = None

    shutil.rmtree(working_dir)


def pixelcounts_under_parcel(parcel_wkt_epsg3857, source_raster_path):
    """Get a breakdown of pixel counts under a parcel per lulc code.

    Args:
        parcel_wkt_epsg3857 (str): The parcel WKT in web mercator.
        source_raster_path (str): The LULC to get pixel counts from.

    Returns:
        counts (dict): A dict mapping int lulc codes to float (0-1) percent of
            pixels rounded to 4 decimal places.  This percentage reflects the
            percentage of pixels under the parcel, not the lulc, so if a parcel
            covers 4 pixels, 1 of lulc code 5 and 3 of lulc code 6, ``counts``
            would be ``{5: 0.25, 6: 0.75}``.
    """
    if source_raster_path.startswith(('https', 'http')):
        source_raster_path = f'/vsicurl/{source_raster_path}'
    source_raster = gdal.OpenEx(source_raster_path,
                                gdal.GA_ReadOnly | gdal.OF_RASTER)
    source_band = source_raster.GetRasterBand(1)
    geotransform = source_raster.GetGeoTransform()
    inv_geotransform = gdal.InvGeoTransform(geotransform)

    parcel = shapely.wkt.loads(parcel_wkt_epsg3857)
    # Convert lon/lat degrees to x/y pixel for the dataset
    minx, miny, maxx, maxy = parcel.bounds
    _x0, _y0 = gdal.ApplyGeoTransform(inv_geotransform, minx, miny)
    _x1, _y1 = gdal.ApplyGeoTransform(inv_geotransform, maxx, maxy)
    x0, y0 = min(_x0, _x1), min(_y0, _y1)
    x1, y1 = max(_x0, _x1), max(_y0, _y1)

    pygeoprocessing.geoprocessing.shapely_geometry_to_vector(
        [parcel],
        os.path.join('parcel_loaded.fgb'),
        WEB_MERCATOR_SRS_WKT, 'FlatGeoBuf',
        ogr_geom_type=OGR_GEOMETRY_TYPES[parcel.type],
    )

    # "Round up" to the next pixel
    x0 = math.floor(x0)
    y0 = math.floor(y0)
    x1 = math.ceil(x1)
    y1 = math.ceil(y1)
    array = source_band.ReadAsArray(
        int(x0), int(y0), int(x1-x0), int(y1-y0))

    # create a new in-memory dataset filled with 0
    gdal_driver = gdal.GetDriverByName('MEM')
    target_raster = gdal_driver.Create(
        '', array.shape[1], array.shape[0], 1, gdal.GDT_Byte)
    target_raster.SetProjection(LULC_SRS_WKT)
    target_origin_x, target_origin_y = gdal.ApplyGeoTransform(
        geotransform, x0, y0)
    target_raster.SetGeoTransform(
        [target_origin_x, PIXELSIZE_X, 0.0, target_origin_y, 0.0, PIXELSIZE_Y])
    target_band = target_raster.GetRasterBand(1)
    target_band.Fill(0)

    vector_driver = ogr.GetDriverByName('MEMORY')
    vector = vector_driver.CreateDataSource('parcel')
    parcel_layer = vector.CreateLayer(
        'parcel_layer', _WEB_MERCATOR_SRS, ogr.wkbPolygon)
    parcel_layer.StartTransaction()
    feature = ogr.Feature(parcel_layer.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkt(parcel.wkt))
    parcel_layer.CreateFeature(feature)
    parcel_layer.CommitTransaction()

    gdal.RasterizeLayer(
        target_raster, [1], parcel_layer,
        options=['ALL_TOUCHED=TRUE'], burn_values=[1])

    parcel_mask = target_band.ReadAsArray()
    assert parcel_mask.shape == array.shape
    values_under_parcel, counts = numpy.unique(
        array[parcel_mask == 1], return_counts=True)

    return_values = {}
    # cast lulc_codes and counts to list for future json dump call
    # which does not allow numpy types for keys
    for lulc_code, pixel_count in zip(
            values_under_parcel.tolist(), counts.tolist()):
        return_values[lulc_code] = pixel_count

    return return_values


def make_thumbnail(pattern_wkt_epsg3857, colors_dict, target_thumbnail_path,
                   working_dir=None):
    working_dir = tempfile.mkdtemp(dir=working_dir, prefix='thumbnail-')
    thumbnail_gtiff_path = os.path.join(working_dir, 'pattern.tif')

    # Buffer the bbox by a half-pixel to make sure we get the whole pattern and
    # just a small bit of the surrounding context.
    pygeoprocessing.geoprocessing.warp_raster(
        LULC_RASTER_PATH, _LULC_RASTER_INFO['pixel_size'],
        thumbnail_gtiff_path, 'near',
        target_bb=shapely.wkt.loads(pattern_wkt_epsg3857).buffer(
            PIXELSIZE_X/2).bounds
    )

    raw_image = Image.open(thumbnail_gtiff_path)
    # 'P' mode indicates palletted color
    image = raw_image.convert('P')

    rgb_colors = {}
    for lucode, hex_color in colors_dict.items():
        rgb_colors[lucode] = [
            int(f'0x{"".join(hex_color[1:3])}', 16),
            int(f'0x{"".join(hex_color[3:5])}', 16),
            int(f'0x{"".join(hex_color[5:7])}', 16)
        ]

    rgb_colors_list = []
    for i in range(0, 256):
        try:
            rgb_colors_list.extend(rgb_colors[i])
        except KeyError:
            rgb_colors_list.extend([0, 0, 0])

    image.putpalette(rgb_colors_list)
    factor = 30  # taken from the pixelsize so we can just deal in native units
    image = image.resize((image.width * factor,
                          image.height * factor))
    image.save(target_thumbnail_path)
    shutil.rmtree(working_dir, ignore_errors=True)


def do_work(host, port, outputs_location):
    job_queue_url = f'http://{host}:{port}/jobsqueue/'
    LOGGER.info(f'Starting worker, queueing {job_queue_url}')
    LOGGER.info(f'Polling the queue every {POLLING_INTERVAL_S}s if no work')

    while True:
        response = requests.get(job_queue_url)
        # if there is no work on the queue, expecting response.json()==None
        if not response.json():
            time.sleep(POLLING_INTERVAL_S)
            continue
        LOGGER.info("Response received; loading job details")

        # response.json() returns a stringified json object, so need to load
        # it into a python dict
        response_json = json.loads(response.json())
        server_args = response_json['server_attrs']
        job_id = server_args['job_id']
        job_type = response_json['job_type']
        job_args = response_json['job_args']

        #TODO: could this be moved outside of while loop?
        # Make sure the appropriate directory is created
        scenarios_dir = os.path.join(outputs_location, 'scenarios')
        model_outputs_dir = os.path.join(outputs_location, 'model_outputs')
        for path in (scenarios_dir, model_outputs_dir):
            if not os.path.exists(path):
                os.makedirs(path)

        LOGGER.info(f"Starting job {job_id}:{job_type}")
        try:
            if job_type in {JOBTYPE_FILL, JOBTYPE_WALLPAPER, JOBTYPE_CROP}:
                scenario_id = server_args['scenario_id']
                workspace = os.path.join(scenarios_dir, str(scenario_id))
                result_path = os.path.join(
                    workspace, f'{scenario_id}_{job_type}.tif')
                os.makedirs(workspace, exist_ok=True)

                if job_type == JOBTYPE_CROP:
                    _create_new_lulc(
                        parcel_wkt_epsg3857=job_args['target_parcel_wkt'],
                        target_local_gtiff_path=result_path,
                        include_pixel_values=True
                    )
                    LOGGER.info(f"Baseline study area written to {result_path}")
                if job_type == JOBTYPE_FILL:
                    fill_parcel(
                        parcel_wkt_epsg3857=job_args['target_parcel_wkt'],
                        fill_lulc_class=job_args['lulc_class'],
                        target_lulc_path=result_path
                    )
                    LOGGER.info(f"Filled study area written to {result_path}")
                elif job_type == JOBTYPE_WALLPAPER:
                    wallpaper_temp_dir = tempfile.mkdtemp(
                        dir=workspace, prefix='wallpaper-')
                    wallpaper_parcel(
                        parcel_wkt_epsg3857=job_args['target_parcel_wkt'],
                        pattern_wkt_epsg3857=job_args['pattern_bbox_wkt'],
                        source_nlud_raster_path=job_args['lulc_source_url'],
                        target_raster_path=result_path,
                        working_dir=wallpaper_temp_dir
                    )
                    LOGGER.info(f"Wallpapered study area written to {result_path}")
                    try:
                        shutil.rmtree(wallpaper_temp_dir)
                    except OSError as e:
                        LOGGER.exception(
                            "Something went wrong removing "
                            f"{wallpaper_temp_dir}: {e}")
                data = {
                    'result': {
                        'lulc_path': result_path,
                        'lulc_stats': pixelcounts_under_parcel(
                            job_args['target_parcel_wkt'],
                            result_path
                        ),
                    },
                }
            elif job_type == JOBTYPE_PARCEL_STATS:
                data = {
                    'result': {
                        'lulc_stats': {
                            'base': pixelcounts_under_parcel(
                                job_args['target_parcel_wkt'],
                                job_args['lulc_source_url']
                            ),
                        }
                    }
                }
            elif job_type == JOBTYPE_INVEST:
                invest_model = job_args['invest_model']
                scenario_id = job_args['scenario_id']
                LOGGER.info(f"Run InVEST model: {job_args['invest_model']}")

                model_meta = INVEST_MODELS[invest_model]
                lulc_path = job_args['lulc_source_url']

                workspace_dir = os.path.join(
                    model_outputs_dir, f'{invest_model}-{scenario_id}')

                # Ultimately we may not need prepare_workspace, but it is
                # convenient for 1) creating the workspace as a location to
                # write dynamically-created input files like an AOI vector, and
                # 2) having invest log to a file.
                with utils.prepare_workspace(workspace_dir,
                                             name=invest_model,
                                             logging_level=logging.INFO):
                    args_dict = model_meta['build_args'](
                        lulc_path, workspace_dir, job_args['study_area_wkt'])
                    LOGGER.info(f'{invest_model} model arguments: {args_dict}')
                    model_meta['api'].execute(args_dict)
                    LOGGER.info(f'Post processing {invest_model} model')
                    model_result_path = model_meta['derive_results'](workspace_dir)

                if invest_model == URBAN_COOLING:
                    serviceshed = args_dict['aoi_vector_path']
                elif invest_model == URBAN_NATURE_ACCESS:
                    serviceshed = args_dict['admin_boundaries_vector_path']
                else:
                    serviceshed = ''
                data = {
                    'result': {
                        'invest-result': model_result_path,
                        'model': job_args['invest_model'],
                        'serviceshed': serviceshed
                    }
                }
            else:
                raise ValueError(f"Invalid job type: {job_type}")
            status = STATUS_SUCCESS
        except Exception as error:
            LOGGER.exception(f'{job_type} failed: {error}')
            status = STATUS_FAILED
            result_path = None
            data = {
                'result': STATUS_FAILED
            }  # data must validate against schema even in fail
        finally:
            LOGGER.info(f"Job {job_id}: {job_type} finished with {status}")
            data['server_attrs'] = server_args
            data['status'] = status
            requests.post(
                f'{job_queue_url}{ENDPOINTS[job_type]}',
                data=json.dumps(data)
            )


def main():
    parser = argparse.ArgumentParser(
        __name__, description=('Worker for Urban Online Workflow'))
    parser.add_argument('queue_host')
    parser.add_argument('queue_port')
    parser.add_argument('output_dir')

    args = parser.parse_args()
    LOGGER.info(f'parser args: {args}')
    do_work(
        host=args.queue_host,
        port=args.queue_port,
        outputs_location=args.output_dir
    )


if __name__ == '__main__':
    main()
