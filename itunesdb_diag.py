#!/usr/bin/env python3
"""
itunesdb_diag.py -- diagnose (and cautiously, partially fix) the classic
iPod "duplicate album in Cover Flow" bug, by reading the device's binary
iTunesDB directly.

THE BUG
-------
On the iPod Classic / Nano (older firmware), Cover Flow can show one album
as TWO (or more) separate cover tiles, even when every visible tag in
iTunes -- Artist, Album, Album Artist -- looks completely correct and
identical across all of an album's tracks. This has been reported on and
off since at least 2006, with no single agreed-upon fix.

CONFIRMED ROOT CAUSE
---------------------
The iPod's on-device track database (iTunesDB) stores, per track, three
"Sort" fields -- ArtistSort, AlbumSort, AlbumArtistSort -- separate from
the main Artist/Album/Album Artist fields you see in iTunes. These map to
documented Cover Flow sort keys: the device sorts tracks by Sort Artist,
then Artist, then Sort Album, then Album, then Track No, and renders a new
cover tile every time Album changes as you walk through that sorted
sequence.

If even ONE track on an album has different Sort field values than the
rest of the album -- including one track having a value and another being
blank, even if the *displayed* tags look identical -- that track sorts out
of sequence and Cover Flow renders it as a separate album.

This was confirmed by directly inspecting the binary iTunesDB (not by
reasoning from iTunes's UI, which doesn't show these fields anywhere
prominent) across a real, mixed-source ~220-track library, fixing one
small album at a time, and re-verifying at the byte level after each fix
until duplicates actually stopped appearing on the device.

A FALSE LEAD WORTH DOCUMENTING: Compilation flag / Disc Number
------------------------------------------------------------------
Earlier in this investigation, a 1-byte "Compilation" flag and a "Disc
Number"/"Disc Total" pair were found to reliably correlate with every
broken album in the test library: the same outlier tracks that had
mismatched Sort fields also tended to have a mismatched Compilation flag
and/or Disc Number. Fixing ONLY Compilation/Disc, with Sort fields still
mismatched and verified clean via this same tool, did NOT fix Cover Flow.
Both problems share a root cause (one or two tracks on an album skipping
whatever tagging pass the rest of the album went through) -- but
Compilation/Disc were a correlated symptom, not the actual mechanism.
This is kept in the tool (see `summary`'s output) because it's still a
useful hint that an album has this kind of mixed-tagging problem, but it
is not, by itself, the fix.

WHAT THIS SCRIPT DOES
----------------------
  search   Dump every field this script understands for tracks whose
           Album matches a search string -- useful for inspecting one
           album closely.
  summary  Scan the WHOLE library and report every album where any Sort
           field, or Compilation/Disc, is NOT uniform across all of that
           album's tracks. This is the diagnostic step: it tells you
           exactly which albums are affected and why.
  fix      Patches ONLY Compilation/Disc (the correlated symptom, not the
           confirmed cause) for album(s) you name explicitly, writing a
           NEW file. Kept for research purposes -- see the large warning
           printed when you run it, and the README. It is NOT a
           replacement for fixing Sort fields at the source.

THE ACTUAL FIX
---------------
Correct ArtistSort, AlbumSort, and AlbumArtistSort so every track on an
affected album agrees -- in your tag editor, or via iTunes's own Get Info
-> Sorting tab -- then do a normal iTunes sync. Use `summary` afterward to
confirm, at the byte level, that the fix actually reached the device
(this tool's own development repeatedly ran into tag-editor changes that
looked applied in the editor but silently failed to propagate through to
the device on the next sync -- don't trust a visual check alone).

IMPORTANT LIMITATIONS -- PLEASE READ
-------------------------------------
* `fix` mode patches the on-device copy of iTunesDB directly. In testing,
  replacing a real device's iTunesDB with output from `fix` caused BOTH
  iTunes and the iPod itself to show ZERO songs for that device. The
  likely cause is an integrity hash elsewhere in the file that this tool
  does not recalculate. Treat `fix` as experimental and NOT confirmed
  safe for a real device -- use the source-side fix instead.
* `summary` can also flag albums that are legitimately multi-disc
  releases with real, correct Disc Number variation -- not every flagged
  album is actually broken. Always sanity-check the output.
* Tested against iTunesDB files written by iTunes 10.7 and 12.10.11.2 on
  an iPod Classic (7th gen). Field offsets are taken directly from the
  wikiPodLinux iTunesDB specification and should hold for other
  click-wheel iPods, but hasn't been verified against every device/
  iTunes version combination.

Format reference: http://www.ipodlinux.org/ITunesDB/iTunesDB_File.html

Usage:
    python3 itunesdb_diag.py search  <path_to_iTunesDB> "<album search string>"
    python3 itunesdb_diag.py summary <path_to_iTunesDB>
    python3 itunesdb_diag.py fix     <path_to_iTunesDB> <output_path> "<Album1>" ["<Album2>" ...]

Tip: copy the iTunesDB file off the device first (to avoid any chance of
reading a half-written file if something is still syncing), then point
this at the copy.
"""

