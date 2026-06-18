import React, { useMemo, useState } from "react";
import {
  Linking,
  Platform,
  Pressable,
  ScrollView,
  Switch,
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

type Role = "mic" | "speaker";

// Live mode = a small wireless PA built from phones, in real time:
//   • Several phones run as MICROPHONES (role "mic"); each streams its mic into a
//     shared room (by code).
//   • One phone is the SPEAKER/output (role "speaker"); it gathers every mic in the
//     room, mixes them, processes the mix live with a chosen sound (Natural /
//     Studio Voice / Podcast / Karaoke / Party — the same family as the saved-file
//     presets), and plays it out of whatever is connected to it (Bluetooth / wired).
//     It can also save a recording of the live mix.
//
// It is powered by the backend /live/mic and /live/speaker pages (getUserMedia +
// WebAudio over a WebSocket relay) hosted in a WebView, because React Native /
// Expo Go has no Web Audio API. getUserMedia is only granted on a secure (https)
// origin — see LIVE_IS_SECURE.
const PRESETS: { id: string; label: string; desc: string }[] = [
  { id: "natural", label: "Natural / Stereo", desc: "Clean, as-is mix" },
  { id: "studio_voice", label: "Studio Voice", desc: "Warm, clear speech" },
  { id: "podcast", label: "Podcast", desc: "Tight, intelligible" },
  { id: "karaoke", label: "Karaoke", desc: "Singing + space" },
  { id: "party", label: "Party / Room", desc: "Big, wide, fun" },
];

export default function LiveScreen(_props: Props) {
  const [role, setRole] = useState<Role>("speaker");
  const [room, setRoom] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState("natural");
  const [save, setSave] = useState(false);
  const [live, setLive] = useState(false);

  const liveUrl = useMemo(() => {
    const code = encodeURIComponent(room.trim().toUpperCase());
    if (role === "mic") {
      const who = encodeURIComponent(name.trim() || "Mic");
      return `${LIVE_BASE_URL}/live/mic?room=${code}&name=${who}`;
    }
    return `${LIVE_BASE_URL}/live/speaker?room=${code}&mode=${mode}${save ? "&save=1" : ""}`;
  }, [role, room, name, mode, save]);

  const canGoLive = room.trim().length >= 3;
  // getUserMedia (the mic) is only granted on a secure (https) origin.
  const micBlocked = role === "mic" && !LIVE_IS_SECURE;

  if (live) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <WebView
          source={{ uri: liveUrl }}
          mediaPlaybackRequiresUserAction={false}
          onPermissionRequest={(event: any) => {
            try {
              event?.nativeEvent?.grant?.(event.nativeEvent.resources);
            } catch {
              // older webview versions: OS prompt handles the permission
            }
          }}
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
          <Text style={styles.buttonText}>
            {role === "mic" ? "Stop mic" : "Stop speaker"}
          </Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 20 }}
    >
      <Text style={styles.title}>Live sound</Text>
      <Text style={styles.subtitle}>
        Use several phones as wireless mics. Their sound is gathered, mixed and
        processed live, then played out of one speaker phone. Put the same room
        code on every device.
      </Text>

      <Text style={styles.label}>This phone is the…</Text>
      <View style={{ flexDirection: "row", gap: 10 }}>
        <Pressable
          style={[chip, role === "speaker" && chipActive]}
          onPress={() => setRole("speaker")}
        >
          <Text style={[chipText, role === "speaker" && chipTextActive]}>
            🔊 Speaker
          </Text>
          <Text style={chipSub}>plays the mix</Text>
        </Pressable>
        <Pressable
          style={[chip, role === "mic" && chipActive]}
          onPress={() => setRole("mic")}
        >
          <Text style={[chipText, role === "mic" && chipTextActive]}>
            🎤 Microphone
          </Text>
          <Text style={chipSub}>sends your voice</Text>
        </Pressable>
      </View>

      <Text style={styles.label}>Room code (same on every device)</Text>
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

      {role === "mic" ? (
        <>
          <Text style={styles.label}>This mic's name (optional)</Text>
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            autoCorrect={false}
            placeholder="Mic 1"
            placeholderTextColor={colors.muted}
            maxLength={24}
          />
        </>
      ) : (
        <>
          <View style={[styles.card, { marginTop: 16 }]}>
            <Text style={{ color: colors.text, fontWeight: "700", marginBottom: 6 }}>
              🔌 Connect your speaker first
            </Text>
            <Text style={{ color: colors.muted, fontSize: 13, lineHeight: 20 }}>
              Pair a Bluetooth speaker or plug in a wired speaker/headphones on this
              phone. The live mix follows this phone's audio output.
            </Text>
          </View>

          <Text style={styles.label}>Live sound</Text>
          {PRESETS.map((p) => (
            <Pressable
              key={p.id}
              style={[
                styles.card,
                {
                  marginBottom: 8,
                  flexDirection: "row",
                  alignItems: "center",
                  justifyContent: "space-between",
                  borderColor: mode === p.id ? colors.primary : colors.border,
                },
              ]}
              onPress={() => setMode(p.id)}
            >
              <View style={{ flex: 1 }}>
                <Text style={{ color: colors.text, fontSize: 16, fontWeight: "600" }}>
                  {p.label}
                </Text>
                <Text style={{ color: colors.muted, fontSize: 12, marginTop: 2 }}>
                  {p.desc}
                </Text>
              </View>
              <Text style={{ color: mode === p.id ? colors.primary : colors.muted, fontSize: 18 }}>
                {mode === p.id ? "●" : "○"}
              </Text>
            </Pressable>
          ))}

          <View
            style={[
              styles.card,
              { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
            ]}
          >
            <View style={{ flex: 1, paddingRight: 12 }}>
              <Text style={{ color: colors.text, fontSize: 16, fontWeight: "600" }}>
                Save the live mix
              </Text>
              <Text style={{ color: colors.muted, fontSize: 13, marginTop: 2 }}>
                Also keep a recording of what plays out.
              </Text>
            </View>
            <Switch
              value={save}
              onValueChange={setSave}
              trackColor={{ true: colors.primary, false: colors.border }}
            />
          </View>
        </>
      )}

      {micBlocked && (
        <View style={[styles.card, { borderColor: colors.danger, marginTop: 16 }]}>
          <Text style={{ color: colors.danger, fontWeight: "700", marginBottom: 6 }}>
            Microphone needs a secure (https) server
          </Text>
          <Text style={{ color: colors.muted, fontSize: 13, lineHeight: 20 }}>
            Phones only grant the microphone over https. Your live server is{" "}
            <Text style={{ color: colors.text }}>{LIVE_BASE_URL}</Text>. Set
            EXPO_PUBLIC_LIVE_URL to an https backend to use a phone as a mic.
          </Text>
        </View>
      )}

      <Pressable
        style={[styles.button, (!canGoLive || micBlocked) && { opacity: 0.4 }]}
        disabled={!canGoLive || micBlocked}
        onPress={() => setLive(true)}
      >
        <Text style={styles.buttonText}>
          {role === "mic" ? "Go live (mic)" : "Start speaker"}
        </Text>
      </Pressable>

      <Pressable style={styles.buttonGhost} onPress={() => Linking.openURL(liveUrl)}>
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
const chipText = { color: colors.muted, fontWeight: "700" as const, fontSize: 15 };
const chipTextActive = { color: colors.text };
const chipSub = { color: colors.muted, fontSize: 11, marginTop: 2 };
