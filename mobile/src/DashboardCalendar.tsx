import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';
import { Connection, request } from './api';

type CalendarEvent = { title: string; date: string; time: string; impact: string; actual?: string; forecast?: string; previous?: string };

export function DashboardCalendar({ connection, onTapFullCalendar }: { connection: Connection; onTapFullCalendar: () => void }) {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    let timer: any;
    (async () => {
      try {
        const data = await request(connection, '/dashboard/calendar');
        if (!cancelled) {
          if (data.status === 'ok' || data.status === 'cached') {
            const evs = Array.isArray(data.events) ? data.events : [];
            setEvents(evs);
            setError('');
          } else {
            setEvents([]);
            setError('');
          }
          setLoading(false);
        }
      } catch (e: any) {
        if (!cancelled) {
          setEvents([]);
          setError(e.message || 'Gagal memuat kalender');
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [connection]);

  if (error) return <View style={styles.message}><Text style={styles.muted}>{error}</Text></View>;
  if (loading) return <View style={styles.message}><ActivityIndicator color={C.gold} size="small" /></View>;
  if (!events.length) return <View style={styles.message}><Text style={styles.muted}>Tidak ada event High impact dalam 7 hari ke depan.</Text></View>;

  const grouped = events.reduce((acc, e) => {
    (acc[e.date] = acc[e.date] || []).push(e);
    return acc;
  }, {} as Record<string, CalendarEvent[]>);
  const dates = Object.keys(grouped).sort((a, b) => {
    const [am, ad, ay] = a.split('-').map(Number);
    const [bm, bd, by] = b.split('-').map(Number);
    if (ay !== by) return ay - by;
    if (am !== bm) return am - bm;
    return ad - bd;
  });

  return (
    <ScrollView style={{ flex: 1 }} showsVerticalScrollIndicator={false}>
      <View style={styles.header}>
        <Text style={styles.title}>Event High Impact (7 hari)</Text>
        <Text style={styles.count}>{events.length} event</Text>
      </View>
      {dates.map(date => (
        <View key={date} style={styles.section}>
          <Text style={styles.dateLabel}>{date}</Text>
          {grouped[date].map((e, i) => (
            <View key={`${e.title}:${e.time}:${i}`} style={styles.event}>
              <View style={styles.row}>
                <Text style={styles.time}>{e.time || 'Tentatif'}</Text>
                <Text style={styles.eventTitle}>{e.title}</Text>
              </View>
              <View style={styles.metrics}>
                <View style={styles.metric}><Text style={styles.label}>Forecast</Text><Text style={styles.value}>{e.forecast || 'N/A'}</Text></View>
                <View style={styles.metric}><Text style={styles.label}>Previous</Text><Text style={styles.value}>{e.previous || 'N/A'}</Text></View>
                {e.actual && e.actual !== 'N/A' && <View style={styles.metric}><Text style={styles.label}>Actual</Text><Text style={[styles.value, { color: C.gold }]}>{e.actual}</Text></View>}
              </View>
            </View>
          ))}
        </View>
      ))}
      <Pressable onPress={onTapFullCalendar} style={styles.button}><Text style={styles.buttonText}>Lihat Kalender Lengkap</Text></Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, paddingHorizontal: 4 },
  title: { color: '#f5f5f0', fontSize: 16, fontWeight: '700' },
  count: { color: '#8a8a84', fontSize: 12 },
  section: { marginBottom: 20 },
  dateLabel: { color: '#c4af9b', fontSize: 11, fontWeight: '600', marginBottom: 8, paddingHorizontal: 4 },
  event: { backgroundColor: '#1a1a19', borderRadius: 10, padding: 12, marginBottom: 8, gap: 10 },
  row: { flexDirection: 'row', gap: 10, alignItems: 'center' },
  time: { color: '#9a9a94', fontSize: 11, fontWeight: '600', width: 50 },
  eventTitle: { flex: 1, color: '#f5f5f0', fontSize: 13, fontWeight: '600' },
  metrics: { flexDirection: 'row', gap: 12 },
  metric: { flex: 1 },
  label: { color: '#8a8a84', fontSize: 10, marginBottom: 3 },
  value: { color: '#d4cfc4', fontSize: 11, fontWeight: '600' },
  button: { alignItems: 'center', padding: 14, marginTop: 12, backgroundColor: '#2a2a29', borderRadius: 10 },
  buttonText: { color: '#c4af9b', fontSize: 13, fontWeight: '600' },
  message: { padding: 20, alignItems: 'center', justifyContent: 'center' },
  muted: { color: '#8a8a84', fontSize: 12, textAlign: 'center' },
});
