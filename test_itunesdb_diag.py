#!/usr/bin/env python3
"""
test_itunesdb_diag.py -- unit tests for itunesdb_diag.py.

Builds small, synthetic iTunesDB byte buffers by hand (matching the real
on-disk format closely enough to exercise the parser) so the tests don't
depend on having a real iPod or a real iTunesDB file available.

Run with:
    python3 -m unittest test_itunesdb_diag.py -v

No third-party dependencies -- standard library only, same as the tool
itself.
"""

import struct
import unittest

import itunesdb_diag as diag


# ---------------------------------------------------------------------------
# Synthetic iTunesDB builder
# ---------------------------------------------------------------------------

def make_mhod(mhod_type, value):
    """Build a single string-type mhod chunk."""
    s = value.encode('utf-16-le')
    buf = bytearray(40)
    buf[0:4] = b'mhod'
    struct.pack_into('<I', buf, 4, 24)              # header_len (not load-bearing for parsing)
    struct.pack_into('<I', buf, 8, 40 + len(s))      # total_len
    struct.pack_into('<I', buf, 12, mhod_type)
    struct.pack_into('<I', buf, 28, len(s))
    return bytes(buf) + s


def make_mhit(title, album, artist, compilation_flag=0, disc_number=1,
              total_discs=1, dbid=0, unique_id=0, genre=None, bitrate=0,
              year=0, filetype=b'M4A ', artist_sort=None, album_sort=None,
              album_artist_sort=None):
    """Build a single mhit (track) chunk with the given field values."""
    mhods = [make_mhod(1, title), make_mhod(3, album), make_mhod(4, artist)]
    if genre is not None:
        mhods.append(make_mhod(5, genre))
    if artist_sort is not None:
        mhods.append(make_mhod(23, artist_sort))
    if album_sort is not None:
        mhods.append(make_mhod(28, album_sort))
    if album_artist_sort is not None:
        mhods.append(make_mhod(29, album_artist_sort))
    num_strings = len(mhods)

    header_len = 320  # comfortably past every fixed offset this tool reads
    body = bytearray(header_len)
    body[0:4] = b'mhit'
    struct.pack_into('<I', body, 4, header_len)
    struct.pack_into('<I', body, 12, num_strings)
    struct.pack_into('<I', body, 16, unique_id)
    body[24:28] = filetype
    struct.pack_into('<I', body, 52, year)
    struct.pack_into('<I', body, 56, bitrate)
    body[30] = compilation_flag
    struct.pack_into('<I', body, 92, disc_number)
    struct.pack_into('<I', body, 96, total_discs)
    struct.pack_into('<Q', body, 112, dbid)
    struct.pack_into('<H', body, 314, 0)  # album_id, always 0 in observed real files

    full = bytearray(bytes(body) + b''.join(mhods))
    struct.pack_into('<I', full, 8, len(full))  # mhit total_len
    return bytes(full)


def make_itunesdb(tracks):
    """Build a minimal but structurally valid iTunesDB containing the given
    list of pre-built mhit byte blobs (e.g. from make_mhit)."""
    mhlt_body = b''.join(tracks)
    mhlt = bytearray(12)
    mhlt[0:4] = b'mhlt'
    struct.pack_into('<I', mhlt, 4, 12)
    struct.pack_into('<I', mhlt, 8, len(tracks))  # mhlt's field3 = child COUNT, not length
    mhlt = bytes(mhlt) + mhlt_body

    mhsd = bytearray(16)
    mhsd[0:4] = b'mhsd'
    struct.pack_into('<I', mhsd, 4, 16)
    struct.pack_into('<I', mhsd, 12, 1)  # mhsd type 1 = track list
    mhsd_full = bytearray(bytes(mhsd) + mhlt)
    struct.pack_into('<I', mhsd_full, 8, len(mhsd_full))  # mhsd total_len

    mhbd = bytearray(244)
    mhbd[0:4] = b'mhbd'
    struct.pack_into('<I', mhbd, 4, 244)
    struct.pack_into('<I', mhbd, 20, 1)  # num mhsd children
    full_file = bytearray(bytes(mhbd) + bytes(mhsd_full))
    struct.pack_into('<I', full_file, 8, len(full_file))
    return full_file


