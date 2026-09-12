import { Platform } from 'react-native';
import Constants from 'expo-constants';
import * as Device from 'expo-device';
import { Connection, request } from './api';

export async function registerPush(connection: Connection) {
  if (Platform.OS === 'web' || !Device.isDevice) throw new Error('Push memerlukan build aplikasi di HP fisik.');
  if (Constants.appOwnership === 'expo') throw new Error('Gunakan development build atau APK, bukan Expo Go, untuk push.');
  const projectId = Constants.expoConfig?.extra?.eas?.projectId || Constants.easConfig?.projectId;
  if (!projectId) throw new Error('Project ID Expo belum diatur saat build. Kotak notifikasi tetap tersedia.');
  const Notifications = await import('expo-notifications');
  if (Platform.OS === 'android') await Notifications.setNotificationChannelAsync('market', {
    name: 'Market alerts', importance: Notifications.AndroidImportance.HIGH,
  });
  let permission = await Notifications.getPermissionsAsync();
  if (permission.status !== 'granted') permission = await Notifications.requestPermissionsAsync();
  if (permission.status !== 'granted') throw new Error('Izin notifikasi belum diberikan. Aktifkan melalui pengaturan HP.');
  const token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
  await request(connection, '/devices', 'POST', { token });
  return token;
}
