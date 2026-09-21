import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

export type Connection = { url: string; token: string };
const key = 'bayproject_connection';

export async function loadConnection(): Promise<Connection | null> {
  // Browser preview intentionally does not persist the private access token.
  if (Platform.OS === 'web') return null;
  const value = await SecureStore.getItemAsync(key);
  return value ? JSON.parse(value) : null;
}

export async function saveConnection(value: Connection | null) {
  if (Platform.OS === 'web') return;
  if (value) await SecureStore.setItemAsync(key, JSON.stringify(value));
  else await SecureStore.deleteItemAsync(key);
}

export function validateConnection(value: Connection) {
  const url = new URL(value.url);
  if (url.username || url.password || url.search || url.hash) throw new Error('Gunakan alamat server tanpa password, query, atau fragment.');
  const local = ['localhost', '127.0.0.1', '10.0.2.2'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(local && url.protocol === 'http:')) {
    throw new Error('Gunakan HTTPS agar kunci akses terlindungi.');
  }
  if (value.token.length < 32) throw new Error('Kunci akses minimal 32 karakter.');
  return { url: value.url.replace(/\/+$/, ''), token: value.token.trim() };
}

export async function request(connection: Connection, path: string, method = 'GET', body?: unknown): Promise<any> {
  let lastError: any;
  const maxAttempts = method === 'GET' ? 3 : 1;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(connection.url + path, {
        method, signal: controller.signal,
        headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = data?.detail;
        const message = typeof detail === 'string' ? detail : response.status === 422
          ? 'Periksa isian: pair, angka, atau format gambar tidak valid.' : `Server merespons ${response.status}.`;
        if ((response.status === 502 || response.status === 503 || response.status === 504) && attempt < maxAttempts - 1) {
          await new Promise(resolve => setTimeout(resolve, 3000 * (attempt + 1)));
          continue;
        }
        throw new Error(message);
      }
      return data;
    } catch (error: any) {
      lastError = error;
      if (error.name === 'AbortError' || error instanceof TypeError) {
        if (attempt < maxAttempts - 1) {
          await new Promise(resolve => setTimeout(resolve, 3000 * (attempt + 1)));
          continue;
        }
        throw new Error('Server sedang dibangunkan atau tidak dapat dijangkau. Coba lagi beberapa saat.');
      }
      throw error;
    } finally { clearTimeout(timer); }
  }
  throw lastError;
}
