"""HydroWatch analytical service: REST API, interactive map, GeoJSON export and reports.

The service works on a prepared set of scenes/masks (allowed by the case statement):
masks produced by ``hydrowatch-predict`` are the single source of truth, so every number
returned by the API can be reproduced by pixel counting on ``predictions/masks/*_all.tif``.
"""
