import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View, ViewStyle } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Line, Rect, Text as SvgText } from 'react-native-svg';
import { C } from './theme';
import { AIResponse } from './AIResponse';
export { C } from './theme';

export const s = StyleSheet.create({
  page: { padding: 22, gap: 20, paddingBottom: 34 },
  card: { backgroundColor: C.card, borderWidth: 1, borderColor: C.line, borderRadius: 22, padding: 18, gap: 12 },
  title: { color: C.text, fontSize: 29, fontWeight: '700', letterSpacing: -1 },
  heading: { color: C.text, fontSize: 18, fontWeight: '600', letterSpacing: -.3 },
  text: { color: C.text, fontSize: 14, lineHeight: 22 },
  muted: { color: C.muted, fontSize: 13, lineHeight: 20 },
  label: { color: C.muted, fontSize: 11, fontWeight: '600', letterSpacing: 1.5, textTransform: 'uppercase' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12 },
  input: { backgroundColor: C.bg, borderWidth: 1, borderColor: C.line, color: C.text, borderRadius: 14, padding: 14, fontSize: 15, minHeight: 50 },
  button: { backgroundColor: C.gold, borderRadius: 14, paddingHorizontal: 18, minHeight: 48, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 8 },
  buttonText: { color: C.bg, fontWeight: '700', fontSize: 14 },
  chip: { paddingHorizontal: 14, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: C.line, backgroundColor: C.card },
  divider: { height: 1, backgroundColor: C.line },
});

export function Icon({ name, color = C.muted, size = 21 }: { name: keyof typeof Ionicons.glyphMap; color?: string; size?: number }) {
  return <Ionicons name={name} color={color} size={size} />;
}
export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) { return <View style={[s.card, style]}>{children}</View>; }
export function Button({ title, onPress, secondary, disabled, icon }: { title: string; onPress: () => void; secondary?: boolean; disabled?: boolean; icon?: keyof typeof Ionicons.glyphMap }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={title} disabled={disabled} onPress={onPress} style={({ pressed }) => [s.button, secondary && { backgroundColor: C.raised }, { opacity: disabled ? .4 : pressed ? .7 : 1 }]}>
    {icon && <Icon name={icon} size={18} color={secondary ? C.text : C.bg} />}<Text style={[s.buttonText, secondary && { color: C.text }]}>{title}</Text>
  </Pressable>;
}
export function Field({ label, value, onChangeText, secure, numeric, placeholder, multiline }: { label: string; value: string; onChangeText: (s: string) => void; secure?: boolean; numeric?: boolean; placeholder?: string; multiline?: boolean }) {
  return <View style={{ gap: 8 }}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label} value={value} onChangeText={onChangeText} secureTextEntry={secure} autoCapitalize="none" autoCorrect={false} keyboardType={numeric ? 'decimal-pad' : 'default'} placeholder={placeholder} placeholderTextColor={C.muted} multiline={multiline} style={[s.input, multiline && { minHeight: 92, textAlignVertical: 'top' }]} /></View>;
}
export function Chips({ values, value, onChange }: { values: string[]; value: string; onChange: (s: string) => void }) {
  return <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>{values.map(v => <Pressable accessibilityRole="button" accessibilityState={{ selected: v === value }} key={v} onPress={() => onChange(v)} style={[s.chip, v === value && { backgroundColor: C.gold, borderColor: C.gold }]}><Text style={{ color: v === value ? C.bg : C.muted, fontSize: 12, fontWeight: '600' }}>{v}</Text></Pressable>)}</View>;
}
export function Empty({ title, detail, icon = 'layers-outline' }: { title: string; detail: string; icon?: keyof typeof Ionicons.glyphMap }) {
  return <View style={{ alignItems: 'center', paddingVertical: 28, gap: 10 }}><Icon name={icon} size={32} color={C.gold} /><Text style={s.heading}>{title}</Text><Text style={[s.muted, { textAlign: 'center', maxWidth: 310 }]}>{detail}</Text></View>;
}
export function Busy({ label = 'Mengambil data…' }: { label?: string }) { return <View style={[s.row, { padding: 14 }]}><ActivityIndicator color={C.gold} /><Text style={s.muted}>{label}</Text></View>; }