def make_mhyp(title, data_object_child_count=1, extra_header_bytes=b''):
    """Build a single mhyp (playlist) chunk with just a Title mhod child.

    `extra_header_bytes` simulates the partially-understood region of the
    real mhyp header (master-playlist flag, timestamp, etc.) -- tests use
    this to confirm the parser reports it back faithfully as a hex dump
    without claiming to interpret it.
    """
    mhod = make_mhod(1, title)
    header_len = 16 + len(extra_header_bytes)
    buf = bytearray(header_len)
    buf[0:4] = b'mhyp'
    struct.pack_into('<I', buf, 4, header_len)
    struct.pack_into('<I', buf, 12, data_object_child_count)
    buf[16:16 + len(extra_header_bytes)] = extra_header_bytes
    full = bytearray(bytes(buf) + mhod)
    struct.pack_into('<I', full, 8, len(full))  # total_len
    return bytes(full)


def make_itunesdb_with_playlists(tracks, playlists):
    """Build a minimal iTunesDB containing both a track list (mhsd holding
    an mhlt) and a playlist list (mhsd holding an mhlp -> mhyp chunks)."""
    mhlt_body = b''.join(tracks)
    mhlt = bytearray(12)
    mhlt[0:4] = b'mhlt'
    struct.pack_into('<I', mhlt, 4, 12)
    struct.pack_into('<I', mhlt, 8, len(tracks))
    mhlt = bytes(mhlt) + mhlt_body

    mhsd_tracks = bytearray(16)
    mhsd_tracks[0:4] = b'mhsd'
    struct.pack_into('<I', mhsd_tracks, 4, 16)
    struct.pack_into('<I', mhsd_tracks, 12, 1)
    mhsd_tracks_full = bytearray(bytes(mhsd_tracks) + mhlt)
    struct.pack_into('<I', mhsd_tracks_full, 8, len(mhsd_tracks_full))

    mhlp_body = b''.join(playlists)
    mhlp = bytearray(12)
    mhlp[0:4] = b'mhlp'
    struct.pack_into('<I', mhlp, 4, 12)
    struct.pack_into('<I', mhlp, 8, len(playlists))  # documented exception: child COUNT
    mhlp = bytes(mhlp) + mhlp_body

    mhsd_playlists = bytearray(16)
    mhsd_playlists[0:4] = b'mhsd'
    struct.pack_into('<I', mhsd_playlists, 4, 16)
    struct.pack_into('<I', mhsd_playlists, 12, 2)  # arbitrary; parser checks child tag, not this
    mhsd_playlists_full = bytearray(bytes(mhsd_playlists) + mhlp)
    struct.pack_into('<I', mhsd_playlists_full, 8, len(mhsd_playlists_full))

    mhbd = bytearray(244)
    mhbd[0:4] = b'mhbd'
    struct.pack_into('<I', mhbd, 4, 244)
    struct.pack_into('<I', mhbd, 20, 2)  # 2 mhsd children
    full_file = bytearray(bytes(mhbd) + bytes(mhsd_tracks_full) + bytes(mhsd_playlists_full))
    struct.pack_into('<I', full_file, 8, len(full_file))
    return full_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParsing(unittest.TestCase):
    def test_parses_basic_fields(self):
        db = make_itunesdb([
            make_mhit("Song A", "Album X", "Artist 1", compilation_flag=1,
                      disc_number=1, total_discs=1, dbid=111, unique_id=1,
                      genre="Pop", bitrate=256, year=1999),
        ])
        tracks = diag.parse_all_tracks(db)
        self.assertEqual(len(tracks), 1)
        t = tracks[0]
        self.assertEqual(t['Title'], "Song A")
        self.assertEqual(t['Album'], "Album X")
        self.assertEqual(t['Artist'], "Artist 1")
        self.assertEqual(t['Genre'], "Pop")
        self.assertEqual(t['compilation_flag'], 1)
        self.assertEqual(t['disc_number'], 1)
        self.assertEqual(t['total_discs'], 1)
        self.assertEqual(t['dbid'], 111)
        self.assertEqual(t['bitrate'], 256)
        self.assertEqual(t['year'], 1999)
        self.assertEqual(t['filetype'], 'M4A')

    def test_unicode_titles_round_trip(self):
        db = make_itunesdb([
            make_mhit("Caf\u00e9 \u2014 Stra\u00dfe", "Caf\u00e9 Album", "B\u00f4a"),
        ])
        tracks = diag.parse_all_tracks(db)
        self.assertEqual(tracks[0]['Title'], "Caf\u00e9 \u2014 Stra\u00dfe")
        self.assertEqual(tracks[0]['Artist'], "B\u00f4a")

    def test_rejects_non_itunesdb_file(self):
        with self.assertRaises(diag.ITunesDBError):
            diag.parse_all_tracks(bytearray(b'not an itunesdb file at all'))

    def test_multiple_tracks_and_albums(self):
        db = make_itunesdb([
            make_mhit("T1", "Album A", "Artist", unique_id=1),
            make_mhit("T2", "Album A", "Artist", unique_id=2),
            make_mhit("T3", "Album B", "Artist", unique_id=3),
        ])
        tracks = diag.parse_all_tracks(db)
        self.assertEqual(len(tracks), 3)
        by_album = diag.group_by_album(tracks)
        self.assertEqual(set(by_album.keys()), {"Album A", "Album B"})
        self.assertEqual(len(by_album["Album A"]), 2)
        self.assertEqual(len(by_album["Album B"]), 1)


