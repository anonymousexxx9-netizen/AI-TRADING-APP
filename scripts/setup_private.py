"""Initialize local backend configuration without overwriting existing credentials."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / 'backend' / '.env'
template = root / 'backend' / '.env.example'
if target.exists():
    print('backend/.env sudah ada; tidak diubah.')
else:
    content = template.read_text(encoding='utf-8')
    content = content.replace('APP_ACCESS_TOKEN=\n', 'APP_ACCESS_TOKEN=' + secrets.token_urlsafe(32) + '\n', 1)
    # Exclusive creation prevents accidental overwrites if setup runs concurrently.
    with target.open('x', encoding='utf-8') as file:
        file.write(content)
    print('backend/.env dibuat dengan kunci akses acak. Kunci tidak dicetak.')
(root / 'backend' / 'data').mkdir(exist_ok=True)
print('Berikutnya: isi AI_API_KEY dan model yang tersedia di backend/.env atau Secrets hosting.')
