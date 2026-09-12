import React, { useMemo, useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';
import { parseResponse } from './responseBlocks';

function Inline({ text }: { text: string }) {
  // Render text nodes only, never model-supplied HTML or embedded remote images.
  const tokens = text.split(/(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\n]+\*|_[^_\n]+_|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g);
  return <>{tokens.map((token, i) => {
    if (/^(\*\*|__)/.test(token)) return <Text key={i} style={styles.bold}>{token.slice(2, -2)}</Text>;
    if (token.startsWith('`')) return <Text key={i} style={styles.code}>{token.slice(1, -1)}</Text>;
    if (/^(\*|_)/.test(token) && token.length > 2) return <Text key={i} style={{ fontStyle: 'italic' }}>{token.slice(1, -1)}</Text>;
    const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (link) return <Text accessibilityRole="link" accessibilityLabel={link[1]} key={i} style={styles.link} onPress={() => { void Linking.openURL(link[2]).catch(() => {}); }}>{link[1]}</Text>;
    return <Text key={i}>{token}</Text>;
  })}</>;
}

function TableCards({ headers, rows }: { headers: string[]; rows: string[][] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? rows : rows.slice(0, 3);
  return <View style={{ gap: 12 }}>
    <Text style={styles.caption}>{headers[0] || 'Rincian'} · {rows.length} item</Text>
    {visible.map((row, index) => <View key={index} style={styles.dataCard}>
      <Text selectable style={styles.cardTitle}><Inline text={row[0] || '—'} /></Text>
      <View style={styles.fields}>{row.slice(1).map((value, col) => <View key={col} style={[styles.field, value.length > 65 && { flexBasis: '100%' }]}>
        <Text style={styles.caption}>{headers[col + 1] || `Rincian ${col + 2}`}</Text>
        <Text selectable style={styles.body}><Inline text={value || '—'} /></Text>
      </View>)}</View>
    </View>)}
    {rows.length > 3 && <Pressable accessibilityRole="button" accessibilityState={{ expanded }} onPress={() => setExpanded(!expanded)} style={styles.expand}>
      <Text style={styles.expandText}>{expanded ? 'Ringkas rincian' : `Lihat ${rows.length - 3} item lainnya`}</Text>
    </Pressable>}
  </View>;
}

export function AIResponse({ text }: { text: string }) {
  const blocks = useMemo(() => parseResponse(text), [text]);
  return <View style={styles.response}>{blocks.map((block, i) => {
    switch (block.kind) {
      case 'heading': return <Text key={i} selectable accessibilityRole="header" style={[styles.heading, block.level <= 2 && styles.title, i > 0 && { marginTop: 12 }]}><Inline text={block.text} /></Text>;
      case 'rule': return <View key={i} style={styles.rule} />;
      case 'table': return <TableCards key={`${i}:${text}`} headers={block.headers} rows={block.rows} />;
      case 'list': return <View key={i} style={[styles.list, { marginLeft: block.indent * 12 }]}><Text style={styles.bullet}>{block.marker}</Text><Text selectable style={[styles.body, { flex: 1 }]}><Inline text={block.text} /></Text></View>;
      case 'quote': return <View key={i} style={styles.quote}><Text selectable style={styles.body}><Inline text={block.text} /></Text></View>;
      case 'code': return <Text key={i} selectable style={[styles.body, styles.codeBlock]}>{block.text}</Text>;
      default: return <Text key={i} selectable style={styles.body}><Inline text={block.text} /></Text>;
    }
  })}</View>;
}

const styles = StyleSheet.create({
  response: { gap: 14, width: '100%' },
  body: { color: C.text, fontSize: 15, lineHeight: 25, flexShrink: 1 },
  bold: { fontWeight: '700', color: '#fff6dc' },
  heading: { color: C.gold, fontSize: 17, fontWeight: '700', lineHeight: 25 },
  title: { color: C.text, fontSize: 21, lineHeight: 30, letterSpacing: -.3 },
  rule: { height: 1, backgroundColor: C.line, marginVertical: 4 },
  list: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  bullet: { color: C.gold, fontSize: 15, lineHeight: 25, minWidth: 14 },
  caption: { color: C.muted, fontSize: 12, lineHeight: 18 },
  dataCard: { backgroundColor: '#0c0c0b', borderColor: C.line, borderWidth: 1, borderRadius: 14, padding: 14, gap: 12 },
  cardTitle: { color: C.gold, fontSize: 16, lineHeight: 24, fontWeight: '700', borderBottomWidth: 1, borderColor: C.line, paddingBottom: 10 },
  fields: { flexDirection: 'row', flexWrap: 'wrap', gap: 14 },
  field: { gap: 3, flexBasis: 90, flexGrow: 1, minWidth: 75 },
  expand: { minHeight: 44, padding: 12, alignItems: 'center', borderRadius: 12, backgroundColor: C.raised },
  expandText: { color: C.gold, fontSize: 13, fontWeight: '600' },
  quote: { borderLeftWidth: 3, borderColor: C.gold, paddingLeft: 12 },
  code: { fontFamily: 'monospace', color: C.gold },
  codeBlock: { fontFamily: 'monospace', backgroundColor: C.bg, padding: 12, borderRadius: 10 },
  link: { color: C.gold, textDecorationLine: 'underline' },
});
