# jellyfin-sync

Sync music from a Jellyfin server straight to a Zune, without staging a library on the computer
first.

```
Jellyfin  ->  download  ->  transcode (only if needed)  ->  MusicBrainz  ->  aft-mtp-cli
```

No C++ is involved. The contract with Zune Sync is simply "write correct tags into the file, then
hand it to the CLI" — upstream's `Metadata::Read` takes its metadata from the file's own tags and
sends it to the device atomically with the file data, which is the one thing the Zune firmware
requires.

## Setup

```sh
cd tools/jellyfin-sync
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp config.example.toml config.toml   # gitignored
$EDITOR config.toml                  # server URL, API key, what to sync
```

You also need `ffmpeg`/`ffprobe` on PATH, and `aft-mtp-cli` built from this repo:

```sh
cd ../..
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_QT_UI=OFF -DBUILD_FUSE=OFF -DBUILD_PYTHON=OFF
cmake --build build
```

`build/cli/aft-mtp-cli` is preferred over any `aft-mtp-cli` on PATH. That ordering is deliberate:
a distro-packaged copy would otherwise shadow this repo's build and local C++ changes would appear
to do nothing.

## Use

```sh
./.venv/bin/python -m jellyfin_sync --dry-run     # show what would sync, change nothing
./.venv/bin/python -m jellyfin_sync --limit 5     # try a handful first
./.venv/bin/python -m jellyfin_sync               # full run
./.venv/bin/python -m jellyfin_sync --device-info # dump the device's MTP capabilities
```

Runs are incremental: uploaded tracks are recorded in `sync.state_file` and skipped next time.
`--force` re-syncs them anyway.

## What it does

**Selection.** Playlists, favourites, named artists and named albums are gathered and
de-duplicated by Jellyfin item id, so a track in two selections is only transferred once.

**Transcoding.** The Zune plays MP3, unprotected WMA and unprotected AAC. Only files outside that
set (FLAC, Opus, Vorbis, ALAC…) are converted, to VBR MP3 at LAME V0 by default; anything already
playable is passed through untouched rather than needlessly re-encoded. Note that WMA Pro is
deliberately *not* treated as playable.

**Metadata.** A full Picard-style rewrite, matched in this order:

1. MusicBrainz IDs Jellyfin already holds for the item
2. AcoustID audio fingerprint — most accurate, needs `fpcalc` plus a free API key
3. MusicBrainz text search, accepted only at a score of 90+

Canonical title, artist, **album artist**, album, track/disc number, year and genre are written
back into the file, and album art is pulled from Jellyfin and embedded. Any miss falls back to
Jellyfin's own metadata rather than failing the track. ID3v2.3 is used in preference to v2.4,
which old players handle badly.

**Upload.** Tracks go over in batches (`device.batch_size`) because `zune-import` loads the
on-device media library on first use — one invocation per track would reload it every time. If a
batch fails, its tracks are retried individually so one bad file can't take out the rest.

## Playlists

Playlist *membership* is already recorded in the state file under `playlists`, but playlists are
not yet created on the device — upstream AFT knows the playlist object formats and never builds
playlist objects. That's tracked as goal 2 in [`../../ZUNE-SYNC.md`](../../ZUNE-SYNC.md); the
data captured here is what that work will consume.

## Known gap

`Library::CreateTrack` in the C++ core never sends `ObjectProperty::AlbumArtist` (`0xdc9b`) as a
property of its own, so although this tool writes a correct, distinct album artist into the file,
upstream currently folds it into the artist field on the way to the device. Fixing that in
`CreateTrack` is what makes per-track "feat." credits survive.
