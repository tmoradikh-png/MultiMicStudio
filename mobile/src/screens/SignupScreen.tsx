import React, { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  Text,
  TextInput,
} from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useAuth } from "../context/AuthContext";
import { describeError } from "../api/client";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Signup">;

export default function SignupScreen({ navigation }: Props) {
  const { signUp } = useAuth();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit() {
    setBusy(true);
    setError(null);
    try {
      await signUp(email.trim(), name.trim(), password);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={[styles.screen, styles.center]}
    >
      <Text style={styles.title}>Create account</Text>
      <Text style={styles.subtitle}>Join a recording in seconds.</Text>

      <Text style={styles.label}>Name</Text>
      <TextInput
        style={styles.input}
        placeholderTextColor={colors.muted}
        placeholder="Your name"
        value={name}
        onChangeText={setName}
      />
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
        placeholder="Choose a password"
        value={password}
        onChangeText={setPassword}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={onSubmit} disabled={busy}>
        <Text style={styles.buttonText}>
          {busy ? "Creating…" : "Create account"}
        </Text>
      </Pressable>
      <Pressable style={styles.buttonGhost} onPress={() => navigation.goBack()}>
        <Text style={styles.buttonGhostText}>Back to sign in</Text>
      </Pressable>
    </KeyboardAvoidingView>
  );
}
