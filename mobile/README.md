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

This project uses Expo SDK 57 for Expo Go compatibility. The camera preview alone needs
no backend. To use voice, memory and guidance, connect it to the backend (next section).
The browser app remains in `web/`.

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

## Connect to the backend (memory, voice, Gemini, directional beeps)

The app calls the same API as the web page (`docs/CONTRACT.md`) and holds no keys: the
backend owns Gemini, Perceptron and ElevenLabs. The phone cannot use `localhost` or the Vite
proxy, so it needs a URL that reaches the backend directly.

**Same Wi-Fi (simplest):**
```powershell
# Terminal 1: listen on the network, not just localhost (allow it in the Windows firewall prompt)
cd backend
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
ipconfig        # note your IPv4 address, e.g. 192.168.1.20
# Terminal 2
cd mobile; npm start
```
In the app, open "settings", enter `http://192.168.1.20:8000` and press Connect.
(Or put it in `mobile/.env` as `EXPO_PUBLIC_API_BASE`, see `.env.example`.)

**ngrok:** run `ngrok http 8000` (the **backend** port, unlike the web page which tunnels 5173)
and enter the `https://...ngrok-free.app` address.

**Voice:** hold-to-talk records audio and the backend transcribes it, so set
`STT_PROVIDER=elevenlabs` and `ELEVENLABS_API_KEY` in the repo's `.env` (see the root README).
Without that, the app shows a text box instead. The chat then goes through
`LLM_PROVIDER` (Gemini, etc.) exactly like the web page.

**Directional beeps:** `expo-audio` has no pan control, so the app renders stereo beep files
(left/right volume baked in) once and plays the nearest pan. Phone motion
(`expo-sensors`) keeps the pan correct between server updates.

## What is and is not verified

Checked: TypeScript, lint, `npm test` (WAV beeps, pan, yaw math), and iOS and Android
bundles all build. **Not run on a phone:** nothing here has been tried on a device. Unverified:
- whether ElevenLabs accepts the `.m4a` the phone records (its docs say "all major formats");
- the unit and sign of `expo-sensors` rotation (the code assumes radians, clockwise heading);
- plain-`http://` LAN access from Expo Go on iOS/Android (ngrok `https` avoids the question);
- capture speed of about one frame a second, and the 640 px resize;
- VoiceOver/TalkBack with press-and-hold (TODO in `src/Controls.tsx`).

Guidance follows real object positions only when the backend runs the real Perceptron client;
in mock mode (the default) the beeps follow fake positions.

Further reading before the demo: `src/Controls.tsx` is the whole screen logic, mirroring
`web/src/phone/Phone.jsx`.

## Voice (text-to-speech)

Chat replies are spoken with the backend's ElevenLabs voice when `TTS_PROVIDER=elevenlabs` (root `.env`; see the root README). Fast guidance cues use the built-in voice, except fixed cues the server already has cached. If `/api/speak` fails or is slow, the app speaks with the built-in voice. Unverified: playback on real phones (`src/voice.ts` cannot detect a silent playback failure; TODO(C)).

## Debug counters (what to look at when frames do not arrive)

The green box at the top of the screen counts every frame: `Memory: N sent · ok · dropped · failed` and, while guiding, `Guide: ...`, plus the last result in plain words. A failure names the step that broke:
- `FAILED at camera: ...` the photo could not be taken (the camera was not ready, or the permission/camera is unavailable)
- `FAILED at resize: ...` the 640 px resize failed
- `FAILED at upload: ...` the frame was taken but the backend could not be reached (for example `Network request failed`, `HTTP 500`, or a timeout)
- `dropped (backend busy)` the backend answered 204 because Perceptron was still working on an earlier frame; this is normal

"Camera: starting..." means the camera has not reported ready yet; the loops wait for it (up to 4 seconds, then they try anyway and show the error). Tap "Hide debug counters" to remove the box. It is hidden from screen readers.

**`CameraNotReadyException` on iPhone** means the previous photo has not finished: expo-camera refuses a new one until it does. The app takes photos one at a time (a second request fails at once with "previous photo still in progress"), gives each photo 15 seconds ("photo timed out"), and restarts the camera view by itself only when photos keep failing for real or one has been stuck for 20+ seconds (a merely slow photo is left to finish; the restart count shows as "restarted Nx" in the Camera line). On iOS the camera is set to 640x480 photos: the default is full resolution, which is huge and slow on recent iPhones. If that count keeps climbing, photos are hanging on that phone; tell whoever is debugging what the last memory frame says.

**`FAILED at upload: Unsupported FormDataPart Implementation`** means a file was attached the old React Native way (`{uri, name, type}`), which Expo SDK 57's `fetch` rejects. Files are now attached through `src/filePart.ts` (an object with `bytes()`), used by `src/api.ts`. `npm run check:upload` proves it without a phone: it runs Expo's own multipart encoder, shows the old shape failing with that exact error, and shows the new shape producing the right filename, content type and bytes.
