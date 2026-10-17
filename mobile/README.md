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
