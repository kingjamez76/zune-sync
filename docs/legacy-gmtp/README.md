# Legacy gMTP prototype (archived)

Zune Sync originally started as a fork of [gMTP](https://gmtp.sourceforge.io/) 1.3.11 (BSD, last
upstream release 2017) built against libmtp 1.1.23. That prototype was abandoned in favour of
[android-file-transfer-linux](https://github.com/whoozle/android-file-transfer-linux) (LGPLv2.1),
which this repository is now based on.

`gmtp-1.3.11-zune-sync.patch` is the complete diff of the gMTP prototype against the pristine
1.3.11 tarball, kept only as a record of what was learned. It touches `about.c`, `interface.c`,
`main.c`, `metatag_info.c`, `metatag_info.h` and `mtp.c` — rebranding plus the metadata workaround
described below. It is not intended to be applied to anything.

## Why the rewrite

The Zune's firmware treats audio object metadata as **read-only once the object is committed**.
Calling `LIBMTP_Set_Object_String` on a property such as `LIBMTP_PROPERTY_AlbumArtist` after
`LIBMTP_Send_Track_From_File` returns PTP error `0x200f` (Access Denied). Only an atomic
`SendObjectPropList` — sent *with* the file data during the initial upload — can set track
metadata on a Zune. That is how Windows Media Player's MTP stack and the official Zune desktop
software do it.

libmtp 1.1.23 cannot express this: its `LIBMTP_track_t` struct has no `albumartist` field, so
AlbumArtist can never be part of the initial property list. The only fixes available were to
substitute the album artist into `track->artist` before sending (what this patch does, at the cost
of losing per-track "feat." attribution) or to maintain a locally patched libmtp indefinitely.

android-file-transfer-linux has no such limitation. It implements PTP/MTP directly rather than
going through libmtp, and its media library layer (`mtp/metadata/Library.cpp`) already builds the
property list and sends it atomically with the file:

- `Library::CreateTrack` → `SendObjectPropList` with Artist/ArtistId, Name, Track, Genre
- `Library::CreateAlbum` → real `AbstractAudioAlbum` objects, linked with `SetObjectReferences`
- `Library::AddCover` → album art via `RepresentativeSampleData`
- `Metadata::Read` already prefers the `ALBUMARTIST` / `ALBUM ARTIST` / `MUSICBRAINZ_ALBUMARTIST`
  tag when present, applying the same workaround upstream and automatically

Upstream also ships `zune-init` and `zune-import` CLI commands and Microsoft's MTPZ trusted-app
handshake (`mtp/mtpz/TrustedApp.cpp`, requires `~/.mtpz-data`), which is what allows the Zune HD
to connect at all.

## Known remaining gap

`Library::CreateTrack` never sends `ObjectProperty::AlbumArtist` (`0xdc9b`) as a property in its
own right, even though the enum defines it. Because `Metadata::Read` folds the album artist into
the artist field, compilations group correctly but a track's own artist credit is lost. Sending
both as separate properties is a small change in `CreateTrack` — and is the correct fix that was
impossible under libmtp.
