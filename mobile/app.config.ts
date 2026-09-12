export default {
  expo: {
    name: 'Bayproject', slug: 'bayproject-private', version: '1.0.0',
    icon: './assets/bayproject-logo.jpeg',
    backgroundColor: '#070707',
    orientation: 'portrait', userInterfaceStyle: 'dark', scheme: 'bayproject',
    ios: { supportsTablet: true, bundleIdentifier: 'com.bayproject.privateapp' },
    android: { package: 'com.bayproject.privateapp',
      ...(process.env.GOOGLE_SERVICES_JSON ? { googleServicesFile: process.env.GOOGLE_SERVICES_JSON } : {}) },
    plugins: ['expo-secure-store', 'expo-notifications', ['expo-image-picker', {
      photosPermission: 'Izinkan Bayproject memilih screenshot chart untuk dianalisis.',
      cameraPermission: false, microphonePermission: false
    }]],
    extra: { eas: { projectId: process.env.EXPO_PUBLIC_EAS_PROJECT_ID || '' } },
  }
};