import argparse
import struct
import sys

# mhod type -> friendly field name, for the "string" style mhods we care
# about. Confirmed against the wikiPodLinux mhod type table.
MHOD_STRING_TYPES = {
    1: "Title",
    3: "Album",
    4: "Artist",
    5: "Genre",
    22: "AlbumArtist",
    23: "ArtistSort",
    27: "TitleSort",
    28: "AlbumSort",
    29: "AlbumArtistSort",
}

# Columns printed by `search` mode, in display order.
DISPLAY_COLUMNS = [
    'Title', 'Artist', 'Album', 'AlbumArtist', 'ArtistSort', 'AlbumSort',
    'AlbumArtistSort', 'Genre', 'filetype', 'year', 'bitrate',
    'compilation_flag', 'album_id', 'disc_number', 'total_discs', 'dbid',
    'unique_id',
]


class ITunesDBError(Exception):
    """Raised when the file doesn't look like a valid iTunesDB."""


# ---------------------------------------------------------------------------
# Low-level chunk parsing
# ---------------------------------------------------------------------------

def read_chunk_header(data, offset):
    """Read the universal 12-byte mh-chunk header shared by every chunk type.

    Returns (tag: bytes, header_len: int, field3: int). `field3` means
    "total length of this chunk" for most chunk types, but means "number
    of children" specifically for mhlt and mhlp.
    """
    tag = bytes(data[offset:offset + 4])
    header_len = struct.unpack_from('<I', data, offset + 4)[0]
    field3 = struct.unpack_from('<I', data, offset + 8)[0]
    return tag, header_len, field3


def parse_mhod(data, offset):
    """Parse a single mhod chunk.

    Returns (mhod_type: int, value: str|None, total_len: int). `value` is
    populated for the "string type" mhods (Title, Artist, Album, the Sort
    variants, etc); other mhod types return value=None since this tool
    doesn't need them.
    """
    tag, _header_len, total_len = read_chunk_header(data, offset)
    if tag != b'mhod':
        raise ITunesDBError(f"Expected mhod at offset {offset}, got {tag!r}")

    mhod_type = struct.unpack_from('<I', data, offset + 12)[0]
    value = None

    # String-type mhods (type < 15, plus the higher-numbered sort/album
    # artist variants which share the identical on-disk layout) store:
    #   offset+28 -> string length in bytes (UTF-16LE, 2 bytes/char)
    #   offset+40 -> the string itself, not NULL-terminated
    if mhod_type in MHOD_STRING_TYPES or mhod_type < 15:
        str_len = struct.unpack_from('<I', data, offset + 28)[0]
        raw = bytes(data[offset + 40: offset + 40 + str_len])
        try:
            value = raw.decode('utf-16-le')
        except UnicodeDecodeError:
            value = raw.decode('utf-16-le', errors='replace')

    return mhod_type, value, total_len


def _decode_filetype(raw4):
    """Decode the 4-byte filetype tag (e.g. b'MP3 ', b'M4A ') defensively."""
    if not raw4:
        return None
    try:
        text = bytes(raw4).decode('ascii').strip()
        if text and text.isprintable():
            return text
    except UnicodeDecodeError:
        pass
    return repr(bytes(raw4))


