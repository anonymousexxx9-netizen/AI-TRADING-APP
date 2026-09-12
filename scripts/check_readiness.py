"""Read-only deployment checks. Never print credential values."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
server = {**dotenv_values(root / 'backend' / '.env'), **os.environ}
mobile = {**dotenv_values(root / 'mobile' / '.env'), **os.environ}

def available(config, name):
    return bool((config.get(name) or '').strip())

checks = {
    'backend_env_file': (root / 'backend' / '.env').exists(),
    'private_access_token': len(server.get('APP_ACCESS_TOKEN') or '') >= 32,
    'ai_key_configured': available(server, 'AI_API_KEY') or available(server, 'GROQ_API_KEY'),
    'text_model_configured': available(server, 'AI_TEXT_MODEL'),
    'vision_model_configured': available(server, 'AI_VISION_MODEL'),
    'ai_endpoint_configured': urlparse(server.get('AI_BASE_URL') or '').scheme in ('http', 'https'),
    'expo_project_id_configured': available(mobile, 'EXPO_PUBLIC_EAS_PROJECT_ID'),
    'brand_logo_available': (root / 'mobile' / 'assets' / 'bayproject-logo.jpeg').is_file(),
}
print(json.dumps({'checks': checks,
    'note': 'Pemeriksaan konfigurasi saja; belum menguji koneksi AI, server HTTPS, signing, atau push HP.'}, indent=2))
