import React, { useMemo, useState } from "react";
import {
  Linking,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";
import { WebView } from "react-native-webview";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { LIVE_BASE_URL, LIVE_IS_SECURE } from "../config";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Live">;

type Mode = "publish" | "listen";
type Channel = "mono" | "left" | "right";

// Live mode is powered by the backend's /live publish + listen pages (the working
// real-time mic engine: getUserMedia + WebAudio over a WebSocket relay). React
// Native / Expo Go has no Web Audio API and expo-av cannot stream raw PCM, so the
// only way to ship the SAME working live audio into the native app without ejecting
// is to host those pages in a WebView. The listener path needs no microphone and is
// fully reliable; the microphone (publish) path needs an HTTPS origin to be granted
// the mic — see LIVE_IS_SECURE below.
export default function LiveScreen(_props: Props) {
  const [mode, setMode] = useState<Mode>("listen");
  const [room, setRoom] = useState("");
  const [name, setName] = useState("");
  const [channel, setChannel] = useState<Channel>("mono");
  const [live, setLive] = useState(false);

  const liveUrl = useMemo(() => {
    const code = room.trim().toUpperCase();
    const who = encodeURIComponent(name.trim() || (mode === "publish" ? "Mic" : "Speaker"));
    const base = `${LIVE_BASE_URL}/live/${mode === "publish" ? "publish" : "listen"}`;
    const params = `room=${encodeURIComponent(code)}&name=${who}` +
      (mode === "publish" ? `&channel=${channel}` : "");
    return `${base}?${params}`;
  }, [mode, room, name, channel]);

  const canGoLive = room.trim().length >= 3;
  // The microphone can only be granted on a secure (https) origin.
  const micBlocked = mode === "publish" && !LIVE_IS_SECURE;

  if (live) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <WebView
          source={{ uri: liveUrl }}
          // Let the page autoplay incoming audio without a tap (listener) and use
          // the mic without a second native gesture (publisher).
          mediaPlaybackRequiresUserAction={false}
          // Android: auto-grant the getUserMedia permission the page requests, after
          // the OS-level mic permission has been accepted (declared in app.json).
          onPermissionRequest={(event: any) => {
            try {
              event?.nativeEvent?.grant?.(event.nativeEvent.resources);
            } catch {
              // older webview versions: permission handled by the OS prompt
            }
          }}
          // iOS: allow the WebView to capture the microphone inline.
          {...(Platform.OS === "ios"
            ? { mediaCapturePermissionGrantType: "grant" as const }
            : {})}
          allowsInlineMediaPlayback
          javaScriptEnabled
          domStorageEnabled
          originWhitelist={["*"]}
          style={{ flex: 1, backgroundColor: colors.bg }}
        />
        <Pressable
          style={[styles.button, { margin: 16, backgroundColor: colors.danger }]}
          onPress={() => setLive(false)}
        >
          <Text style={styles.buttonText}>Leave live</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 20 }}
    >
      <Text style={styles.title}>Live speaker</Text>
      <Text style={styles.subtitle}>
        Stream a phone's microphone to other phones in real time. One phone is the
        microphone; the others listen. Use the same room code on every phone.
      </Text>

      <Text style={styles.label}>This phone is the…</Text>
      <View style={{ flexDirection: "row", gap: 10 }}>
        <Pressable
          style={[chip, mode === "listen" && chipActive]}
          onPress={() => setMode("listen")}
        >
          <Text style={[chipText, mode === "listen" && chipTextActive]}>
            🔈 Listener
          </Text>
        </Pressable>
        <Pressable
          style={[chip, mode === "publish" && chipActive]}
          onPress={() => setMode("publish")}
        >
          <Text style={[chipText, mode === "publish" && chipTextActive]}>
            🎤 Microphone
          </Text>
        </Pressable>
      </View>

      <Text style={styles.label}>Room code</Text>
      <TextInput
        style={styles.input}
        value={room}
        onChangeText={(t) => setRoom(t.toUpperCase())}
        autoCapitalize="characters"
        autoCorrect={false}
        placeholder="e.g. STAGE1"
        placeholderTextColor={colors.muted}
        maxLength={12}
      />

      <Text style={styles.label}>Your name (optional)</Text>
      <TextInput
        style={styles.input}
        value={name}
        onChangeText={setName}
        autoCorrect={false}
        placeholder={mode === "publish" ? "Mic" : "Speaker"}
        placeholderTextColor={colors.muted}
        maxLength={24}
      />

      {mode === "publish" && (
        <>
          <Text style={styles.label}>Stereo placement</Text>
          <View style={{ flexDirection: "row", gap: 10 }}>
            {(["left", "mono", "right"] as Channel[]).map((c) => (
              <Pressable
                key={c}
                style={[chip, channel === c && chipActive]}
                onPress={() => setChannel(c)}
              >
                <Text style={[chipText, channel === c && chipTextActive]}>
                  {c === "left" ? "◀ Left" : c === "right" ? "Right ▶" : "● Mono"}
                </Text>
              </Pressable>
            ))}
          </View>
        </>
      )}

      {micBlocked && (
        <View
          style={[
            styles.card,
            { marginTop: 18, borderColor: colors.danger },
          ]}
        >
          <Text style={{ color: colors.danger, fontWeight: "700", marginBottom: 6 }}>
            Microphone needs a secure (https) server
          </Text>
          <Text style={{ color: colors.muted, fontSize: 13 }}>
            Phone browsers only grant the microphone over https. Your live server is{" "}
            <Text style={{ color: colors.text }}>{LIVE_BASE_URL}</Text>. Set
            EXPO_PUBLIC_LIVE_URL to an https backend to use this phone as a
            microphone. You can still use this phone as a Listener now.
          </Text>
        </View>
      )}

      <Pressable
        style={[
          styles.button,
          (!canGoLive || micBlocked) && { opacity: 0.4 },
        ]}
        disabled={!canGoLive || micBlocked}
        onPress={() => setLive(true)}
      >
        <Text style={styles.buttonText}>
          {mode === "publish" ? "Go live (microphone)" : "Start listening"}
        </Text>
      </Pressable>

      <Pressable
        style={styles.buttonGhost}
        onPress={() => Linking.openURL(liveUrl)}
      >
        <Text style={styles.buttonGhostText}>Open in browser instead</Text>
      </Pressable>

      <Text
        style={{ color: colors.muted, fontSize: 12, marginTop: 20, textAlign: "center" }}
      >
        Live server: {LIVE_BASE_URL}
      </Text>
    </ScrollView>
  );
}

const chip = {
  flex: 1,
  backgroundColor: colors.card,
  borderColor: colors.border,
  borderWidth: 1,
  borderRadius: 12,
  paddingVertical: 12,
  alignItems: "center" as const,
};
const chipActive = {
  borderColor: colors.primary,
  backgroundColor: "#1d2747",
};
const chipText = { color: colors.muted, fontWeight: "600" as const };
const chipTextActive = { color: colors.text };