def parse_mhit(data, offset):
    """Parse a single track item (mhit) plus its mhod children.

    Returns (info: dict, total_len: int). `info['offset']` records this
    track's absolute position in `data`, which `fix` mode needs in order
    to patch specific bytes back in later.
    """
    tag, header_len, total_len = read_chunk_header(data, offset)
    if tag != b'mhit':
        raise ITunesDBError(f"Expected mhit at offset {offset}, got {tag!r}")

    num_strings = struct.unpack_from('<I', data, offset + 12)[0]
    unique_id = struct.unpack_from('<I', data, offset + 16)[0]

    # These fixed-offset fields are only present if this dbversion's mhit
    # header is long enough to contain them -- guard against older/shorter
    # header lengths rather than reading garbage or raising.
    def _maybe(fmt, rel_offset, min_len):
        size = struct.calcsize(fmt)
        if header_len >= rel_offset + size and min_len <= header_len:
            return struct.unpack_from(fmt, data, offset + rel_offset)[0]
        return None

    disc_number = _maybe('<I', 92, 96)
    total_discs = _maybe('<I', 96, 100)
    dbid = _maybe('<Q', 112, 120)
    album_id = _maybe('<H', 314, 316)
    year = _maybe('<I', 52, 56)
    bitrate = _maybe('<I', 56, 60)
    compilation_flag = data[offset + 30] if header_len > 30 else None
    filetype = _decode_filetype(data[offset + 24:offset + 28]) if header_len > 28 else None

    info = {
        'offset': offset,
        'unique_id': unique_id,
        'disc_number': disc_number,
        'total_discs': total_discs,
        'dbid': dbid,
        'album_id': album_id,
        'compilation_flag': compilation_flag,
        'filetype': filetype,
        'year': year,
        'bitrate': bitrate,
    }

    # Walk the mhod children, which start right after this mhit's own header.
    pos = offset + header_len
    end = offset + total_len
    count = 0
    while pos < end and count < num_strings:
        mtag, _mheader_len, mtotal_len = read_chunk_header(data, pos)
        if mtag != b'mhod':
            break
        mhod_type, value, _ = parse_mhod(data, pos)
        if mhod_type in MHOD_STRING_TYPES and value is not None:
            info[MHOD_STRING_TYPES[mhod_type]] = value
        pos += mtotal_len
        count += 1

    return info, total_len


def parse_all_tracks(data, log=None):
    """Walk the full iTunesDB structure and return a list of track-info dicts.

    `log`, if given, is a callable (e.g. print) used to report structural
    info as it's discovered -- useful for the CLI's verbose output, but
    kept optional so this function stays easy to call from tests without
    needing to capture stdout.
    """
    def _log(msg):
        if log is not None:
            log(msg)

    tag, header_len, _ = read_chunk_header(data, 0)
    if tag != b'mhbd':
        raise ITunesDBError(
            f"This doesn't look like an iTunesDB file (expected mhbd, got {tag!r})."
        )

    num_children = struct.unpack_from('<I', data, 20)[0]
    _log(f"[mhbd] file size={len(data)} header_len={header_len} mhsd_children={num_children}")

    pos = header_len
    all_tracks = []

    for child_i in range(num_children):
        stag, sheader_len, stotal_len = read_chunk_header(data, pos)
        if stag != b'mhsd':
            _log(f"  Warning: expected mhsd at offset {pos}, got {stag!r}. Stopping.")
            break
        mhsd_type = struct.unpack_from('<I', data, pos + 12)[0]
        _log(f"[mhsd #{child_i}] type={mhsd_type} offset={pos} total_len={stotal_len}")

        if mhsd_type == 1:
            # Track List
            tpos = pos + sheader_len
            ttag, theader_len, num_songs = read_chunk_header(data, tpos)
            if ttag != b'mhlt':
                _log(f"  Warning: expected mhlt, got {ttag!r}")
            else:
                _log(f"  [mhlt] num_songs={num_songs}")
                ipos = tpos + theader_len
                for _ in range(num_songs):
                    info, ilen = parse_mhit(data, ipos)
                    all_tracks.append(info)
                    ipos += ilen

        pos += stotal_len

    return all_tracks


