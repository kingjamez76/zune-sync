# Zune Sync

A media sync tool for the Microsoft Zune and Zune HD, based on
[android-file-transfer-linux](https://github.com/whoozle/android-file-transfer-linux) by
Vladimir Menshakov (whoozle), LGPLv2.1.

Upstream AFT works with both the Zune and Zune HD as-is — verified 2026-08-12. This fork exists to
build a better sync experience on top of it, not to fix anything broken. See
[`docs/legacy-gmtp/`](docs/legacy-gmtp/) for why the earlier gMTP-based prototype was abandoned.

Upstream's `README.md` is left untouched so that `git merge upstream/master` stays clean. Project
documentation lives here and under `docs/`.

## Relationship to upstream

```
origin    git@github.com:kingjamez76/zune-sync.git
upstream  git@github.com:whoozle/android-file-transfer-linux.git
```

Pull upstream fixes with:

```sh
git fetch upstream && git merge upstream/master
```

A pristine, unmodified copy of upstream is also kept at
`kingjamez76/android-file-transfer-linux`.

## Goals

1. **Jellyfin sync** — pull music straight from a Jellyfin server instead of downloading to the
   computer first, transcoding to MP3 where the Zune needs it and correcting tags against
   MusicBrainz before upload. This sits outside the C++ entirely: it prepares files with correct
   tags and hands them to the CLI, because `Metadata::Read` takes its metadata from the file's own
   tags.
2. **Playlist support** — upstream knows the playlist object formats
   (`AbstractAudioPlaylist` 0xba09, `M3uPlaylist`, `WplPlaylist`, …) but never creates playlist
   objects. The work mirrors the existing album path: create the abstract object with
   `SendObjectPropList`, then attach tracks with `SetObjectReferences`. Confirm the device
   advertises the format via `aft-mtp-cli`'s `device-info` first.
3. **Wireless sync** — the speculative one. `Session` owns a concrete `PipePacketer` over a USB
   `BulkPipe` with no transport abstraction, and there is no socket code anywhere in `mtp/`, so
   this needs a transport seam *and* an answer to what the Zune's wireless sync protocol actually
   is. Research before committing.

## Building

CMake. `taglib` is required for metadata import (`BUILD_TAGLIB`, found via pkg-config) — without
it there is no tag reading at all, which would defeat the point.
