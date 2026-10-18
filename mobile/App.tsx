import { CameraView, useCameraPermissions } from 'expo-camera';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import Controls from './src/Controls';

export default function App() {
  const [permission, requestPermission, refreshPermission] = useCameraPermissions();
  const [active, setActive] = useState(AppState.currentState === 'active');
  const [error, setError] = useState('');
  const [cameraKey, setCameraKey] = useState(0);
  const [cameraReady, setCameraReady] = useState(false);   // set by CameraView's onCameraReady
  const cameraRef = useRef<CameraView>(null);
  // Controls calls this when photos keep failing: remounting the camera clears a stuck capture on iOS.
  const restartCamera = useCallback(() => { setCameraReady(false); setCameraKey((key) => key + 1); }, []);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (state) => {
      setActive(state === 'active');
      if (state !== 'active') setCameraReady(false);   // the camera is released in the background
      if (state === 'active') {
        void refreshPermission().catch(() => setError('Could not check camera permission.'));
      }
    });
    return () => subscription.remove();
  }, [refreshPermission]);

  async function enableCamera() {
    setError('');
    try {
      if (permission?.canAskAgain === false) {
        await Linking.openSettings();
      } else {
        await requestPermission();
      }
    } catch {
      setError('Could not open camera permissions. Please try again.');
    }
  }

  const granted = permission?.granted;
  const blocked = permission?.canAskAgain === false;

  return (
    <View style={styles.container}>
      <StatusBar hidden />
      {granted && active && !error && (
        <CameraView
          ref={cameraRef}
          key={cameraKey}
          style={StyleSheet.absoluteFill}
          facing="back"
          mode="picture"
          accessibilityLabel="Rear camera preview"
          onCameraReady={() => setCameraReady(true)}
          onMountError={() => { setCameraReady(false); setError('Camera unavailable. Please try again.'); }}
        />
      )}
      {granted && active && !error && <Controls cameraRef={cameraRef} cameraReady={cameraReady} onRestartCamera={restartCamera} />}
      {(!granted || error) && (
        <View style={styles.message}>
          <Text style={styles.title} accessibilityRole="header">Sightline</Text>
          <Text style={styles.text} accessibilityLiveRegion="polite">
            {error || (!permission
              ? 'Checking camera permission…'
              : blocked
                ? 'Enable camera access in Settings to show the camera.'
                : 'Allow camera access to show the camera preview.')}
          </Text>
          {permission && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={granted ? 'Retry camera' : blocked ? 'Open camera settings' : 'Allow camera access'}
              style={({ pressed }) => [styles.button, pressed && styles.pressed]}
              onPress={granted ? () => { setError(''); setCameraReady(false); setCameraKey((key) => key + 1); } : enableCamera}
            >
              <Text style={styles.buttonText}>{granted ? 'Try again' : blocked ? 'Open Settings' : 'Allow camera'}</Text>
            </Pressable>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  message: { flex: 1, justifyContent: 'center', padding: 32, gap: 24 },
  title: { color: '#fff', fontSize: 32, fontWeight: '700' },
  text: { color: '#fff', fontSize: 22, lineHeight: 32 },
  button: { minHeight: 64, backgroundColor: '#fff', borderRadius: 12, padding: 20, alignItems: 'center', justifyContent: 'center' },
  pressed: { opacity: 0.75 },
  buttonText: { color: '#000', fontSize: 22, fontWeight: '700' },
});
