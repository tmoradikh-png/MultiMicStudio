# MultiMic Studio — Mobile (React Native / Expo)

One codebase for **iOS and Android**. Each phone is one speaker's microphone:
records locally, keeps a backup, then uploads to the backend.

## Requirements

- Node.js 18+
- Expo CLI (`npx expo`)
- The [Expo Go](https://expo.dev/go) app on your phone, or an iOS/Android simulator

## Setup

```powershell
cd MultiMicStudio/mobile
npm install
```

Point the app at your backend. On a **physical phone** you must use your computer's
LAN IP (not `localhost`), because the phone can't reach the dev machine via localhost:

```powershell
# PowerShell, for the current session:
$env:EXPO_PUBLIC_API_URL = "http://192.168.1.20:8000"   # <- your machine's LAN IP
npm start
```

Then scan the QR code with Expo Go (Android) or the Camera app (iOS).

## Build native apps (App Store / Play Store)

This is an Expo project, so production builds use EAS:

```powershell
npm install -g eas-cli
eas build --platform ios
eas build --platform android
```

Bundle identifiers are already set in `app.json`
(`ai.multimic.studio`).

## Screens

- **Login / Signup** — JWT auth against the backend.
- **Home** — create or join a session.
- **Create** — names the session, shows the join **code + QR**, shares an invite.
- **Join** — enter the host's code and your speaker name.
- **Record** — visible recording indicator + consent reminder, sync-clap prompt,
  local high-quality recording (48 kHz), stop, and upload with retry. The host can
  trigger **mix & transcribe** after everyone has uploaded.

## Privacy

The recording screen always shows a live indicator while recording and reminds users
to obtain consent, per the product's privacy requirements. Microphone permission is
requested before the first recording.

## What maps to the full product later

- QR **scanning** to join (MVP shows/enters the code; camera scan is additive).
- Device **roles** (backup recorder, camera) — the `role` plumbing already exists.
- Live input-level metering and resumable chunked uploads.
