import React, { useState } from "react";
import { Pressable, Share, Text, TextInput, View } from "react-native";
import QRCode from "react-native-qrcode-svg";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api, type SessionData } from "../api/client";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "CreateSession">;

export default function CreateSessionScreen({ navigation }: Props) {
  const [title, setTitle] = useState("");
  const [session, setSession] = useState<SessionData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onCreate() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSession(title.trim() || "Untitled session");
      setSession(created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create session");
    } finally {
      setBusy(false);
    }
  }

  async function onStartRecording() {
    if (!session) return;
    // The host is the first participant on the session.
    const host = session.participants.find((p) => p.role === "host") ??
      session.participants[0];
    navigation.replace("Record", { session, participant: host, role: "host" });
  }

  if (session) {
    return (
      <View style={[styles.screen, styles.center]}>
        <Text style={styles.title}>{session.title}</Text>
        <Text style={styles.subtitle}>
          Others join with this code (or scan the QR).
        </Text>

        <View style={[styles.card, { alignItems: "center" }]}>
          <Text style={styles.code}>{session.code}</Text>
          <View style={{ height: 18 }} />
          <View style={{ backgroundColor: "#fff", padding: 12, borderRadius: 12 }}>
            <QRCode value={session.code} size={170} />
          </View>
        </View>

        <Pressable
          style={styles.buttonGhost}
          onPress={() =>
            Share.share({ message: `Join my MultiMic session. Code: ${session.code}` })
          }
        >
          <Text style={styles.buttonGhostText}>Share invite</Text>
        </Pressable>

        <Pressable style={styles.button} onPress={onStartRecording}>
          <Text style={styles.buttonText}>Continue to recording →</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={[styles.screen, styles.center]}>
      <Text style={styles.title}>New recording</Text>
      <Text style={styles.subtitle}>Give this session a name.</Text>

      <Text style={styles.label}>Session title</Text>
      <TextInput
        style={styles.input}
        placeholderTextColor={colors.muted}
        placeholder="Interview with John"
        value={title}
        onChangeText={setTitle}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={onCreate} disabled={busy}>
        <Text style={styles.buttonText}>{busy ? "Creating…" : "Create session"}</Text>
      </Pressable>
    </View>
  );
}
