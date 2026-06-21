# itunesdb-coverflow-fix

A small, dependency-free Python tool to diagnose — and cautiously,
partially fix — the classic iPod "duplicate album in Cover Flow" bug, by
reading the device's binary `iTunesDB` file directly.

> **Disclaimer:** the code in this repository, including the test suite,
> was written with the help of an AI assistant (Claude). It's been tested
> against real `iTunesDB` files from a real iPod Classic and has an
> automated test suite (see below), but AI-assisted code can still contain
> mistakes that testing hasn't caught — especially on iPod models, iTunes
> versions, or `iTunesDB` versions not listed in the Limitations section.
> Read the code before running it against a device you care about, and
> always work from a copy of `iTunesDB`, never the original on the device.

## The bug

On an iPod Classic or Nano (older click-wheel firmware), Cover Flow can
show **one album as two or more separate cover tiles**, even when every
visible tag in iTunes — Artist, Album, Album Artist — looks completely
correct and identical across every track on that album.

This has been reported, on and off, since at least 2006, across many
now-dead forum threads, with several different partial workarounds
floating around (matching Album Artist, marking compilations, re-typing
tags by hand). Some of those threads land on a piece of the real
mechanism without fully nailing it down — this tool exists because none of
them gave a way to actually *verify*, at the byte level, whether a given
fix had taken effect.

## The confirmed root cause

The iPod's on-device track database (`iTunesDB`) stores, per track, three
**Sort fields** — `ArtistSort`, `AlbumSort`, `AlbumArtistSort` — separate
from the main Artist/Album/Album Artist fields you see in iTunes. These
correspond to documented Cover Flow sort keys: the device sorts tracks by
Sort Artist, then Artist, then Sort Album, then Album, then Track No, and
renders a new cover tile every time Album changes as you walk through that
sorted sequence.

**If even one track on an album has different Sort field values than the
rest of the album — including one track having an explicit value and
another being blank, even when every *displayed* tag looks identical —
that track sorts out of sequence and Cover Flow renders it as a separate
album.**

This is especially likely on libraries assembled from multiple sources —
e.g. some tracks tagged by one tool/pipeline, others added manually or
tagged by a different tool — where one or two tracks on an album never
went through the same tagging pass as the rest, and end up with Sort
fields that disagree with the majority even though the main tags match.

