# FResucitary Pro 2.1.3 — intégrité de récupération NTFS

## Correctifs 2.1.3

Cette version corrige le faux positif observé lors d'un test réel Windows : un PDF annoncé comme « récupéré » alors que seuls quelques octets avaient été écrits et que le fichier était illisible.

- capture du vrai flux NTFS `$DATA` au moment du scan (type, id, flags, taille) ;
- conservation du numéro de séquence MFT pour détecter une entrée réutilisée ;
- conservation complète des data-runs NTFS `(offset, adresse, longueur, flags)` ;
- reconstruction directe depuis les data-runs pour les flux NTFS non compressés/non chiffrés ;
- lecture du bon attribut `$DATA` au lieu de dépendre uniquement de `open_meta(inode)` ;
- validation post-récupération de la taille réellement écrite ;
- validation de la signature du type de fichier ;
- validation structurelle PDF via PDFium, images via Pillow et conteneurs ZIP/OOXML via `zipfile` ;
- nouveaux états de sortie : `Intact`, `Partiel`, `Corrompu`, `Échec` ;
- un fichier partiel ou corrompu n'est plus compté comme une récupération réussie ;
- les fichiers partiels/corrompus sont conservés pour analyse, les sorties vides en échec sont supprimées ;
- score de récupérabilité plafonné à 39 % lorsque l'extension connue contredit la signature actuelle ;
- persistance des informations de flux NTFS dans les sessions ;
- statistiques et rapport alignés sur les seuils de score 75/50/25 ;
- 159 tests automatisés réussis ;
- syntaxe validée pour Python 3.11.

## Base 2.1.x conservée

- application autonome Windows via PyInstaller ;
- installateur NSIS x64 ;
- élévation UAC à la demande pour `\.\PhysicalDriveN` ;
- protection contre l'écriture sur le disque physique source ;
- support NTFS/FAT/exFAT, carving, partitions multiples, sessions et rapports ;
- journaux dans `%LOCALAPPDATA%\FResucitary\Logs`.

## Validation encore requise sur matériel réel

Le moteur doit maintenant être retesté sur le même `PhysicalDrive1`. Pour les fichiers dont les clusters ont réellement été écrasés, FResucitary doit les classer `Corrompu` ou `Partiel` au lieu d'annoncer un succès. Pour les fichiers encore présents dans leurs data-runs, la reconstruction directe augmente les chances d'obtenir le contenu intégral.
