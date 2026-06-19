import React, { useEffect, useState } from "react";
import { Pressable, Share, Text, TextInput, View } from "react-native";
import QRCode from "react-native-qrcode-svg";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api, describeError, type SessionData } from "../api/client";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "CreateSession">;

export default function CreateSessionScreen({ navigation }: Props) {
  const [title, setTitle] = useState("");
  const [session, setSession] = useState<SessionData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Once the session exists, poll for joiners so the host sees phones connect
  // before starting (clear host flow: create → share code → see participants).
  useEffect(() => {
    if (!session) return;
    let active = true;
    const poll = setInterval(async () => {
      try {
        const s = await api.getSession(session.id);
        if (active) setSession(s);
      } catch {
        // Ignore transient errors; keep last known state.
      }
    }, 2500);
    return () => {
      active = false;
      clearInterval(poll);
    };
  }, [session?.id]);

  async function onCreate() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSession(title.trim() || "Untitled session");
      setSession(created);
    } catch (e) {
      setError(describeError(e));
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
    const guests = session.participants.filter((p) => p.role !== "host");
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

        <View style={styles.card}>
          <Text style={styles.label}>
            Joined ({guests.length})
          </Text>
          {guests.length === 0 ? (
            <Text style={[styles.subtitle, { marginBottom: 0 }]}>
              Waiting for phones to join…
            </Text>
          ) : (
            guests.map((p) => (
              <Text key={p.id} style={[styles.subtitle, { marginBottom: 4 }]}>
                • {p.speaker_name}
              </Text>
            ))
          )}
        </View>

        <Pressable
          style={styles.buttonGhost}
          onPress={() =>
            Share.share({ message: `Join my MultiMic session. Code: ${session.code}` })
          }
        >
          <Text style={styles.buttonGhostText}>Share invite</Text>
        </Pressable>

        <Pressable
          style={[styles.button, { marginTop: 8 }]}
          onPress={() =>
            navigation.navigate("Live", {
              room: session.code,
              role: "speaker",
              autoStart: true,
            })
          }
        >
          <Text style={styles.buttonText}>Go live now</Text>
        </Pressable>

        <Pressable style={styles.button} onPress={onStartRecording}>
          <Text style={styles.buttonText}>Continue to recording</Text>
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