# ---------------------------------------------------------------------------
# Analysis helpers (pure functions, no I/O -- easy to unit test)
# ---------------------------------------------------------------------------

def group_by_album(tracks):
    """Group a list of track-info dicts by their Album field."""
    by_album = {}
    for info in tracks:
        album = info.get('Album')
        if album is None:
            continue
        by_album.setdefault(album, []).append(info)
    return by_album


def find_non_uniform_albums(tracks):
    """Return {album_name: track_list} for every album (with 2+ tracks)
    where ArtistSort, AlbumSort, AlbumArtistSort, compilation_flag, or
    (disc_number, total_discs) is not uniform across all of that album's
    tracks.

    ArtistSort/AlbumSort/AlbumArtistSort are the CONFIRMED mechanism for
    the Cover Flow duplicate-album bug (see README) -- the iPod sorts
    tracks by Sort Artist, then Artist, then Sort Album, then Album, then
    Track No, generating a new cover tile every time Album changes in that
    sorted order. If even one track's sort fields differ from the rest of
    its album (whether one is blank and others aren't, or the values
    genuinely differ), that track gets sorted out of sequence and Cover
    Flow renders it as a separate album.

    compilation_flag / disc_number / total_discs are checked too, since
    they were found to reliably CORRELATE with the same broken albums in
    practice (likely because both problems share a root cause: one or two
    tracks on the album never went through the same tagging pass as the
    rest). But fixing them alone, with sort fields still mismatched, was
    confirmed NOT to fix the bug -- so treat any compilation/disc-only
    flag as a hint to go check the sort fields by hand too, not as the
    fix in itself.
    """
    flagged = {}
    for album, album_tracks in sorted(group_by_album(tracks).items()):
        if len(album_tracks) < 2:
            continue
        comp_values = set(t.get('compilation_flag') for t in album_tracks)
        disc_pairs = set((t.get('disc_number'), t.get('total_discs')) for t in album_tracks)
        artist_sorts = set(t.get('ArtistSort') for t in album_tracks)
        album_sorts = set(t.get('AlbumSort') for t in album_tracks)
        album_artist_sorts = set(t.get('AlbumArtistSort') for t in album_tracks)
        if (len(comp_values) > 1 or len(disc_pairs) > 1 or len(artist_sorts) > 1
                or len(album_sorts) > 1 or len(album_artist_sorts) > 1):
            flagged[album] = album_tracks
    return flagged


def compute_majority(values):
    """Return the most common value in a list; ties broken by first-seen order."""
    counts = {}
    order = []
    for v in values:
        if v not in counts:
            counts[v] = 0
            order.append(v)
        counts[v] += 1
    return max(order, key=lambda v: counts[v])


def apply_fix(data, tracks_by_album, album_names):
    """Patch `data` (a mutable bytearray) in place for the named albums only.

    IMPORTANT: this only patches compilation_flag and (disc_number,
    total_discs) -- fixed-width integer fields that can be overwritten
    in place without touching anything else in the file. It does NOT
    patch ArtistSort/AlbumSort/AlbumArtistSort, which are the CONFIRMED
    actual cause of the Cover Flow bug (see README) -- those are
    variable-length string fields, and changing their length would shift
    every subsequent byte offset in the file, which this tool does not
    attempt to do.

    In other words: this function patches a correlated symptom, not the
    confirmed cause. It's kept for research/inspection purposes. For an
    actual fix, correct the Sort fields at the source (tag editor / iTunes
    Get Info) and re-sync normally -- see the big warning in `fix`'s CLI
    output and the README.

    For each named album, computes the majority compilation_flag and
    majority (disc_number, total_discs) pair among its tracks, and
    rewrites any outlier track's bytes to match. Returns a list of
    per-album result dicts (for reporting), e.g.:

        {'album': str, 'found': bool, 'majority_comp': int|None,
         'majority_disc': (int, int)|None, 'patches': [str, ...]}

    Albums not present in `tracks_by_album` are reported with found=False
    and are otherwise skipped. Albums never named are never touched.
    """
    results = []
    for album_name in album_names:
        album_tracks = tracks_by_album.get(album_name)
        if not album_tracks:
            results.append({
                'album': album_name, 'found': False,
                'majority_comp': None, 'majority_disc': None, 'patches': [],
            })
            continue

        comp_values = [t.get('compilation_flag') for t in album_tracks]
        disc_pairs = [(t.get('disc_number'), t.get('total_discs')) for t in album_tracks]
        majority_comp = compute_majority(comp_values)
        majority_disc = compute_majority(disc_pairs)

        patches = []
        for t in album_tracks:
            off = t['offset']
            if t.get('compilation_flag') != majority_comp:
                struct.pack_into('B', data, off + 30, majority_comp)
                patches.append(
                    f"{t.get('Title')}: compilation_flag {t.get('compilation_flag')} -> {majority_comp}"
                )
            if (t.get('disc_number'), t.get('total_discs')) != majority_disc:
                struct.pack_into('<I', data, off + 92, majority_disc[0])
                struct.pack_into('<I', data, off + 96, majority_disc[1])
                patches.append(
                    f"{t.get('Title')}: disc {t.get('disc_number')}/{t.get('total_discs')} "
                    f"-> {majority_disc[0]}/{majority_disc[1]}"
                )

        results.append({
            'album': album_name, 'found': True,
            'majority_comp': majority_comp, 'majority_disc': majority_disc,
            'patches': patches,
        })

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load(path):
    with open(path, 'rb') as f:
        return bytearray(f.read())


