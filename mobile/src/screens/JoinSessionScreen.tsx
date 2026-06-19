import React, { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api, describeError, type JoinResult } from "../api/client";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "JoinSession">;

export default function JoinSessionScreen({ navigation }: Props) {
  const [code, setCode] = useState("");
  const [speakerName, setSpeakerName] = useState("");
  const [joined, setJoined] = useState<JoinResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onJoin() {
    const trimmed = code.trim().toUpperCase();
    if (trimmed.length < 6) {
      setError("Enter the full 6‑character session code from the host.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.joinSession(trimmed, speakerName.trim(), "mobile");
      setJoined(result);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  if (joined) {
    return (
      <View style={[styles.screen, styles.center]}>
        <Text style={styles.title}>{joined.session.title}</Text>
        <Text style={styles.subtitle}>
          Joined as {joined.participant.speaker_name}. Choose how this phone should work.
        </Text>

        <Pressable
          style={styles.button}
          onPress={() =>
            navigation.replace("Live", {
              room: joined.session.code,
              role: "mic",
              name: joined.participant.speaker_name,
              autoStart: true,
            })
          }
        >
          <Text style={styles.buttonText}>Use this phone as live mic</Text>
        </Pressable>

        <Pressable
          style={styles.buttonGhost}
          onPress={() =>
            navigation.replace("Record", {
              session: joined.session,
              participant: joined.participant,
              role: "speaker_mic",
            })
          }
        >
          <Text style={styles.buttonGhostText}>Continue to recording</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={[styles.screen, styles.center]}>
      <Text style={styles.title}>Join session</Text>
      <Text style={styles.subtitle}>Enter the host's code and your name.</Text>

      <Text style={styles.label}>Session code</Text>
      <TextInput
        style={[styles.input, { letterSpacing: 4, fontSize: 22, textAlign: "center" }]}
        autoCapitalize="characters"
        placeholderTextColor={colors.muted}
        placeholder="ABC123"
        maxLength={6}
        value={code}
        onChangeText={(t) => setCode(t.toUpperCase())}
      />
      <Text style={styles.label}>Your speaker name</Text>
      <TextInput
        style={styles.input}
        placeholderTextColor={colors.muted}
        placeholder="e.g. Sara (optional)"
        value={speakerName}
        onChangeText={setSpeakerName}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={onJoin} disabled={busy}>
        <Text style={styles.buttonText}>{busy ? "Joining…" : "Join"}</Text>
      </Pressable>
    </View>
  );
}
