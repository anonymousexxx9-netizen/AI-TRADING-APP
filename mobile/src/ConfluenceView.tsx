import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';

type Timeframe = { interval: string; bias?: string; confidence?: number; regime?: string; weight?: number; error?: string };
type Confluence = { symbol?: string; per_tf: Timeframe[]; overall?: string; error?: string; total_tf?: number; weighted_bullish?: number; weighted_bearish?: number; total_weight?: number; htf_conflict?: boolean };
const labels: Record<string, string> = { '1day': 'D1 · Harian', '4h': 'H4 · 4 jam', '1h': 'H1 · 1 jam', '15min': 'M15 · 15 menit' };
const order = ['1day', '4h', '1h', '15min'];
const color = (bias?: string) => bias === 'Bullish' ? C.gold : bias === 'Bearish' ? C.red : C.silver;

export function ConfluenceView({ data }: { data: Confluence }) {
  const [details, setDetails] = useState(false);
  const rows = [...data.per_tf].sort((a, b) => order.indexOf(a.interval) - order.indexOf(b.interval));
  const valid = rows.filter(row => !row.error).length;
  return <View style={{ gap: 18 }}>
    <View style={styles.between}><Text style={styles.title}>{data.symbol || 'Confluence'}</Text><Text style={styles.muted}>{valid}/4 TF tersedia</Text></View>
    <View style={[styles.summary, data.htf_conflict && { borderColor: C.red }]}>
      <Text style={styles.label}>KESIMPULAN CONFLUENCE</Text>
      <Text style={[styles.body, { color: data.htf_conflict || data.error ? C.red : C.text }]}>{data.error || data.overall || 'Kesimpulan belum tersedia.'}</Text>
      {valid < 4 && <Text style={styles.muted}>Data belum lengkap. Periksa timeframe yang gagal dimuat.</Text>}
    </View>
    <Text style={styles.muted}>D1 & H4 menentukan arah utama. H1 & M15 memberi konteks setup dan eksekusi.</Text>
    <View style={styles.grid}>{rows.map(row => <View key={row.interval} style={styles.tile}>
      <Text style={styles.tf}>{labels[row.interval] || row.interval}</Text>
      <Text style={[styles.bias, { color: row.error ? C.muted : color(row.bias) }]}>{row.error ? 'Tidak tersedia' : row.bias || 'Netral'}</Text>
      {row.error ? <Text style={styles.muted}>{row.error}</Text> : <>
        <Text style={styles.score}>{row.confidence ?? '—'}<Text style={styles.muted}> /100</Text></Text>
        <Text style={styles.label}>SKOR TEKNIKAL</Text>
        <Text style={styles.regime}>{row.regime || 'Regime belum tersedia'}</Text>
      </>}
    </View>)}</View>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: details }} onPress={() => setDetails(!details)} style={styles.toggle}><Text style={{ color: C.gold, fontWeight: '600' }}>{details ? 'Tutup detail bobot −' : 'Lihat detail bobot +'}</Text></Pressable>
    {details && <View style={styles.summary}>
      <View style={styles.between}><Text style={styles.muted}>Bobot bullish</Text><Text style={[styles.body, { color: C.gold }]}>{data.weighted_bullish ?? '—'}</Text></View>
      <View style={styles.between}><Text style={styles.muted}>Bobot bearish</Text><Text style={[styles.body, { color: C.red }]}>{data.weighted_bearish ?? '—'}</Text></View>
      <Text style={styles.muted}>Total bobot arah: {data.total_weight ?? '—'}. Timeframe netral tidak masuk total ini.</Text>
      {rows.map(row => <View key={row.interval} style={styles.between}><Text style={styles.muted}>{labels[row.interval] || row.interval}</Text><Text style={styles.body}>{row.error ? 'Gagal dimuat' : `Bobot ${row.weight ?? '—'}`}</Text></View>)}
      <Text style={styles.muted}>Skor teknikal bukan probabilitas profit. Bobot menunjukkan kontribusi timeframe pada kesimpulan.</Text>
    </View>}
  </View>;
}

const styles = StyleSheet.create({
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  title: { color: C.text, fontSize: 20, fontWeight: '700' }, body: { color: C.text, fontSize: 14, lineHeight: 22 }, muted: { color: C.muted, fontSize: 12, lineHeight: 19 },
  summary: { backgroundColor: C.bg, borderWidth: 1, borderColor: C.line, borderRadius: 14, padding: 14, gap: 10 },
  label: { color: C.muted, fontSize: 9, letterSpacing: .7 }, grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  tile: { flexBasis: '46%', flexGrow: 1, minWidth: 110, backgroundColor: C.bg, borderWidth: 1, borderColor: C.line, borderRadius: 16, padding: 13, gap: 9 },
  tf: { color: C.silver, fontSize: 12, fontWeight: '600' }, bias: { fontSize: 17, fontWeight: '700' }, score: { color: C.text, fontSize: 26, fontWeight: '700' },
  regime: { color: C.muted, fontSize: 12, lineHeight: 19, borderTopWidth: 1, borderColor: C.line, paddingTop: 10 },
  toggle: { minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 12, backgroundColor: C.raised },
});
