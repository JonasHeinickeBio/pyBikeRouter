#!/bin/sh
# Runs inside the Overpass container before it starts (the image's
# /docker-entrypoint-initdb.d hook).
#
# The image's /db is the database owner's home directory and is created mode 700.
# The web server (fcgiwrap, another user) must pass through it to reach the
# database socket in /db/db, so with a Docker named volume -- which copies that
# mode -- every query fails with "open64: 13 Permission denied ...
# /db/db//osm3s_osm_base" and /api/status crashes. A bind-mounted host directory
# (the image's own example) does not have the problem; this keeps the named volume.
chmod 755 /db
