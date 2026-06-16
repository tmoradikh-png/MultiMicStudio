import React, { useState } from "react";
import { Pressable, Text, TextInput, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "JoinSession">;

export default function JoinSessionScreen({ navigation }: Props) {
  const [code, setCode] = useState("");
  const [speakerName, setSpeakerName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onJoin() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.joinSession(
        code.trim().toUpperCase(),
        speakerName.trim(),
        "mobile",
      );
      navigation.replace("Record", {
        session: result.session,
        participant: result.participant,
        role: "speaker_mic",
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not join session");
    } finally {
      setBusy(false);
    }
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