class TestComputeMajority(unittest.TestCase):
    def test_clear_majority(self):
        self.assertEqual(diag.compute_majority([1, 1, 1, 0, 0]), 1)

    def test_tie_breaks_to_first_seen(self):
        self.assertEqual(diag.compute_majority([0, 1]), 0)
        self.assertEqual(diag.compute_majority([1, 0]), 1)

    def test_single_value(self):
        self.assertEqual(diag.compute_majority([5]), 5)

    def test_works_on_tuples(self):
        pairs = [(1, 1), (1, 1), (0, 0)]
        self.assertEqual(diag.compute_majority(pairs), (1, 1))


class TestFindNonUniformAlbums(unittest.TestCase):
    def test_flags_compilation_mismatch(self):
        db = make_itunesdb([
            make_mhit("T1", "Bad Album", "Artist", compilation_flag=1, unique_id=1),
            make_mhit("T2", "Bad Album", "Artist", compilation_flag=1, unique_id=2),
            make_mhit("T3", "Bad Album", "Artist", compilation_flag=0, unique_id=3),
        ])
        tracks = diag.parse_all_tracks(db)
        flagged = diag.find_non_uniform_albums(tracks)
        self.assertIn("Bad Album", flagged)
        self.assertEqual(len(flagged["Bad Album"]), 3)

    def test_flags_disc_mismatch(self):
        db = make_itunesdb([
            make_mhit("T1", "Disc Album", "Artist", disc_number=1, total_discs=1, unique_id=1),
            make_mhit("T2", "Disc Album", "Artist", disc_number=0, total_discs=0, unique_id=2),
        ])
        tracks = diag.parse_all_tracks(db)
        flagged = diag.find_non_uniform_albums(tracks)
        self.assertIn("Disc Album", flagged)

    def test_does_not_flag_uniform_album(self):
        db = make_itunesdb([
            make_mhit("T1", "Clean Album", "Artist", compilation_flag=0,
                      disc_number=1, total_discs=1, unique_id=1),
            make_mhit("T2", "Clean Album", "Artist", compilation_flag=0,
                      disc_number=1, total_discs=1, unique_id=2),
        ])
        tracks = diag.parse_all_tracks(db)
        flagged = diag.find_non_uniform_albums(tracks)
        self.assertEqual(flagged, {})

    def test_ignores_single_track_albums(self):
        # A solo single can't be "non-uniform" against itself.
        db = make_itunesdb([
            make_mhit("Only Track", "Solo Single", "Artist", compilation_flag=1, unique_id=1),
        ])
        tracks = diag.parse_all_tracks(db)
        flagged = diag.find_non_uniform_albums(tracks)
        self.assertEqual(flagged, {})

    def test_flags_artist_sort_mismatch_even_with_matching_displayed_tags(self):
        # This is the CONFIRMED real-world mechanism: Artist/Album/AlbumArtist
        # all match perfectly, but ArtistSort differs -- this must be caught,
        # since it's exactly what looked fine in iTunes but broke Cover Flow.
        db = make_itunesdb([
            make_mhit("T1", "Sorted Album", "The Band", unique_id=1, artist_sort="Band, The"),
            make_mhit("T2", "Sorted Album", "The Band", unique_id=2, artist_sort="Band, The"),
            make_mhit("T3", "Sorted Album", "The Band", unique_id=3, artist_sort=None),
        ])
        tracks = diag.parse_all_tracks(db)
        flagged = diag.find_non_uniform_albums(tracks)
        self.assertIn("Sorted Album", flagged)

    def test_flags_album_sort_and_album_artist_sort_mismatch(self):
        db1 = make_itunesdb([
            make_mhit("T1", "Album", "Artist", unique_id=1, album_sort="Album"),
            make_mhit("T2", "Album", "Artist", unique_id=2, album_sort=None),
        ])
        self.assertIn("Album", diag.find_non_uniform_albums(diag.parse_all_tracks(db1)))

        db2 = make_itunesdb([
            make_mhit("T1", "Album2", "Artist", unique_id=1, album_artist_sort="Artist"),
            make_mhit("T2", "Album2", "Artist", unique_id=2, album_artist_sort=None),
        ])
        self.assertIn("Album2", diag.find_non_uniform_albums(diag.parse_all_tracks(db2)))

    def test_does_not_flag_uniformly_blank_sort_fields(self):
        # All tracks genuinely having NO sort override is fine -- it's
        # inconsistency (some have it, some don't, or the values differ)
        # that's the actual signal.
        db = make_itunesdb([
            make_mhit("T1", "Clean Album", "Artist", unique_id=1),
            make_mhit("T2", "Clean Album", "Artist", unique_id=2),
        ])
        tracks = diag.parse_all_tracks(db)
        flagged = diag.find_non_uniform_albums(tracks)
        self.assertEqual(flagged, {})

    def test_does_not_flag_uniformly_populated_sort_fields(self):
        # All tracks having the SAME explicit sort override is also fine.
        db = make_itunesdb([
            make_mhit("T1", "Clean Album 2", "Artist", unique_id=1,
                      artist_sort="Artist", album_sort="Clean Album 2",
                      album_artist_sort="Artist"),
            make_mhit("T2", "Clean Album 2", "Artist", unique_id=2,
                      artist_sort="Artist", album_sort="Clean Album 2",
                      album_artist_sort="Artist"),
        ])
        tracks = diag.parse_all_tracks(db)
        flagged = diag.find_non_uniform_albums(tracks)
        self.assertEqual(flagged, {})


