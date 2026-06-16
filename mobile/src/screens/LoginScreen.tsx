import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  Text,
  TextInput,
  View,
} from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useAuth } from "../context/AuthContext";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Login">;

export default function LoginScreen({ navigation }: Props) {
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit() {
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={[styles.screen, styles.center]}
    >
      <Text style={styles.title}>MultiMic Studio</Text>
      <Text style={styles.subtitle}>Sign in to start recording.</Text>

      <Text style={styles.label}>Email</Text>
      <TextInput
        style={styles.input}
        autoCapitalize="none"
        keyboardType="email-address"
        placeholderTextColor={colors.muted}
        placeholder="you@example.com"
        value={email}
        onChangeText={setEmail}
      />
      <Text style={styles.label}>Password</Text>
      <TextInput
        style={styles.input}
        secureTextEntry
        placeholderTextColor={colors.muted}
        placeholder="••••••••"
        value={password}
        onChangeText={setPassword}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={onSubmit} disabled={busy}>
        <Text style={styles.buttonText}>{busy ? "Signing in…" : "Sign in"}</Text>
      </Pressable>
      <Pressable
        style={styles.buttonGhost}
        onPress={() => navigation.navigate("Signup")}
      >
        <Text style={styles.buttonGhostText}>Create a new account</Text>
      </Pressable>
      <Pressable
        style={styles.buttonGhost}
        onPress={() => navigation.navigate("JoinSession")}
      >
        <Text style={styles.buttonGhostText}>
          Join a session as guest (no account)
        </Text>
      </Pressable>
    </KeyboardAvoidingView>
  );
}
