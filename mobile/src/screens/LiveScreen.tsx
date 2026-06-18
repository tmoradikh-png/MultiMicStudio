import React, { useMemo, useState } from "react";
import {
  Linking,
  Platform,
  Pressable,
  ScrollView,
  Switch,
  Text,
  View,
} from "react-native";
import { WebView } from "react-native-webview";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { LIVE_BASE_URL, LIVE_IS_SECURE } from "../config";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Live">;

// Live mic mode turns THIS phone into a live microphone: it captures the voice,
// processes it, and plays it straight out of whatever is connected to the phone —
// a Bluetooth speaker or wired headphones/speaker. There is no second "listener"
// phone and no network room; everything happens on this one device.
//
// It is powered by the backend's /live/mic page (getUserMedia + WebAudio routed to
// the phone's audio output) hosted in a WebView, because React Native / Expo Go has
// no Web Audio API. The microphone (getUserMedia) is only granted on a secure
// (https) origin — see LIVE_IS_SECURE.
export default function LiveScreen(_props: Props) {
  const [save, setSave] = useState(false);
  const [live, setLive] = useState(false);

  const liveUrl = useMemo(
    () => `${LIVE_BASE_URL}/live/mic${save ? "?save=1" : ""}`,
    [save],
  );

  // The microphone can only be granted on a secure (https) origin.
  const micBlocked = !LIVE_IS_SECURE;

  if (live) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <WebView
          source={{ uri: liveUrl }}
          // Let the page use the mic and play audio without an extra native tap.
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
          <Text style={styles.buttonText}>Stop live mic</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 20 }}
    >
      <Text style={styles.title}>Live microphone</Text>
      <Text style={styles.subtitle}>
        Turn this phone into a live microphone. Your voice is captured, processed,
        and played out of whatever is connected to the phone.
      </Text>

      <View style={styles.card}>
        <Text style={{ color: colors.text, fontWeight: "700", marginBottom: 8 }}>
          🔌 Before you start
        </Text>
        <Text style={{ color: colors.muted, fontSize: 14, lineHeight: 21 }}>
          Connect your output first: pair a{" "}
          <Text style={{ color: colors.text }}>Bluetooth speaker</Text> or plug in{" "}
          <Text style={{ color: colors.text }}>wired headphones / a speaker</Text>.
          The sound comes out of whatever is connected. Using the phone's own
          speaker will squeal (feedback) unless the mic is far from it.
        </Text>
      </View>

      <View
        style={[
          styles.card,
          { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
        ]}
      >
        <View style={{ flex: 1, paddingRight: 12 }}>
          <Text style={{ color: colors.text, fontSize: 16, fontWeight: "600" }}>
            Save a recording
          </Text>
          <Text style={{ color: colors.muted, fontSize: 13, marginTop: 2 }}>
            Also keep a recording of this live session.
          </Text>
        </View>
        <Switch
          value={save}
          onValueChange={setSave}
          trackColor={{ true: colors.primary, false: colors.border }}
        />
      </View>

      {micBlocked && (
        <View style={[styles.card, { borderColor: colors.danger }]}>
          <Text style={{ color: colors.danger, fontWeight: "700", marginBottom: 6 }}>
            Microphone needs a secure (https) server
          </Text>
          <Text style={{ color: colors.muted, fontSize: 13, lineHeight: 20 }}>
            Phones only grant the microphone over https. Your live server is{" "}
            <Text style={{ color: colors.text }}>{LIVE_BASE_URL}</Text>. Set
            EXPO_PUBLIC_LIVE_URL to an https backend to use the live mic.
          </Text>
        </View>
      )}

      <Pressable
        style={[styles.button, micBlocked && { opacity: 0.4 }]}
        disabled={micBlocked}
        onPress={() => setLive(true)}
      >
        <Text style={styles.buttonText}>Go live</Text>
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