class TestApplyFix(unittest.TestCase):
    def setUp(self):
        self.db = make_itunesdb([
            make_mhit("T1", "Mixed Album", "Artist", compilation_flag=1,
                      disc_number=1, total_discs=1, unique_id=1),
            make_mhit("T2", "Mixed Album", "Artist", compilation_flag=1,
                      disc_number=1, total_discs=1, unique_id=2),
            make_mhit("T3", "Mixed Album", "Artist", compilation_flag=0,
                      disc_number=0, total_discs=0, unique_id=3),
            make_mhit("T4", "Untouched Album", "Artist", compilation_flag=0,
                      disc_number=5, total_discs=9, unique_id=4),
        ])

    def test_patches_only_named_album(self):
        tracks = diag.parse_all_tracks(self.db)
        by_album = diag.group_by_album(tracks)
        results = diag.apply_fix(self.db, by_album, ["Mixed Album"])

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['found'])
        self.assertEqual(results[0]['majority_comp'], 1)
        self.assertEqual(results[0]['majority_disc'], (1, 1))
        self.assertEqual(len(results[0]['patches']), 2)  # T3's comp + disc both changed

        # Re-parse the (now mutated) buffer and confirm it's actually fixed.
        re_tracks = diag.parse_all_tracks(self.db)
        flagged_after = diag.find_non_uniform_albums(re_tracks)
        self.assertNotIn("Mixed Album", flagged_after)

        # The untouched album must be completely unaffected.
        untouched = diag.group_by_album(re_tracks)["Untouched Album"][0]
        self.assertEqual(untouched['compilation_flag'], 0)
        self.assertEqual(untouched['disc_number'], 5)
        self.assertEqual(untouched['total_discs'], 9)

    def test_unnamed_flagged_album_is_left_alone(self):
        # "Untouched Album" only has one track here so it's not flagged by
        # find_non_uniform_albums anyway, but the key guarantee is: fix only
        # ever touches albums explicitly passed in album_names.
        tracks = diag.parse_all_tracks(self.db)
        by_album = diag.group_by_album(tracks)
        before = bytes(self.db)
        diag.apply_fix(self.db, by_album, ["Mixed Album"])
        # Bytes belonging to "Untouched Album"'s track must be byte-identical.
        untouched_offset = by_album["Untouched Album"][0]['offset']
        self.assertEqual(
            self.db[untouched_offset:untouched_offset + 320],
            before[untouched_offset:untouched_offset + 320],
        )

    def test_unknown_album_name_reported_not_found(self):
        tracks = diag.parse_all_tracks(self.db)
        by_album = diag.group_by_album(tracks)
        results = diag.apply_fix(self.db, by_album, ["Does Not Exist"])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]['found'])
        self.assertEqual(results[0]['patches'], [])

    def test_already_uniform_album_produces_no_patches(self):
        tracks = diag.parse_all_tracks(self.db)
        by_album = diag.group_by_album(tracks)
        # "Untouched Album" has just one track, trivially "uniform."
        results = diag.apply_fix(self.db, by_album, ["Untouched Album"])
        self.assertTrue(results[0]['found'])
        self.assertEqual(results[0]['patches'], [])


