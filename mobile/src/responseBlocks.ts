// Presentation only: keep all numbers and wording from the original response.
export type Block =
  | { kind: 'heading'; text: string; level: number }
  | { kind: 'paragraph' | 'quote' | 'code'; text: string }
  | { kind: 'list'; text: string; marker: string; indent: number }
  | { kind: 'rule' }
  | { kind: 'table'; headers: string[]; rows: string[][] };

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map(s => s.trim().replace(/\\\|/g, '|'));
}
function isDivider(line: string): boolean {
  const values = cells(line);
  return values.length > 1 && values.every(s => /^:?-{3,}:?$/.test(s));
}
function listMatch(line: string) { return line.match(/^(\s*)([-+*•]|\d+[.)])\s+(.+)$/); }
function isSpecial(lines: string[], i: number): boolean {
  const l = lines[i];
  return !l.trim() || /^(#{1,6}\s|```|>\s?|\s*(?:---+|\*\*\*+|___+)\s*$)/.test(l)
    || !!listMatch(l) || (i + 1 < lines.length && l.includes('|') && isDivider(lines[i + 1]));
}

export function parseResponse(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^```/.test(line)) {
      const content: string[] = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) content.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push({ kind: 'code', text: content.join('\n') }); continue;
    }
    if (i + 1 < lines.length && line.includes('|') && isDivider(lines[i + 1])) {
      const headers = cells(line), rows: string[][] = []; i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push({ kind: 'table', headers, rows }); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { blocks.push({ kind: 'heading', text: heading[2], level: heading[1].length }); i++; continue; }
    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) { blocks.push({ kind: 'rule' }); i++; continue; }
    const list = listMatch(line);
    if (list) {
      let content = list[3]; i++;
      // Wrapped prose remains with the list item; nested bullets get their own row.
      while (i < lines.length && /^\s+\S/.test(lines[i]) && !isSpecial(lines, i)) content += ' ' + lines[i++].trim();
      blocks.push({ kind: 'list', text: content, marker: /^\d/.test(list[2]) ? list[2] : '•', indent: Math.min(2, Math.floor(list[1].length / 2)) }); continue;
    }
    if (/^>/.test(line)) { blocks.push({ kind: 'quote', text: line.replace(/^>\s?/, '') }); i++; continue; }
    const content = [line.trim()]; i++;
    while (i < lines.length && !isSpecial(lines, i)) content.push(lines[i++].trim());
    const paragraph = content.join('\n');
    // AI often emits a standalone bold title instead of a Markdown heading.
    if (/^\*\*[^\n]+\*\*$/.test(paragraph)) blocks.push({ kind: 'heading', text: paragraph.slice(2, -2), level: blocks.length ? 3 : 1 });
    else blocks.push({ kind: 'paragraph', text: paragraph });
  }
  return blocks;
}
