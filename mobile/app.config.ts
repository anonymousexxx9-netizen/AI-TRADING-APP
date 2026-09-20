export default {
  expo: {
    name: 'Bayproject', slug: 'bayproject-private', version: '1.0.0',
    icon: './assets/bayproject-logo.jpeg',
    splash: {
      image: './assets/bayproject-logo.jpeg',
      resizeMode: 'contain',
      backgroundColor: '#070707',
    },
    backgroundColor: '#070707',
    orientation: 'portrait', userInterfaceStyle: 'dark', scheme: 'bayproject',
    ios: { supportsTablet: true, bundleIdentifier: 'com.bayproject.privateapp' },
    android: { package: 'com.bayproject.privateapp',
      ...(process.env.GOOGLE_SERVICES_JSON ? { googleServicesFile: process.env.GOOGLE_SERVICES_JSON } : {}) },
    plugins: ['expo-font', 'expo-secure-store', 'expo-notifications', ['expo-image-picker', {
      photosPermission: 'Izinkan Bayproject memilih screenshot chart untuk dianalisis.',
      cameraPermission: false, microphonePermission: false
    }]],
    extra: { eas: { projectId: '8f7108a4-b58e-4a49-9705-35bb7962c758' } },
  }
};