This was confirmed by directly parsing the binary `iTunesDB` per the
[wikiPodLinux iTunesDB specification](http://www.ipodlinux.org/ITunesDB/iTunesDB_File.html),
fixing one small album at a time in a real, mixed-source ~220-track
library, and re-verifying at the byte level after each attempt — rather
than trusting a tag editor's display or iTunes's own UI, both of which
turned out to be unreliable indicators of what had actually propagated to
the device.

## A false lead, documented on purpose

Earlier in this investigation, a 1-byte **Compilation flag** and a **Disc
Number / Disc Total** pair were found to reliably *correlate* with every
broken album in the test library — the same outlier tracks with
mismatched Sort fields also tended to have a mismatched Compilation flag
and/or Disc Number.

**Fixing only Compilation/Disc, with Sort fields confirmed still
mismatched, did not fix Cover Flow.** Both problems share the same root
cause (a track or two skipping the rest of the album's tagging pass), but
Compilation/Disc were a side effect, not the mechanism. This is left in
the tool's `summary` output as a hint — an album flagged for
Compilation/Disc mismatch is worth checking for Sort-field problems too —
but it is not, on its own, the fix.

This is documented explicitly so nobody else burns the same amount of time
chasing it that this project did.

## What this tool does

```
python3 itunesdb_diag.py search  <path_to_iTunesDB> "<album search string>"
python3 itunesdb_diag.py summary <path_to_iTunesDB>
python3 itunesdb_diag.py fix     <path_to_iTunesDB> <output_path> "<Album1>" ["<Album2>" ...]
```

### `search`

Dumps every field this tool understands — Title, Artist, Album, Album
Artist, all three Sort fields, Genre, Filetype, Year, Bitrate, Compilation
flag, Disc Number/Total, and the internal `dbid`/`album_id` — for every
track whose Album field contains your search string. Useful for
inspecting one specific album closely.

### `summary` — the main diagnostic step

Scans your **entire** library in one pass, groups tracks by Album, and
reports every album where `ArtistSort`, `AlbumSort`, `AlbumArtistSort`,
Compilation flag, or (Disc Number, Disc Total) is **not uniform** across
all of that album's tracks. This tells you exactly which albums are
affected and which fields are inconsistent, without checking one album at
a time.

> **Note:** `summary` can also flag albums that are *legitimately*
> multi-disc releases, where Disc Number genuinely and correctly varies.
> Always sanity-check the output — don't assume every flagged album is
> actually broken; cross-check against what Cover Flow actually shows on
> the device.

### `fix` — experimental, NOT confirmed safe on a real device

Patches **only** the album(s) you name explicitly — but only the
Compilation/Disc fields, since those are fixed-width integers that can be
overwritten in place. It deliberately does **not** patch the Sort fields
(the confirmed actual cause), because those are variable-length strings —
changing their length would shift every subsequent byte offset in the
file, which this tool doesn't attempt.

**In real-device testing, replacing a device's `iTunesDB` with output from
`fix` caused both iTunes and the iPod itself to show zero songs for that
device.** The likely cause is an integrity hash elsewhere in the file
(possibly related to FairPlay/library-pairing validation in some
`iTunesDB` versions) that this tool does not recalculate — patching
individual fields without it may cause the whole database to be treated
as untrustworthy, rather than just the bytes that were actually changed.

**Use `fix` for research/inspection only. Do not rely on it to actually
fix a real device.** The confirmed, safe, durable fix is described below.

## The actual fix

1. In your tag editor (or iTunes's own **Get Info → Sorting** tab),
   correct `ArtistSort`, `AlbumSort`, and `AlbumArtistSort` so every track
   on the affected album agrees — either all genuinely blank, or all set
   to the identical value. Either is fine; what matters is that every
   track on the album agrees with every other track.
2. Make sure the change actually reaches iTunes's own library (Get Info's
   "refresh from file" behavior was unreliable in testing on iTunes 10.7;
   removing and re-adding the affected tracks to the library was the
   reliable fallback).
3. Do a normal iTunes sync.
4. Pull the device's `iTunesDB` again and run `summary` against it to
   confirm, at the byte level, that the fix actually landed — don't trust
   a visual check in iTunes alone. This project repeatedly ran into
   changes that looked applied in the tag editor or in iTunes but had
   silently failed to propagate through to the device on the next sync.
5. Check Cover Flow on the device.

## Getting your `iTunesDB` file

It lives on the device at:

```
iPod_Control\iTunes\iTunesDB
```

(You'll need to show hidden/system files in your file manager to see
`iPod_Control` at all.) Copy it off the device to your computer before
running this tool against it, to avoid any chance of reading a
half-written file if something is mid-sync.

## Requirements

- Python 3, standard library only — no `pip install` needed.

## Running the tests

```
python3 -m unittest test_itunesdb_diag.py -v
```

`test_itunesdb_diag.py` is an automated test suite for this tool. It builds
small, fake `iTunesDB` byte buffers by hand — a few hundred bytes, a couple
of synthetic tracks, following the same binary layout as a real file —
then runs the actual parsing/diagnostic/fix functions against them and
checks the output is correct. For example: *given two tracks with matching
displayed tags but one has a blank `ArtistSort` and the other doesn't,
does `summary` correctly flag that album? Does `fix` leave an album you
didn't name completely untouched?*

This means the test suite doesn't require a real iPod or a real
`iTunesDB` file to run, which matters for a few reasons:

- **Regression safety.** If you or someone else changes the parsing logic
  later, running the tests immediately shows whether something broke,
  without needing to dig out a real device to manually re-check.
- **A credibility signal for anyone deciding whether to trust this against
  their own device.** Tests that specifically cover the edge case that
  caused the most grief during development (the "tags look identical but
  Sort fields don't" case) show that was actually validated, not just
  eyeballed once and assumed correct.
- **Catching exactly the kind of subtle-but-wrong result this project ran
  into repeatedly** — something that looks right but isn't (see the
  Compilation/Disc false lead above) is a real risk in this domain, and
  tests are a cheap way to guard the code itself against that, even though
  they can't validate a *theory* about the iPod's firmware the way testing
  against a real device can.

## Tested configuration

This tool and the fix workflow described above were developed and
verified against this specific setup:

- **iTunes:** 10.7, run as a dedicated install (Phase clickwheel game
  playlist generation and other older-iTunes-only behavior require a
  pre-iTunes-11 version; this device is kept permanently on 10.7 rather
  than switching between iTunes versions)
- **Windows:** 11, Build 26200
- **iPod:** Classic, 7th generation, 160GB stock storage upgraded to
  256GB via iFlash Solo, transparent faceplate
- **Installing iTunes 10.7 on a modern Windows 11 machine:** iTunes 10.x
  installers predate the code-signing/trust model modern Windows expects,
  which can cause Windows Defender to quarantine files mid-install. The
  method used here, including temporarily disabling real-time protection
  during install, is documented in
  [this r/LegacyJailbreak post](https://reddit.com/r/LegacyJailbreak/comments/1m184fk/how_to_install_itunes_1011_on_windows_10).

If you're on a different iPod model, iTunes version, or Windows version
and hit something this tool doesn't handle correctly, please open an
issue with your configuration and the `search` output for an affected
track.

## Contributing

If you hit this bug on a device or iTunes version not listed above, or if
you can pin down the integrity-hash mechanism that makes `fix` unsafe,
issues and PRs are welcome.

## License

MIT — see [LICENSE](LICENSE).
