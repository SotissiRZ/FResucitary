r"""
FResucitary — Diagnostic script.
Run as Administrator to diagnose why scan returns 0 results.

Usage:
    python debug_scan.py \\.\PhysicalDrive2
    python debug_scan.py D:\image.dd

Output: paste the result in the conversation for analysis.
"""
import sys
import os

print("=" * 60)
print("FResucitary — Diagnostic NTFS")
print("=" * 60)

# Check pytsk3
try:
    import pytsk3
    print(f"[OK] pytsk3 importé")
except ImportError as e:
    print(f"[ERREUR] pytsk3 manquant : {e}")
    sys.exit(1)

# Check source
if len(sys.argv) < 2:
    print("\nUsage: python debug_scan.py " + r"\\.\PhysicalDrive2")
    print("       python debug_scan.py C:\\image.dd")
    sys.exit(1)

source = sys.argv[1]
print(f"\nSource : {source}")

# Open image
try:
    img = pytsk3.Img_Info(url=source)
    print(f"[OK] Source ouverte")
except Exception as e:
    print(f"[ERREUR] Impossible d'ouvrir : {e}")
    print("        → Lancez en tant qu'Administrateur")
    sys.exit(1)

# Try volume system
print("\n--- Partitions ---")
partitions = []
try:
    vol = pytsk3.Volume_Info(img)
    for part in vol:
        print(f"  Part {part.start} len={part.len} flags={part.flags} desc={getattr(part, 'desc', '?')}")
        if part.flags == pytsk3.TSK_VS_PART_FLAG_ALLOC:
            offset = part.start * 512
            partitions.append(offset)
except Exception as e:
    print(f"  Pas de table de partitions ({e}), essai offset=0")
    partitions = [0]

# Probe each partition
print("\n--- Analyse NTFS par partition ---")
for offset in partitions:
    print(f"\n  Offset : {offset} ({offset // (1024**3):.1f} GB)")
    try:
        fs = pytsk3.FS_Info(img, offset=offset)
        fs_type = str(fs.info.ftype) if fs.info.ftype else "?"
        block_size = fs.info.block_size
        print(f"  [OK] FS ouvert : type={fs_type} block={block_size}")
    except Exception as e:
        print(f"  [ERREUR] FS : {e}")
        continue

    # Walk root (inode 5)
    print(f"  Scan root (inode=5)...")
    try:
        root = fs.open_dir(inode=5)
        total = dirs = meta_del = name_del = either_del = 0
        examples = []
        for entry in root:
            total += 1
            try:
                meta = entry.info.meta
                ni   = entry.info.name
                if meta is None:
                    continue

                # Name
                raw_name = b""
                if ni and ni.name:
                    raw_name = ni.name
                try:
                    name_str = raw_name.decode("utf-8", errors="replace").rstrip("\x00")
                except Exception:
                    name_str = str(raw_name)

                if name_str in (".", ".."):
                    continue

                # Type
                try:
                    is_dir = (meta.type == pytsk3.TSK_FS_META_TYPE_DIR)
                except Exception:
                    is_dir = False
                if is_dir:
                    dirs += 1

                # Flags
                meta_flag = int(meta.flags)
                name_flag = int(ni.flags) if ni else 0

                mm = bool(meta_flag & pytsk3.TSK_FS_META_FLAG_UNALLOC)
                nm = bool(name_flag & pytsk3.TSK_FS_NAME_FLAG_UNALLOC)

                if mm:
                    meta_del += 1
                if nm:
                    name_del += 1
                if mm or nm:
                    either_del += 1
                    if len(examples) < 5:
                        examples.append({
                            "name": name_str,
                            "meta_del": mm,
                            "name_del": nm,
                            "is_dir": is_dir,
                            "size": meta.size,
                            "inode": meta.addr,
                        })
            except Exception as ex:
                pass  # skip bad entries

        print(f"    Entries root total : {total}")
        print(f"    Dossiers           : {dirs}")
        print(f"    Supprimés (meta)   : {meta_del}")
        print(f"    Supprimés (name)   : {name_del}")
        print(f"    Supprimés (l'un ou l'autre) : {either_del}")

        if examples:
            print(f"\n    Exemples fichiers supprimés :")
            for ex in examples:
                print(f"      name={ex['name']!r} size={ex['size']} "
                      f"meta_del={ex['meta_del']} name_del={ex['name_del']} "
                      f"is_dir={ex['is_dir']} inode={ex['inode']}")
        else:
            print(f"\n    ⚠  Aucun fichier supprimé trouvé dans la racine.")
            print(f"       Le scan de FResucitary (mode rapide) cherche dans la racine.")
            print(f"       → Essayez le mode Profond ou Forensique.")

    except Exception as e:
        print(f"  [ERREUR] Root walk : {e}")
        continue

    # Check orphan dir (inode 3)
    print(f"\n  Orphan dir (inode=3)...")
    try:
        orphan = fs.open_dir(inode=3)
        n = 0
        for e in orphan:
            ni = e.info.name
            if ni and ni.name and ni.name not in (b".", b".."):
                n += 1
        print(f"    Orphelins : {n} fichier(s)")
    except Exception as e:
        print(f"    inode=3 inaccessible : {e}")

    # Check recycle bin
    print(f"\n  Recherche $Recycle.Bin...")
    try:
        root2 = fs.open_dir(inode=5)
        found_recycle = False
        for entry in root2:
            ni = entry.info.name
            if ni and ni.name:
                n = ni.name.decode("utf-8", errors="replace").rstrip("\x00")
                if "$Recycle" in n or "RECYCLER" in n.upper():
                    found_recycle = True
                    try:
                        rdir = fs.open_dir(inode=entry.info.meta.addr)
                        cnt = sum(1 for _ in rdir)
                        print(f"    Trouvé : {n} — {cnt} entrée(s)")
                    except Exception as re_err:
                        print(f"    Trouvé : {n} (erreur ouverture : {re_err})")
        if not found_recycle:
            print(f"    Pas de $Recycle.Bin trouvé")
    except Exception as e:
        print(f"    Erreur : {e}")

print("\n" + "=" * 60)
print("Fin du diagnostic. Copiez ce résultat dans la conversation.")
print("=" * 60)