def cmd_search(args):
    data = _load(args.itunesdb)
    tracks = parse_all_tracks(data, log=print)
    search = args.query.lower()
    matches = [t for t in tracks if search in (t.get('Album') or '').lower()]

    print(f"\n=== Found {len(matches)} track(s) with Album containing '{args.query}' ===\n")
    for info in matches:
        print('-' * 80)
        for c in DISPLAY_COLUMNS:
            print(f"  {c:18s}: {info.get(c)}")

    if matches:
        album_ids = sorted(set(m.get('album_id') for m in matches))
        print(f"\n=== Distinct album_id values across these tracks: {album_ids} ===")
    return 0


def cmd_summary(args):
    data = _load(args.itunesdb)
    tracks = parse_all_tracks(data, log=print)
    flagged = find_non_uniform_albums(tracks)

    print(f"\n=== Scanning {len(tracks)} tracks for non-uniform Compilation/Disc fields per album ===\n")
    if not flagged:
        print("No albums found with non-uniform compilation_flag or disc/total fields.")
        return 0

    for album, album_tracks in flagged.items():
        comp_values = sorted(set(t.get('compilation_flag') for t in album_tracks))
        disc_pairs = sorted(set((t.get('disc_number'), t.get('total_discs')) for t in album_tracks))
        artist_sorts = sorted(set(repr(t.get('ArtistSort')) for t in album_tracks))
        album_sorts = sorted(set(repr(t.get('AlbumSort')) for t in album_tracks))
        album_artist_sorts = sorted(set(repr(t.get('AlbumArtistSort')) for t in album_tracks))
        print('=' * 80)
        print(f"ALBUM: {album}  ({len(album_tracks)} tracks)")
        if len(artist_sorts) > 1:
            print(f"  -> Non-uniform ArtistSort (CONFIRMED Cover Flow mechanism): {artist_sorts}")
        if len(album_sorts) > 1:
            print(f"  -> Non-uniform AlbumSort (CONFIRMED Cover Flow mechanism): {album_sorts}")
        if len(album_artist_sorts) > 1:
            print(f"  -> Non-uniform AlbumArtistSort (CONFIRMED Cover Flow mechanism): {album_artist_sorts}")
        if len(comp_values) > 1:
            print(f"  -> Non-uniform compilation_flag (correlated, not the cause): {comp_values}")
        if len(disc_pairs) > 1:
            print(f"  -> Non-uniform (disc_number, total_discs) (correlated, not the cause): {disc_pairs}")
        for t in album_tracks:
            genre = repr(t.get('Genre'))
            print(f"     [{t.get('compilation_flag')}] disc={t.get('disc_number')}/{t.get('total_discs')} "
                  f"AS={t.get('ArtistSort')!r} ALS={t.get('AlbumSort')!r} AAS={t.get('AlbumArtistSort')!r} "
                  f":: {t.get('Title')}")
    return 0


