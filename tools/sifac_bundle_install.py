#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_bundle_install — get a modded SIFAS bundle accepted by the game
=====================================================================

A modded AssetBundle won't load just by dropping it in place: SIFAS uses
KLab's **Octo** asset system, whose database lists every pack with its **size**
and **md5** (and crc).  If the file on disk doesn't match the database, the
client treats it as corrupt and re-downloads the original -- your mod is gone.

So "installing" a modded bundle is two steps:

1. **Place** the new bundle where the game keeps it (named by its pack id, e.g.
   ``mvjubr_0``) -- usually under the app's files dir on Android.
2. **Patch the Octo database** row for that pack so its ``size`` / ``md5``
   (/ ``crc``) match the new file -- otherwise the integrity check fails.

This tool does the parts that don't need the game's exact schema yet:

``--info BUNDLE``
    Print the values an Octo row needs for this file: byte size, md5, crc32,
    sha256, plus the UnityFS header (unity version, internal CAB/asset names).
    These are what you write into the database.

``--scan-db DB [--find TEXT]``
    Open a SQLite database (Octo db is SQLite) and locate the row(s) for a
    pack: list tables/columns and search every text/blob column for the pack
    name or an md5/hash.  Share this output and the DB and the **patch** step is
    pinned exactly to your client's schema (table + size/md5/crc columns) and
    validated by reading the row back.

Requirements: standard library only (``sqlite3``, ``hashlib``, ``zlib``).
``UnityPy`` is optional and only used to print the UnityFS internal names.

Examples
--------
::

    python3 sifac_bundle_install.py --info mvjubr_0__0510Daring.unity
    python3 sifac_bundle_install.py --scan-db octo.db --find mvjubr_0
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import zlib
from pathlib import Path

try:
    import UnityPy
    HAVE_UNITYPY = True
except Exception:  # pragma: no cover
    HAVE_UNITYPY = False


# --------------------------------------------------------------------------- #
# Integrity values (what an Octo/manifest row stores about a pack)
# --------------------------------------------------------------------------- #

def file_info(path):
    data = Path(path).read_bytes()
    return {
        "path": str(path),
        "size": len(data),
        "md5": hashlib.md5(data).hexdigest(),
        "crc32": zlib.crc32(data) & 0xffffffff,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def unityfs_info(path):
    """Best-effort UnityFS header details (unity version + internal names)."""
    out = {}
    if not HAVE_UNITYPY:
        return out
    try:
        env = UnityPy.load(str(path))
        try:
            out["unity_version"] = next(iter(env.assets)).unity_version
        except Exception:
            pass
        # internal serialized-file / CAB names some manifests key on
        names = []
        cab = getattr(env.file, "files", None)
        if isinstance(cab, dict):
            names = list(cab.keys())
        if names:
            out["internal_files"] = names
    except Exception as e:
        out["unityfs_error"] = str(e)
    return out


def cmd_info(path):
    fi = file_info(path)
    print("file:   %s" % fi["path"])
    print("size:   %d bytes" % fi["size"])
    print("md5:    %s" % fi["md5"])
    print("crc32:  %d  (0x%08x)" % (fi["crc32"], fi["crc32"]))
    print("sha256: %s" % fi["sha256"])
    for k, v in unityfs_info(path).items():
        print("%-7s %s" % (k + ":", v))
    print("\n-> write size + md5 (and crc if present) into the pack's Octo-db row.")


# --------------------------------------------------------------------------- #
# Locate the pack row in a SQLite Octo database
# --------------------------------------------------------------------------- #

def _columns(con, table):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]


