"""Package only source/config examples. Never include credentials or user databases."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
out = root / 'deliverables'
out.mkdir(exist_ok=True)
files = [root / name for name in ['README.md', 'REPLIT.md', 'VALIDATION.md', '.gitignore', '.replit', 'requirements.txt', 'compose.yaml']]
files += [p for p in (root / 'backend').glob('*') if p.is_file() and
          (p.suffix == '.py' or p.name.startswith('requirements') or p.name in ['Dockerfile', '.dockerignore', '.env.example'])]
files += list((root / 'backend' / 'tests').glob('*.py'))
files += [root / 'mobile' / name for name in ['App.tsx', 'index.ts', 'tsconfig.json', 'package.json', 'package-lock.json', 'app.config.ts', 'eas.json', '.env.example']]
files += list((root / 'mobile' / 'src').glob('*'))
files += [p for p in (root / 'mobile' / 'assets').iterdir() if p.suffix in ('.png', '.jpeg', '.jpg')]
files += list((root / 'scripts').glob('*.py'))
with ZipFile(out / 'bayproject-mobile-source.zip', 'w', ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.relative_to(root))
print(f'Packaged {len(files)} source files')