def cmd_fix(args):
    print("=" * 80)
    print("WARNING: `fix` patches the on-device iTunesDB's binary fields directly.")
    print("In real-device testing, replacing a device's iTunesDB with a copy whose")
    print("bytes were patched this way caused BOTH iTunes and the iPod itself to")
    print("show ZERO songs for that device -- on at least one iPod Classic. The")
    print("likely cause is an integrity hash elsewhere in the file that this tool")
    print("does not recalculate; patching individual fields without it may cause")
    print("the whole database to be treated as untrusted.")
    print()
    print("`fix` is provided for research/inspection purposes. It is NOT confirmed")
    print("safe to use on a real device. The confirmed, safe, durable fix is to")
    print("correct ArtistSort / AlbumSort / AlbumArtistSort at the source (in your")
    print("tag editor or iTunes's own Get Info), then do a normal iTunes sync, and")
    print("use `summary` to verify the fix landed. See the README for details.")
    print("=" * 80)
    print()

    data = _load(args.itunesdb)
    tracks = parse_all_tracks(data, log=print)
    by_album = group_by_album(tracks)

    print(f"\n=== Fix mode: patching only the {len(args.albums)} album(s) you named ===\n")
    results = apply_fix(data, by_album, args.albums)

    total_patches = 0
    for r in results:
        print('=' * 80)
        print(f"ALBUM: {r['album']}")
        if not r['found']:
            print("  !! WARNING: no tracks found with this exact Album name "
                  "(check spelling/capitalization). Skipped.")
            continue
        print(f"  Majority compilation_flag = {r['majority_comp']}, "
              f"majority (disc_number, total_discs) = {r['majority_disc']}")
        if not r['patches']:
            print("  (already uniform -- nothing to patch)")
        for line in r['patches']:
            print(f"  [PATCHED] {line}")
            total_patches += 1

    with open(args.output, 'wb') as f:
        f.write(data)

    print(f"\n=== Done. Patched {total_patches} track(s) across {len(args.albums)} named album(s). ===")
    print(f"=== Output written to: {args.output} ===\n")
    print(">>> This is a NEW file. Your original iTunesDB was NOT modified.")
    print(">>> Next steps:")
    print(">>>   1. Back up the real iTunesDB on the device.")
    print(">>>   2. Copy this output file over it (iPod_Control/iTunes/iTunesDB).")
    print(">>>   3. Re-run `summary` against the device's copy to confirm the")
    print(">>>      named albums no longer show as non-uniform.")
    print(">>>   4. The NEXT normal iTunes sync will regenerate iTunesDB from")
    print(">>>      iTunes's own library and undo this patch. Fix the tags at")
    print(">>>      the source (in your tag editor / iTunes) for a permanent fix.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='itunesdb_diag.py',
        description="Diagnose (and cautiously, partially fix) the iPod "
                     "Classic/Nano Cover Flow duplicate-album bug by "
                     "inspecting the device's binary iTunesDB directly.",
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_search = sub.add_parser('search', help="Dump all known fields for tracks matching an Album search string")
    p_search.add_argument('itunesdb', help="Path to the iTunesDB file")
    p_search.add_argument('query', help="Substring to search for in the Album field")
    p_search.set_defaults(func=cmd_search)

    p_summary = sub.add_parser('summary', help="Scan the whole library and list every album with a Sort-field, Compilation, or Disc mismatch")
    p_summary.add_argument('itunesdb', help="Path to the iTunesDB file")
    p_summary.set_defaults(func=cmd_summary)

    p_fix = sub.add_parser('fix', help="EXPERIMENTAL, not confirmed safe -- see README. Patches Compilation/Disc (a correlated symptom, not the confirmed cause) for named album(s), writing a new output file")
    p_fix.add_argument('itunesdb', help="Path to the iTunesDB file")
    p_fix.add_argument('output', help="Path to write the patched copy to (original is never modified)")
    p_fix.add_argument('albums', nargs='+', help="Exact Album name(s) to fix (quote each one)")
    p_fix.set_defaults(func=cmd_fix)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ITunesDBError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: file not found -- {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
