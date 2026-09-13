# FResucitary Pro 2.1.3

Logiciel Windows x64 de récupération de données, conçu pour fonctionner **100 % localement** et en lecture seule sur la source.

## Utilisateur final

L'utilisateur final ne doit installer **ni Python, ni pip, ni environnement virtuel**. La distribution cible est :

- `FResucitary-Pro-Setup-2.1.3-x64.exe` — installateur Windows ;
- `FResucitary.exe` — application autonome installée dans `Program Files` ;
- élévation UAC uniquement lorsqu'un disque physique `\\.\PhysicalDriveN` doit être accédé.

Les images `.dd`, `.img`, `.raw`, `.iso`, `.vhd` et `.vhdx` peuvent être ouvertes en mode utilisateur standard.

## Sécurité

- aucune écriture volontaire sur la source ;
- avertissement renforcé sur le disque système ;
- détection de la destination située sur le même disque physique que la source ;
- SHA-256 des fichiers récupérés ;
- journal applicatif rotatif dans `%LOCALAPPDATA%\FResucitary\Logs` ;
- gestion globale des erreurs avec journal de diagnostic.

## Systèmes de fichiers et récupération

- NTFS ;
- FAT12/FAT16/FAT32 ;
- exFAT ;
- carving par signatures en modes profond/forensique ;
- partitions multiples ;
- reprise de sessions ;
- export CSV et rapport PDF.

## Construire la release sous Windows

Le poste de **développement uniquement** doit disposer d'un Python x64 3.11, 3.12 ou 3.13. Python 3.13 est supporté par la version actuelle de `pytsk3`.

Double-cliquer :

```text
BUILD_WINDOWS.bat
```

Le script :

1. sélectionne automatiquement Python x64 compatible ;
2. crée `.venv` ;
3. installe les dépendances ;
4. exécute les tests ;
5. construit `dist\FResucitary\FResucitary.exe` avec PyInstaller ;
6. construit l'installateur NSIS si NSIS 3 est installé.

Sortie attendue :

```text
dist\FResucitary-Pro-Setup-2.1.3-x64.exe
```

Le PC du client n'a besoin d'aucune installation Python.

## Build CI/CD

Le workflow `.github/workflows/ci.yml` teste le moteur puis construit automatiquement la distribution Windows et l'installateur. Une signature Authenticode peut être ajoutée lorsque le certificat de signature est configuré dans les secrets du dépôt.

## Développement

```text
RUN_DEV.bat
```

ou :

```bash
python main.py
```

Tests :

```bash
pytest -q
```

## Version

2.1.3 — validation d’intégrité et récupération NTFS par flux $DATA/data-runs, avec états Intact/Partiel/Corrompu/Échec.
