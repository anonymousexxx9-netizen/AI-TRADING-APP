import test from 'node:test';
import assert from 'node:assert/strict';
import { parseResponse } from './responseBlocks.ts';

test('headings, emphasis and nested lists preserve source content', () => {
  const result = parseResponse('**Ringkasan**\n\n### Data penting\n- **Actual:** 0.4%\n  - Perubahan: +0.2 pp');
  assert.equal(result[0].kind, 'heading');
  assert.equal(result[1].text, 'Data penting');
  assert.equal(result[2].text, '**Actual:** 0.4%');
  assert.equal(result[3].indent, 1);
});
test('wide tables retain every header and row as card fields', () => {
  const result = parseResponse('| Indikator | Forecast | Actual | Implikasi |\n|---|---:|:---:|---|\n| **A** | 1 | 2 | Tidak berubah |\n| B | — | 3 | Teks panjang |');
  assert.deepEqual(result[0].headers, ['Indikator', 'Forecast', 'Actual', 'Implikasi']);
  assert.equal(result[0].rows.length, 2);
  assert.deepEqual(result[0].rows[0], ['**A**', '1', '2', 'Tidak berubah']);
});
test('escaped pipes stay in a cell', () => {
  const [table] = parseResponse('| Nama | Nilai |\n| --- | --- |\n| A \\| B | 10 |');
  assert.equal(table.rows[0][0], 'A | B');
});
test('code is not interpreted as headings or list items', () => {
  const [block] = parseResponse('```text\n# literal\n- literal\n```');
  assert.equal(block.kind, 'code');
  assert.equal(block.text, '# literal\n- literal');
});
test('plain text and incomplete trailing list retain content', () => {
  const result = parseResponse('Jawaban biasa.\n\n- **Equities:**');
  assert.equal(result[0].text, 'Jawaban biasa.');
  assert.equal(result[1].text, '**Equities:**');
});
