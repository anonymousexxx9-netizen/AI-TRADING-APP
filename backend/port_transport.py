"""One-time source migration helper, retained to document the upstream adaptation."""
from pathlib import Path
import re

path = Path(__file__).with_name('core.py')
source = path.read_text(encoding='utf-8')
pattern = r'requests\.post\(\s*"https://api\.groq\.com/openai/v1/chat/completions",\s*headers=hdrs, json=(payload2?), timeout=(\d+),?\s*\)'
source, count = re.subn(pattern, r'chat_completion(\1, timeout=\2)', source)
if count:
    assert count == 4, f'Expected four completion calls, found {count}'
    path.write_text(source, encoding='utf-8')
print(f'Migrated {count} transport calls')