def cmd_scan_db(db_path, find=None):
    if not Path(db_path).is_file():
        print("no such file: %s" % db_path)
        return 1
    head = Path(db_path).read_bytes()[:16]
    if not head.startswith(b"SQLite format 3"):
        print("not a SQLite database (first bytes: %r).\n"
              "If the Octo db is encrypted/obfuscated, share a sample and the "
              "decrypt/parse step gets added here." % head)
        return 1
    con = sqlite3.connect(db_path)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    print("tables: %s" % tables)
    for t in tables:
        cols = _columns(con, t)
        try:
            n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except Exception:
            n = "?"
        print("  %-24s rows=%-7s cols=%s" % (t, n, cols))
    if find:
        print("\nsearching every column for %r ..." % find)
        hits = 0
        for t in tables:
            cols = _columns(con, t)
            for c in cols:
                try:
                    q = "SELECT * FROM %s WHERE CAST(%s AS TEXT) LIKE ? LIMIT 5" % (t, c)
                    rows = con.execute(q, ("%" + find + "%",)).fetchall()
                except Exception:
                    continue
                if rows:
                    hits += len(rows)
                    print("  [%s.%s] %d hit(s):" % (t, c, len(rows)))
                    for r in rows[:5]:
                        print("      ", dict(zip(cols, r)))
        if not hits:
            print("  no match -- try the pack id without the _N suffix, "
                  "or an md5 from --info.")
    con.close()
    return 0


# --------------------------------------------------------------------------- #
# Patch a located pack row (enabled once the schema is pinned from your db)
# --------------------------------------------------------------------------- #

def cmd_patch_db(db_path, bundle, table, name_col, name_val,
                 size_col=None, md5_col=None, crc_col=None, dry_run=True):
    fi = file_info(bundle)
    con = sqlite3.connect(db_path)
    sets, vals = [], []
    if size_col:
        sets.append("%s=?" % size_col); vals.append(fi["size"])
    if md5_col:
        sets.append("%s=?" % md5_col); vals.append(fi["md5"])
    if crc_col:
        sets.append("%s=?" % crc_col); vals.append(fi["crc32"])
    if not sets:
        print("nothing to set: pass --size-col / --md5-col / --crc-col")
        return 1
    where = "%s=?" % name_col
    sql = "UPDATE %s SET %s WHERE %s" % (table, ", ".join(sets), where)
    n = con.execute("SELECT COUNT(*) FROM %s WHERE %s" % (table, where),
                    (name_val,)).fetchone()[0]
    print("would update %d row(s): %s  (size=%d md5=%s)"
          % (n, sql, fi["size"], fi["md5"]))
    if dry_run:
        print("dry-run: re-run with --apply to write.")
    else:
        con.execute(sql, vals + [name_val])
        con.commit()
        print("updated %d row(s)." % n)
    con.close()
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Help install a modded SIFAS bundle: integrity values + "
                    "Octo-db (SQLite) lookup/patch.")
    ap.add_argument("--info", metavar="BUNDLE", help="print size/md5/crc/sha256 + UnityFS info")
    ap.add_argument("--scan-db", metavar="DB", help="inspect a SQLite Octo db; locate a pack row")
    ap.add_argument("--find", default=None, help="with --scan-db: pack id / md5 to search for")
    # patch (use after --scan-db tells you table + columns)
    ap.add_argument("--patch-db", metavar="DB", help="patch a pack row to match a bundle")
    ap.add_argument("--bundle", help="modded bundle whose size/md5 to write")
    ap.add_argument("--table"); ap.add_argument("--name-col"); ap.add_argument("--name")
    ap.add_argument("--size-col"); ap.add_argument("--md5-col"); ap.add_argument("--crc-col")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    args = ap.parse_args()

    if args.info:
        cmd_info(args.info); return 0
    if args.scan_db:
        return cmd_scan_db(args.scan_db, args.find)
    if args.patch_db:
        if not (args.bundle and args.table and args.name_col and args.name):
            ap.error("--patch-db needs --bundle --table --name-col --name")
        return cmd_patch_db(args.patch_db, args.bundle, args.table, args.name_col,
                            args.name, args.size_col, args.md5_col, args.crc_col,
                            dry_run=not args.apply)
    ap.error("use --info BUNDLE, --scan-db DB [--find ...], or --patch-db ...")


if __name__ == "__main__":
    sys.exit(main())