const labels: Record<string, string> = { confidence: 'Technical score', bias_direction: 'Bias', sr: 'Support & resistance', indicators: 'Indikator', regime: 'Regime pasar', pattern: 'Candlestick pattern', structure: 'Market structure', should_alert: 'Setup memenuhi kriteria', lot_size: 'Ukuran lot', risk_amount: 'Risiko nominal', win_rate: 'Win rate (%)', total_signals: 'Jumlah sinyal', fetched_at: 'Diambil pada', per_tf: 'Per timeframe', trap: 'Deteksi trap', candles_used: 'Candle diuji' };
function title(key: string) { return labels[key] || key.replaceAll('_', ' '); }
function scalar(value: any) { return value === null ? '—' : typeof value === 'boolean' ? value ? 'Ya' : 'Tidak' : String(value); }

export function Result({ data, depth = 0 }: { data: any; depth?: number }) {
  const [expanded, setExpanded] = useState(false);
  if (data === null || data === undefined) return null;
  if (typeof data !== 'object') return <AIResponse text={scalar(data)} />;
  if (Array.isArray(data)) {
    if (!data.length) return <Text style={s.muted}>Belum ada data.</Text>;
    const shown = expanded ? data : data.slice(0, 6);
    return <View style={{ gap: 12 }}>{shown.map((v, i) => <View key={i} style={{ gap: 6, borderLeftWidth: typeof v === 'object' ? 2 : 0, borderColor: C.line, paddingLeft: 10 }}><Result data={v} depth={depth + 1} /></View>)}{data.length > 6 && <Button secondary title={expanded ? 'Ringkas' : `Lihat ${data.length} item`} onPress={() => setExpanded(!expanded)} />}</View>;
  }
  return <View style={{ gap: 12 }}>{Object.entries(data).filter(([key]) => !['candles', 'image'].includes(key)).map(([key, value]) => {
    const object = value !== null && typeof value === 'object';
    if (key === 'text') return <AIResponse key={key} text={String(value)} />;
    return <View key={key} style={{ gap: 7 }}>{object ? <><Text style={[s.label, { color: depth ? C.muted : C.silver }]}>{title(key)}</Text><Result data={value} depth={depth + 1} /></> : <View style={s.between}><Text style={[s.muted, { flex: 1, textTransform: 'capitalize' }]}>{title(key)}</Text><Text selectable style={[s.text, { flex: 1, textAlign: 'right', color: key === 'error' ? C.red : C.text }]}>{scalar(value)}</Text></View>}</View>;
  })}</View>;
}

export function CandleChart({ candles }: { candles: any[] }) {
  const rows = candles.filter(c => ['open', 'high', 'low', 'close'].every(k => Number.isFinite(Number(c[k])))).slice(-36);
  if (!rows.length) return <Empty title="Chart belum tersedia" detail="Ambil analisis untuk menampilkan candle dari sumber pasar." />;
  const min = Math.min(...rows.map(c => Number(c.low))), max = Math.max(...rows.map(c => Number(c.high)));
  const span = max - min || 1, y = (v: number) => 16 + (max - v) / span * 156;
  const width = 282 / rows.length;
  return <View style={{ gap: 8 }}><Svg viewBox="0 0 350 195" width="100%" height={220} accessibilityLabel="Chart candlestick dari data pasar">
    {[0, 1, 2, 3].map(i => { const price = max - span * i / 3; return <React.Fragment key={i}><Line x1={0} x2={288} y1={y(price)} y2={y(price)} stroke={C.line} strokeDasharray="3 5" /><SvgText x={292} y={y(price) + 4} fill={C.muted} fontSize={9}>{price.toFixed(price < 10 ? 4 : 2)}</SvgText></React.Fragment>; })}
    {rows.map((c, i) => { const color = +c.close >= +c.open ? C.gold : C.red, x = 4 + i * width; return <React.Fragment key={i}><Line x1={x + width / 2} x2={x + width / 2} y1={y(+c.high)} y2={y(+c.low)} stroke={color} /><Rect x={x + 1} y={Math.min(y(+c.open), y(+c.close))} width={Math.max(2, width - 3)} height={Math.max(1, Math.abs(y(+c.open) - y(+c.close)))} fill={color} /></React.Fragment>; })}
  </Svg><Text style={s.muted}>Candle terakhir: {String(rows[rows.length - 1].datetime || '—')}</Text></View>;
}
