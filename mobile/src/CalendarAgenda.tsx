import React, { useMemo, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';

type Event = { title: string; date: string; time: string; impact: string; actual?: string; forecast?: string; previous?: string };

function dateKey(value: string) {
  const us = value.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  return us ? `${us[3]}-${us[1]}-${us[2]}` : value;
}
function dateLabel(value: string) {
  const key = dateKey(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return { day: value, date: '' };
  const parsed = new Date(key + 'T12:00:00Z');
  return { day: parsed.toLocaleDateString('id-ID', { weekday: 'short', timeZone: 'UTC' }), date: parsed.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', timeZone: 'UTC' }) };
}
function value(text?: string) { return !text || ['n/a', 'null', 'none'].includes(text.trim().toLowerCase()) ? '—' : text; }

export function CalendarAgenda({ events, onAnalyze }: { events: Event[]; onAnalyze: (name: string) => void }) {
  const days = useMemo(() => [...new Set(events.map(e => e.date))].sort((a, b) => dateKey(a).localeCompare(dateKey(b))), [events]);
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  const initial = days.find(d => dateKey(d) >= today) || days[days.length - 1] || '';
  const [selected, setSelected] = useState(initial);
  const [open, setOpen] = useState<number | null>(null);
  const active = days.includes(selected) ? selected : initial;
  const rows = events.filter(e => e.date === active);
  if (!events.length) return <View style={styles.empty}><Text style={styles.title}>Tidak ada event</Text><Text style={styles.muted}>Tidak ada rilis USD yang cocok dengan filter ini.</Text></View>;
  return <View style={{ gap: 18 }}>
    <View style={styles.between}><Text style={styles.title}>Agenda USD</Text><Text style={styles.muted}>{events.length} event · {days.length} hari</Text></View>
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
      {days.map(day => { const label = dateLabel(day), picked = active === day; return <Pressable key={day} accessibilityRole="button" accessibilityLabel={`Tanggal ${label.date || label.day}`} accessibilityState={{ selected: picked }} onPress={() => { setSelected(day); setOpen(null); }} style={[styles.day, picked && styles.activeDay]}>
        <Text style={[styles.dayName, picked && styles.dark]}>{label.day}</Text><Text style={[styles.dayDate, picked && styles.dark]}>{label.date}</Text><Text style={[styles.count, picked && styles.dark]}>{events.filter(e => e.date === day).length} event</Text>
      </Pressable>; })}
    </ScrollView>
    <View style={styles.between}><Text style={styles.muted}>Waktu Eastern / New York (ET)</Text><Text style={styles.muted}>● = dampak</Text></View>
    <View style={styles.agenda}>
      {rows.map((event, i) => {
        const impact = event.impact === 'High' ? { label: 'Tinggi', color: C.red } : event.impact === 'Medium' ? { label: 'Sedang', color: C.gold } : { label: event.impact === 'Low' ? 'Rendah' : event.impact || 'Lainnya', color: C.muted };
        return <View key={`${event.title}:${event.time}:${i}`} style={[styles.event, i > 0 && styles.separator]}>
          <Pressable accessibilityRole="button" accessibilityLabel={`${event.time}, ${event.title}, dampak ${impact.label}`} accessibilityState={{ expanded: open === i }} onPress={() => setOpen(open === i ? null : i)} style={styles.eventHeader}>
            <Text style={styles.time}>{event.time || 'Tentatif'}</Text>
            <View style={{ flex: 1, gap: 6 }}><Text style={styles.eventTitle}>{event.title}</Text><Text style={[styles.impact, { color: impact.color }]}>● {impact.label}</Text></View>
            <Text style={styles.chevron}>{open === i ? '−' : '+'}</Text>
          </Pressable>
          <View style={styles.metrics}>{[['Actual', event.actual], ['Forecast', event.forecast], ['Previous', event.previous]].map(([label, number]) => <View key={label} style={styles.metric}><Text style={styles.metricLabel}>{label}</Text><Text selectable style={[styles.number, label === 'Actual' && { color: C.gold }]}>{value(number)}</Text></View>)}</View>
          {open === i && <View style={{ gap: 12, marginTop: 12 }}><Text style={styles.muted}>Tanggal sumber: {event.date} · USD{value(event.actual) === '—' ? '\nActual belum tersedia pada feed.' : '\nActual tersedia pada feed; bukan penilaian bullish/bearish.'}</Text><Pressable accessibilityRole="button" onPress={() => onAnalyze(event.title)} style={styles.analyze}><Text style={{ color: C.gold, fontWeight: '600' }}>Analisis event ini →</Text></Pressable></View>}
        </View>;
      })}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  title: { color: C.text, fontSize: 19, fontWeight: '700' }, muted: { color: C.muted, fontSize: 11, lineHeight: 18 },
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  day: { minWidth: 78, padding: 12, borderRadius: 14, backgroundColor: C.raised, gap: 5, alignItems: 'center', borderWidth: 1, borderColor: C.line },
  activeDay: { backgroundColor: C.gold, borderColor: C.gold }, dark: { color: C.bg },
  dayName: { color: C.muted, fontSize: 11, textTransform: 'uppercase' }, dayDate: { color: C.text, fontSize: 15, fontWeight: '700' }, count: { color: C.muted, fontSize: 10 },
  agenda: { borderRadius: 16, borderWidth: 1, borderColor: C.line, overflow: 'hidden' }, event: { padding: 14, backgroundColor: '#0d0d0c' }, separator: { borderTopWidth: 1, borderColor: C.line },
  eventHeader: { flexDirection: 'row', gap: 10, alignItems: 'flex-start', minHeight: 44 },
  time: { width: 55, color: C.silver, fontSize: 12, lineHeight: 20, fontWeight: '600' }, eventTitle: { color: C.text, fontSize: 14, lineHeight: 21, fontWeight: '600' }, impact: { fontSize: 10 }, chevron: { color: C.gold, fontSize: 20 },
  metrics: { flexDirection: 'row', marginTop: 12, gap: 10 }, metric: { flex: 1, gap: 4 }, metricLabel: { color: C.muted, fontSize: 10 }, number: { color: C.silver, fontSize: 14, fontWeight: '600', lineHeight: 20 },
  analyze: { alignItems: 'center', minHeight: 42, justifyContent: 'center', backgroundColor: C.raised, borderRadius: 10 }, empty: { padding: 20, gap: 10 },
});
