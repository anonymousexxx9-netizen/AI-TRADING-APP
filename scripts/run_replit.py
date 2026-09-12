"""Start the backend from a consistent directory in the Replit workspace."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
backend = root / 'backend'
os.chdir(backend)
sys.path.insert(0, str(backend))
from dotenv import load_dotenv
load_dotenv(backend / '.env')
if len(os.getenv('APP_ACCESS_TOKEN', '')) < 32:
    raise SystemExit('Tambahkan APP_ACCESS_TOKEN minimal 32 karakter di Replit Secrets.')
if '--check' in sys.argv:
    import api
    print('Backend dapat diimpor; kunci akses dikonfigurasi. Tidak menguji provider atau persistence deployment.')
else:
    import uvicorn
    uvicorn.run('api:app', host='0.0.0.0', port=8000, workers=1)
