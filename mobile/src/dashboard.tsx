import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { AppState, Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { Connection, request } from './api';

export const DASHBOARD_KINDS = ['macro', 'debrief', 'calendar', 'analysis', 'news', 'signals'] as const;
export type DashboardKind = typeof DASHBOARD_KINDS[number];
export type DashboardSection = { text?: string | null; generated_at?: string | null; pending?: boolean; error?: string; last_attempt?: string | null; attempts?: number };
export type DashboardData = Partial<Record<DashboardKind, DashboardSection>>;
type DashboardContextValue = { data: DashboardData; loading: boolean; refreshing: boolean; error: string; refresh: () => Promise<void>; section: (kind: DashboardKind) => DashboardSection };
const DashboardContext = createContext<DashboardContextValue | null>(null);
const CHUNK_SIZE = 1800;

function storageKey(connection: Connection) {
  let hash = 2166136261;
  for (const char of connection.url) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return `bayproject_dashboard_${(hash >>> 0).toString(16)}`;
}
async function restore(connection: Connection): Promise<DashboardData> {
  if (Platform.OS === 'web') return {};
  const key = storageKey(connection);
  const count = Number(await SecureStore.getItemAsync(`${key}_count`)) || 0;
  if (!count || count > 100) return {};
  const chunks = await Promise.all(Array.from({ length: count }, (_, index) => SecureStore.getItemAsync(`${key}_${index}`)));
  if (chunks.some(chunk => chunk === null)) return {};
  return JSON.parse(chunks.join(''));
}
async function persist(connection: Connection, data: DashboardData) {
  if (Platform.OS === 'web') return;
  const key = storageKey(connection);
  const value = JSON.stringify(data);
  const chunks = Array.from({ length: Math.ceil(value.length / CHUNK_SIZE) }, (_, index) => value.slice(index * CHUNK_SIZE, (index + 1) * CHUNK_SIZE));
  const oldCount = Number(await SecureStore.getItemAsync(`${key}_count`)) || 0;
  await Promise.all(chunks.map((chunk, index) => SecureStore.setItemAsync(`${key}_${index}`, chunk)));
  await SecureStore.setItemAsync(`${key}_count`, String(chunks.length));
  await Promise.all(Array.from({ length: Math.max(0, oldCount - chunks.length) }, (_, index) => SecureStore.deleteItemAsync(`${key}_${chunks.length + index}`)));
}
function merge(previous: DashboardData, incoming: DashboardData) {
  const next = { ...previous };
  for (const kind of DASHBOARD_KINDS) next[kind] = { ...(previous[kind] || {}), ...(incoming[kind] || {}) };
  return next;
}

export function DashboardProvider({ connection, children }: { connection: Connection; children: React.ReactNode }) {
  const [data, setData] = useState<DashboardData>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(true);
  const polling = useRef(false);
  const fetchDashboard = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      const incoming = await request(connection, '/dashboard');
      setData(previous => {
        const next = merge(previous, incoming);
        void persist(connection, next).catch(() => undefined);
        return next;
      });
      setError('');
    } catch (reason: any) {
      setError(reason?.message || 'Dashboard tidak dapat diperbarui.');
    } finally {
      polling.current = false;
      setLoading(false);
    }
  }, [connection]);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError('');
    try {
      await request(connection, '/dashboard/refresh', 'POST');
      setData(previous => merge(previous, Object.fromEntries(DASHBOARD_KINDS.map(kind => [kind, { pending: true }] ))));
      await fetchDashboard();
    } catch (reason: any) {
      setError(reason?.message || 'Refresh dashboard gagal.');
    } finally {
      setRefreshing(false);
    }
  }, [connection, fetchDashboard]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    restore(connection).then(snapshot => { if (!cancelled) setData(snapshot); }).catch(() => undefined).finally(() => { if (!cancelled) void fetchDashboard(); });
    const appState = AppState.addEventListener('change', state => { active.current = state === 'active'; if (active.current) void fetchDashboard(); });
    const timer = setInterval(() => { if (active.current && Object.values(data).some(section => section?.pending)) void fetchDashboard(); }, 15000);
    return () => { cancelled = true; appState.remove(); clearInterval(timer); };
  }, [connection, fetchDashboard, data]);
  return <DashboardContext.Provider value={{ data, loading, refreshing, error, refresh, section: kind => data[kind] || {} }}>{children}</DashboardContext.Provider>;
}
export function useDashboard() {
  const value = useContext(DashboardContext);
  if (!value) throw new Error('useDashboard harus berada di DashboardProvider.');
  return value;
}
export function parseDashboardJson(section: DashboardSection) {
  if (!section.text) return null;
  try { return JSON.parse(section.text); } catch { return null; }
}
