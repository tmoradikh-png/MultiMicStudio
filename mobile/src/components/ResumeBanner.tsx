import React, { useEffect, useState } from "react";
import { Pressable, Text, View } from "react-native";
import {
  clearActiveSession,
  describeError,
  getActiveSession,
  resumeSession,
  type ActiveSession,
} from "../api/client";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

/**
 * If this phone was in a session when the app last closed, offer to jump straight
 * back into it. Guests reconnect with their stored token (same participant, no
 * duplicate); hosts re-fetch the session. Resumability is re-checked against the
 * backend, so an ended/closed session is cleared instead of dead-ending the user.
 *
 * The parent supplies `onResume` (which navigates to the Record screen), so this
 * component stays independent of any one screen's navigation typing.
 */
export default function ResumeBanner({
  onResume,
}: {
  onResume: (params: RootStackParamList["Record"]) => void;
}) {
  const [active, setActive] = useState<ActiveSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getActiveSession().then(setActive).catch(() => undefined);
  }, []);

  if (!active) return null;

  async function onResumePress() {
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      const result = await resumeSession(active);
      if (!result) {
        setError("That session has ended. Start or join a new one.");
        setActive(null);
        return;
      }
      onResume({
        session: result.session,
        participant: result.participant,
        role: active.role,
      });
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDismiss() {
    await clearActiveSession().catch(() => undefined);
    setActive(null);
  }

  return (
    <View style={[styles.card, { borderColor: colors.primary }]}>
      <Text style={styles.label}>Resume your session</Text>
      <Text style={[styles.subtitle, { marginBottom: 12 }]}>
        You were in session {active.code} as {active.speakerName}. Pick up where
        you left off.
      </Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable style={styles.button} onPress={onResumePress} disabled={busy}>
        <Text style={styles.buttonText}>
          {busy ? "Resuming…" : `Resume ${active.code}`}
        </Text>
      </Pressable>
      <Pressable style={styles.buttonGhost} onPress={onDismiss} disabled={busy}>
        <Text style={styles.buttonGhostText}>Leave session</Text>
      </Pressable>
    </View>
  );
}
