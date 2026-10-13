# Third-party licences

pyBikeRouter uses the following software and data that are not its own.

## BRouter

The routing engine behind the `brouter` provider. pyBikeRouter does not contain BRouter's
program code: it runs the official image
(`ghcr.io/abrensch/brouter`, pinned by digest in `docker/compose.yaml` and
`easy-install/docker-compose.yml`) and talks to it over HTTP.

What *is* copied into this repository, and therefore into the easy-install bundle, are files
that come from BRouter's own source tree:

| File | Origin |
| --- | --- |
| `docker/brouter/lookups.dat`, `easy-install/brouter/lookups.dat` | BRouter `misc/profiles2/lookups.dat`, unchanged |
| `docker/brouter/profiles/*.brf`, `easy-install/brouter/profiles/*.brf` | Derived from BRouter's stock profiles (`misc/profiles2`); each file's header names its source and changes |

BRouter is released under the MIT licence. Its copyright notice and licence text, as published at
<https://github.com/abrensch/brouter/blob/master/LICENSE>, follow and are also kept next to the
copied files (`docker/brouter/LICENSE`, `easy-install/brouter/LICENSE`):

```text
The MIT License (MIT)

Copyright (c) 2019 BRouter contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

The headers of the stock profiles that the `.brf` files derive from also state "license GPLv3"
(profile author: quaelnix / BRouter contributors). Those headers are kept in every derived file;
if you redistribute the profiles on their own, keep them too.

## OpenStreetMap data

BRouter's map tiles (`*.rd5`, downloaded from <https://brouter.de/brouter/segments4/>) and the
places, surfaces and road classes pyBikeRouter reads through Overpass and Nominatim are derived
from OpenStreetMap, (c) OpenStreetMap contributors, available under the Open Database Licence
(ODbL). See <https://www.openstreetmap.org/copyright>. Routes you export or publish should carry
that attribution.
