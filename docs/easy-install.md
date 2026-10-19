# Easy install -- for people who do not program

pyBikeRouter runs on your own computer and opens as a web page: you type a start and a
destination and get a bike route -- with alternatives, weather, sights, traffic and surface
along the way, and a GPX file for your phone or bike computer.

Everything runs inside **Docker**, so there is nothing else to install: no Python, no Java,
no commands to learn.

## What you need

- **Docker Desktop** (free): <https://www.docker.com/products/docker-desktop/> for Windows and
  Mac, <https://docs.docker.com/engine/install/> for Linux. Install it, start it, and wait until
  it says it is running.
- 4 GB of memory for Docker (the default is usually fine) and 1-3 GB of disk space.
- An internet connection for the first start (the program and the map data are downloaded).

## Get it

Download **`pybikerouter-easy-<version>.zip`** from the project's *Releases* page on GitHub
(<https://github.com/JonasHeinickeBio/pyBikeRouter/releases>) and unzip it somewhere you will
find it again, for example your home folder. You get a folder called `pyBikeRouter` with
these files:

| File | What it does |
| --- | --- |
| `START-HERE.txt` | the short version of this page |
| `Start-Windows.bat`, `Start-Mac.command`, `start-linux.sh` | start the program (and, the first time, ask where you ride) |
| `Stop-...` | stop it |
| `Update-...` | get the newest version |
| `Choose-area-...` | download the map data of another area |
| `Choose-language-model-...` | connect, change or switch off the language model for free sentences |
| `settings.env` | the few settings you may want to change (open it with a text editor) |
| `data/` | your map data and your routes; created on the first start |

## Start it

1. Double-click the **Start** file for your computer.
   - **Windows:** `Start-Windows.bat`. If Windows warns that it protects your PC, choose *More info*,
     then *Run anyway*.
   - **Mac:** `Start-Mac.command`. The first time, right-click it and choose *Open*, then *Open*.
   - **Linux:** `sh start-linux.sh` in a terminal (or double-click it in your file manager).
2. The first start takes a few minutes. It downloads the program and then asks **one question**:
   where you ride.

   ```
   Where do you ride?

     1) Germany (4 map tiles) -- all of it except the island of Sylt
     2) Austria and Switzerland (3 map tiles)
     ...
     9) Somewhere else: the area around a town I name
    10) I know the tile names
    11) Skip -- I will choose in the web page later
   ```

   Type a number and press Enter. For *the area around a town*, type the town; the program finds
   it and works out which map tiles cover about 80 km around it. It shows the download size
   (100-250 MB per tile) and asks before it downloads.
3. When it is ready your web browser opens at **<http://localhost:8000>**.

Next time the same Start file starts it in a few seconds.

## Free sentences: a language model (optional)

The planner always understands one-line requests (`from Goslar to Hannover by gravel bike`), the
chat's questions and the forms. With a **language model** connected it also reads free sentences
such as `a relaxed 50 km loop around Goslar tomorrow morning`, in the chat and in a "Describe your
ride" box. It only picks out what you wrote; places are still looked up on the map, never guessed.

After the map question, the first start asks whether you want one. **Press Enter to leave it
off** -- nothing else depends on it, and you can add it later with `Choose-language-model-...`.

| Choice | What it is | What you need |
| --- | --- | --- |
| 1. Leave it off | free sentences are not read; everything else works | nothing |
| 2. OpenRouter | an online service that runs the model for you; several models are free | a free account at [openrouter.ai](https://openrouter.ai) and a key (*Keys, Create key*); the program asks you to paste it (it is not shown) and checks it |
| 3. Ollama | the model runs on your own computer; no account, nothing leaves your machine | 4-8 GB of free memory, about 4 GB download for Ollama plus 2-5 GB for the model (once); slow without a graphics card -- an answer can take a minute |

Good to know:

- The key you paste is saved in `settings.env` in your folder (only you can read it) and used for
  requests to OpenRouter -- the text you write in the ride box goes to the model you chose, and to
  nobody else. Do not share the folder with `settings.env` in it.
- OpenRouter's free models change; the program lists the free ones available **now** and you
  pick, or type another model id from <https://openrouter.ai/models>.
- With Ollama, the Start script downloads the model on the first start and starts Ollama next to the
  program; Stop stops it again. Small models misread more often: the planner always shows what it
  understood so you can correct it.
- To switch it off again: `Choose-language-model-...` and press Enter (the key is removed from
  `settings.env`).

## Use it

- **Plan route**: type a start and a destination and press the button. Several routes appear on
  the map with their length, time, climb, surface and traffic.
- **Chat**: write what you want, for example `from Braunschweig to Goslar by gravel bike
  tomorrow at 8`, `a 40 km loop from Goslar`, or `with less traffic but as much asphalt as
  possible`.
- **Optimize** (the second tab): tick what you care about -- less traffic, more asphalt, more
  sights along the track, less climbing ... -- and the program fetches several routes and picks the
  best fit, showing the numbers behind the choice.
- **Your files**: GPX and GeoJSON files of the routes are in `data/exports`. Your chat
  conversations are in `data/chat`, so they are still there after an update.

## Change things

| I want to ... | Do this |
| --- | --- |
| ride in another area | double-click `Choose-area-...`, then Stop and Start |
| add, change or remove the language model | double-click `Choose-language-model-...`, then Stop and Start |
| use another port (8000 is taken) | open `settings.env`, change `PORT=8000`, Stop, Start; open `http://localhost:<new port>` |
| open it from my phone (same Wi-Fi) | in `settings.env` set `APP_BIND=0.0.0.0`, Stop, Start; on the phone open `http://<this computer's address>:8000` |
| get the newest version | double-click `Update-...` |
| stop it | double-click `Stop-...` (your data stays) |
| remove it completely | Stop it, then delete the folder; in Docker Desktop delete the `pybikerouter` images |

## When something goes wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| "Docker is not installed / not running" | the program needs Docker Desktop | install it, start it, wait for "running" |
| "port is already allocated" | another program uses port 8000 | change `PORT` in `settings.env` |
| "Could not download the program" | no internet, or the image is not published yet | check the connection; try again |
| "No map data" / "no route" | the area you ask about has no map data yet | `Choose-area-...` and pick it |
| the first route is slow | long or hilly trips take BRouter up to a minute or two | wait; it is faster the second time |
| "A service did not answer in time" | a free online service (places, sights, weather) was busy | press *Try again* in the chat, or plan again |

## What it contacts

The program runs on your computer. To work it uses these free services: OpenStreetMap's
Nominatim (to find places), the Overpass API (sights), Open-Meteo / MET Norway / DWD (weather)
and brouter.de (the map data, once per area). Routes, searches and settings are not sent
anywhere else, and nothing is sent to the project's authors.

## For the person who publishes it

- The kit lives in `easy-install/`; `python3 scripts/make_easy_bundle.py` builds the zip into
  `dist/`. The release workflow (`.github/workflows/release.yml`) does that on a version tag
  (`v0.2.0`), publishes the container image to `ghcr.io/jonasheinickebio/pybikerouter` (amd64 and
  arm64) and attaches the zip to the GitHub release. Until an image is published the scripts
  cannot pull it: set `PYBIKEROUTER_IMAGE` to a locally built image to try the kit.
- `easy-install/brouter/` holds copies of `docker/brouter/profiles` and `lookups.dat`; a test
  fails when they drift apart.
