# Changelog

## 0.1.0-rc1 — 2026-08-12

First release candidate. Zune Sync restarts from
[android-file-transfer-linux](https://github.com/whoozle/android-file-transfer-linux) (upstream
4.6, LGPL-2.1) rather than the earlier gMTP/libmtp prototype, and adds Jellyfin syncing, playlist
support, and several device-side fixes.

Verified end to end against a **Zune HD** (USB `045e:063e`, firmware 04.05.00114.00) and a
Jellyfin 10.11.5 server: 31 tracks and 4 playlists synced, 0 failures.

### Added

- **`tools/jellyfin-sync`** — syncs music from a Jellyfin server to the device without staging a
  library by hand. Selection by playlist, favourites, artist or album, de-duplicated by item id;
  transcodes only what the Zune cannot play (FLAC/Opus/… to MP3 V0, while MP3/AAC/WMA pass through
  untouched); rewrites tags against MusicBrainz; embeds album art; uploads in batches so the
  on-device media library loads once per batch rather than once per track. Runs are incremental
  via a state file.
- **Playlists.** `Library` can now create and populate `AbstractAVPlaylist` objects. New CLI
  commands: `zune-playlists`, `zune-playlist-clear`, `zune-playlist-add`, `zune-playlist-add-id`.
  Jellyfin playlist membership is rebuilt on the device from object ids alone, needing neither the
  audio files nor a download.
- **`device-info` now prints supported operations and object formats.** Upstream printed five
  strings, so a device's actual capabilities were invisible. This is what established that the
  Zune HD wants `AbstractAVPlaylist` (0xba05) and not `AbstractAudioPlaylist` (0xba09).
- `zune-import` reports each track's object id, on both import and skip, so a caller can reference
  the track afterwards.

### Fixed

- **`zune-import` no longer duplicates tracks.** It now performs the same `Library::HasTrack`
  check the Qt import path always had; re-importing skips instead of creating a second copy.
- **MusicBrainz resolution is deterministic.** Two separate ordering bugs made an unchanged track
  resolve differently on a re-sync — and therefore look new to the device and duplicate:
  recording selection took the first search hit among equally-scored candidates, and release
  selection broke ties on date alone. Both now sort on a stable secondary key.
- **Reissues no longer set the wrong year.** Release selection took whatever MusicBrainz listed
  first, tagging a 2010 single as 2024. It now prefers the earliest release and takes the year
  from the release group's first-release-date.
- **Title-only MusicBrainz search no longer mismatches.** A track with no artist matched a
  different remix at score 90+; a text search now requires an artist to constrain it.
- **Tags are no longer destroyed on a miss.** Existing file tags are only cleared when there is
  something authoritative to replace them with.
- Object ids parse correctly out of `aft-mtp-cli` output, which carries ANSI escapes from the
  progress bar immediately before the message.

### Known limitations

- **Wireless sync is not implemented.** It is PTP/IP and has been reverse engineered by the Xune
  project (also LGPL-2.1), but pairing must be established over USB in two phases first, and
  `Session` still owns a concrete USB `PipePacketer` with no transport seam. See `ZUNE-SYNC.md`.
- **`Library::CreateTrack` never sends `ObjectProperty::AlbumArtist` (0xdc9b)** as a property of
  its own. Album artist is folded into the artist field, so a track's own "feat." credit does not
  survive to the device even though this tool writes both correctly into the file.
- Duplicate detection is keyed on (album, title, track number), so a deliberate metadata change
  legitimately produces a new album rather than a match.
- The Zune can stop answering MTP under sustained enumeration — several `ls-objects` walks back to
  back wedged it, with USB itself remaining healthy. `aft-mtp-cli -R` resets it. Give the device
  time to index after a large sync.
- Upstream's Qt and FUSE frontends are untested here; the CLI is what this release exercises.
