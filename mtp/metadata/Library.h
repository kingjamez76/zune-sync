#ifndef AFTL_MTP_METADATA_LIBRARY_H
#define AFTL_MTP_METADATA_LIBRARY_H

#include <mtp/ptp/ObjectId.h>
#include <mtp/ptp/ObjectFormat.h>
#include <mtp/ByteArray.h>
#include <mtp/types.h>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace mtp
{
	class Session;
	DECLARE_PTR(Session);

	class Library
	{
		SessionPtr		_session;
		StorageId		_storage;

	public:
		enum struct State {
			Initialising,
			QueryingArtists,
			LoadingArtists,
			QueryingAlbums,
			LoadingAlbums,
			Loaded
		};
		using ProgressReporter = std::function<void (State, u64, u64)>;

		struct Artist
		{
			ObjectId 		Id;
			ObjectId		MusicFolderId;
			std::string 	Name;
		};
		DECLARE_PTR(Artist);

		struct Album
		{
			ObjectId 		Id;
			ObjectId		MusicFolderId;
			ArtistPtr		Artist;
			std::string 	Name;
			time_t	 		Year = 0;
			bool			RefsLoaded = false;

			void LoadRefs();

			std::unordered_set<ObjectId> Refs;
			//	track name -> (track index, object id). The object id is what a
			//	playlist has to reference, so it is carried alongside the index.
			std::unordered_multimap<std::string, std::pair<int, ObjectId>> Tracks;
		};
		DECLARE_PTR(Album);

		struct Playlist
		{
			ObjectId					Id;
			std::string					Name;
			std::vector<ObjectId>		Tracks;
		};
		DECLARE_PTR(Playlist);

		struct NewTrackInfo
		{
			ObjectId		Id;
			std::string 	Name;
			int				Index;
		};

	private:
		ObjectId _artistsFolder;
		ObjectId _albumsFolder;
		ObjectId _musicFolder;
		bool _artistSupported;
		bool _albumDateAuthoredSupported;
		bool _albumCoverSupported;
		bool _playlistsSupported;
		bool _playlistFilenameSupported;

		using ArtistMap = std::unordered_map<std::string, ArtistPtr>;
		ArtistMap _artists;

		using AlbumKey = std::pair<ArtistPtr, std::string>;
		struct AlbumKeyHash
		{ size_t operator() (const AlbumKey & key) const {
			return std::hash<ArtistPtr>()(key.first) + std::hash<std::string>()(key.second);
		}};

		using AlbumMap = std::unordered_map<AlbumKey, AlbumPtr, AlbumKeyHash>;
		AlbumMap _albums;

		using PlaylistMap = std::unordered_map<std::string, PlaylistPtr>;
		PlaylistMap _playlists;
		void LoadPlaylists();
		void WritePlaylistRefs(const PlaylistPtr & playlist);

		using NameToObjectIdMap = std::unordered_map<std::string, ObjectId>;
		NameToObjectIdMap ListAssociations(ObjectId parentId);

		ObjectId GetOrCreate(ObjectId parentId, const std::string &name);
		void LoadRefs(AlbumPtr album);

	public:
		Library(const mtp::SessionPtr & session, ProgressReporter && reporter = ProgressReporter());
		~Library();

		static bool Supported(const mtp::SessionPtr & session);

		//search by Metadata?
		ArtistPtr GetArtist(std::string name);
		ArtistPtr CreateArtist(std::string name);

		AlbumPtr GetAlbum(const ArtistPtr & artist, std::string name);
		AlbumPtr CreateAlbum(const ArtistPtr & artist, std::string name, int year);
		bool HasTrack(const AlbumPtr & album, const std::string &name, int trackIndex);
		//	Object id of a track already on the device, or a null id if absent.
		ObjectId GetTrackId(const AlbumPtr & album, const std::string &name, int trackIndex);
		NewTrackInfo CreateTrack(const ArtistPtr & artist, const AlbumPtr & album, ObjectFormat type, std::string name, const std::string & genre, int trackIndex, const std::string &filename, size_t size);
		void AddTrack(AlbumPtr album, const NewTrackInfo &ti);
		void AddCover(AlbumPtr album, const mtp::ByteArray &data);

		//	Playlists are AbstractAVPlaylist objects holding references to track
		//	objects, built the same way albums are.
		bool PlaylistsSupported() const
		{ return _playlistsSupported; }
		const PlaylistMap & GetPlaylists() const
		{ return _playlists; }
		PlaylistPtr GetPlaylist(const std::string & name);
		PlaylistPtr CreatePlaylist(const std::string & name);
		void ClearPlaylist(const PlaylistPtr & playlist);
		void AddToPlaylist(const PlaylistPtr & playlist, ObjectId trackId);
	};
	DECLARE_PTR(Library);
}

#endif
