# Sightline mobile

React Native + Expo phone app. Currently opens a full-screen rear camera preview,
with accessible permission and camera-error controls. The camera is released when
the app goes into the background and resumes when you return.

## Run on your phone

Install Expo Go on your iPhone or Android phone. From the repository root:

```powershell
cd mobile
npm ci
npm start
```

Keep your phone and computer on the same Wi-Fi network. Scan the terminal QR code
with the iPhone Camera app or Expo Go on Android, then allow camera access.
If you previously blocked access, use the Open Settings button to enable it
(when running in Expo Go, this is Expo Go's camera permission).

This project uses Expo SDK 57 for Expo Go compatibility. No backend or API keys
are needed for the camera preview. The existing browser app remains in `web/`.

## Checks

```powershell
npm run lint
npm run typecheck
npx expo install --check
npx expo export --platform all
```

The export checks both iOS and Android JavaScript bundles. Real-device testing
is still needed for camera permissions, returning from Settings, backgrounding,
and VoiceOver/TalkBack.

The initial dependency audit reports 22 advisories (15 high, 7 moderate) in the
Expo/React Native dependency tree. npm's proposed fixes include incompatible
framework downgrades, so no forced audit fix was applied. Review these before
production deployment.

## Current scope

This is the native app foundation and camera preview only. Camera frames are not
captured, stored, or uploaded. Backend memory, object detection, voice commands,
directional audio, and orientation tracking have not yet been connected to this
app. Those features currently live in the browser prototype.

The FastAPI backend can remain a separate service when those flows are ported.
The phone will need a reachable server URL; the browser's relative `/api` paths
and Vite proxy do not apply to the native app.