class TestPlaylistParsing(unittest.TestCase):
    def test_extracts_playlist_titles_in_order(self):
        db = make_itunesdb_with_playlists(
            tracks=[make_mhit("T1", "Album", "Artist", unique_id=1)],
            playlists=[
                make_mhyp("Master", extra_header_bytes=b'\x01\x00\x00\x00'),
                make_mhyp("Phase Music", extra_header_bytes=b'\x00\x00\x00\x00'),
            ],
        )
        playlists = diag.parse_all_playlists(db)
        self.assertEqual([p['Title'] for p in playlists], ["Master", "Phase Music"])

    def test_unknown_header_hex_reflects_real_bytes_without_interpreting_them(self):
        db = make_itunesdb_with_playlists(
            tracks=[make_mhit("T1", "Album", "Artist", unique_id=1)],
            playlists=[
                make_mhyp("A", extra_header_bytes=b'\x01\x00'),
                make_mhyp("B", extra_header_bytes=b'\x00\x00'),
            ],
        )
        playlists = diag.parse_all_playlists(db)
        self.assertEqual(playlists[0]['unknown_header_hex'], '0100')
        self.assertEqual(playlists[1]['unknown_header_hex'], '0000')
        self.assertNotEqual(playlists[0]['unknown_header_hex'], playlists[1]['unknown_header_hex'])

    def test_does_not_confuse_tracks_and_playlists(self):
        # Make sure walking the playlist section doesn't pick up track data,
        # and vice versa -- they're parsed by two independent functions.
        db = make_itunesdb_with_playlists(
            tracks=[
                make_mhit("Track One", "Some Album", "Some Artist", unique_id=1),
                make_mhit("Track Two", "Some Album", "Some Artist", unique_id=2),
            ],
            playlists=[make_mhyp("Only Playlist")],
        )
        tracks = diag.parse_all_tracks(db)
        playlists = diag.parse_all_playlists(db)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0]['Title'], "Only Playlist")


if __name__ == '__main__':
    unittest.main()
