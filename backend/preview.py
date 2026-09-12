"""Local browser preview of the mobile UI. Not a production entry point."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
data = root / '.local-preview'
data.mkdir(exist_ok=True)
os.environ['APP_ACCESS_TOKEN'] = secrets.token_urlsafe(32)
os.environ['DB_PATH'] = str(data / 'preview.db')
os.environ['ENABLE_WORKER'] = 'false'
(data / 'access.txt').write_text(os.environ['APP_ACCESS_TOKEN'], encoding='utf-8')

from fastapi.staticfiles import StaticFiles
from api import app
import uvicorn

app.mount('/', StaticFiles(directory=str(root / 'mobile' / 'dist'), html=True), name='preview')
print('Local preview: http://127.0.0.1:8000; access key in .local-preview/access.txt')
uvicorn.run(app, host='127.0.0.1', port=8000)
