"""Import the supplied brand artwork unchanged; Expo generates native icon sizes."""
from pathlib import Path
import shutil
import sys
out = Path(__file__).resolve().parents[1] / 'mobile' / 'assets' / 'bayproject-logo.jpeg'
if len(sys.argv) != 2:
    raise SystemExit('Usage: python scripts/create_icon.py path/to/original-logo.jpeg')
shutil.copyfile(sys.argv[1], out)
print('Original logo imported without image edits.')
