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
2. **Playlist support** — upstream knows the playlist object formats but never creates playlist
   objects. The work mirrors the existing album path: create the abstract object with
   `SendObjectPropList`, then attach tracks with `SetObjectReferences`. See the device capabilities
   below for which format to use.
3. **Wireless sync** — no longer speculative as to protocol; see below.

## Wireless sync research (2026-08-12)

The Zune's wireless sync is **PTP/IP** — the same PTP/MTP command set `mtp/ptp/` already
implements, carried over TCP instead of USB bulk endpoints. It has been reverse engineered
by the [Xune](https://github.com/xune-software/xune-releases) project, whose
[XuneSyncLibrary](https://github.com/xune-software/XuneSyncLibrary) is **LGPL-2.1 — the same
licence as this codebase**, so its findings are compatible with our work here.

What that implies for us:

- **Pairing is a prerequisite and happens over USB, in two phases** — a sync pairing, then a
  wireless pairing that configures the device's network credentials. An unpaired device simply
  will not talk to a given host over the network. XuneSyncLibrary exposes these as
  `zune_device_establish_sync_pairing()` and `zune_device_establish_wireless_pairing()`.
- **Discovery is SSDP**; the device announces itself and the host reacts, then the host opens the
  PTP/IP connection to the device.
- Xune's own notes describe its PTP/IP path as "implemented but not actively tested", so this is
  thin ice even in the prior art.

Observed on this network (192.168.4.0/22, eero) with wireless sync switched on at the device:

- The Zune does not appear in ARP after a full-subnet sweep, advertises nothing over mDNS, and
  nothing on the LAN listens on 15740 (the standard PTP/IP port).
- **An SSDP `M-SEARCH` drew zero responders network-wide** — not one device, despite mDNS working
  normally. So SSDP appears to be filtered here, which would break the discovery step even after
  pairing. Worth testing directly against the device's IP once it is paired, rather than relying
  on multicast.

The remaining code-side obstacle is unchanged: `Session` owns a concrete `PipePacketer` over a USB
`BulkPipe` ([Session.h](mtp/ptp/Session.h)), and there is no socket code in `mtp/`, so a transport
seam has to be introduced above `BulkPipe` before any of this can be wired in.

## Device capabilities (Zune HD, firmware 04.05.00114.00)

Dumped with `aft-mtp-cli device-info` on 2026-08-12. USB id `045e:063e`; the vendor extension list
includes `microsoft.com/MTPZ: 1.0`, so `~/.mtpz-data` must be present to connect.

Operations relevant to the media library — all three that `Library::Supported()` requires are
present, so `zune-import` is available:

| Operation | Code |
|---|---|
| `GetObjectPropList` | 0x9805 |
| `SendObjectPropList` | 0x9808 |
| `SetObjectReferences` | 0x9811 |

Object formats advertised:

| Format | Code | |
|---|---|---|
| `Mp3` | 0x3009 | audio |
| `Wma` / `Asf` | 0xb901 / 0x300c | audio |
| `M4a` / `Aac` | 0xb215 / 0xb903 | audio |
| `AbstractAudioAlbum` | 0xba03 | album objects |
| `Artist` | 0xb218 | artist objects |
| **`AbstractAVPlaylist`** | **0xba05** | **playlists** |
| `AbstractMediacast` | 0xba0b | |

**Playlists must use `AbstractAVPlaylist` (0xba05).** The device does *not* advertise
`AbstractAudioPlaylist` (0xba09), which would be the obvious guess for an audio playlist.

The audio format list is also what makes the transcode policy in `tools/jellyfin-sync` correct:
MP3, WMA and AAC pass through, everything else is converted.

### Known device-side gap

`zune-import` in the CLI does not check `Library::HasTrack` before creating a track, unlike the Qt
import path (`qt/commandqueue.cpp`). Re-importing the same track therefore creates a duplicate on
the device rather than being skipped.

## Building

CMake. `taglib` is required for metadata import (`BUILD_TAGLIB`, found via pkg-config) — without
it there is no tag reading at all, which would defeat the point.
