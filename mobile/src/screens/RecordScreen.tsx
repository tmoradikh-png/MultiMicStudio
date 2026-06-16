import React, { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import {
  Recorder,
  uploadRecording,
  playSyncBeep,
  type FinishedRecording,
} from "../api/recorder";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Record">;

type Phase =
  | "ready"
  | "armed"
  | "recording"
  | "uploading"
  | "uploaded"
  | "upload_failed"
  | "error";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function RecordScreen({ route, navigation }: Props) {
  const { session, participant, role } = route.params;
  const recorderRef = useRef(new Recorder());
  // Keep the finished local recording so a failed upload can be retried
  // without forcing the user to record again (local backup until uploaded).
  const finishedRef = useRef<FinishedRecording | null>(null);
  // The take this phone is currently recording/uploading for. Every Start mints a
  // new take id on the backend; uploads are tagged so old takes never leak in.
  const takeIdRef = useRef<string | null>(null);
  const [phase, setPhase] = useState<Phase>("ready");
  const [elapsed, setElapsed] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isHost = role === "host";

  // Mirror `phase` into a ref so the polling interval reads the latest value.
  const phaseRef = useRef<Phase>("ready");
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // Fully reset per-take state so a second recording in the same login never
  // reuses an old recorder, file uri, timestamp, duration, phase or error.
  function resetForNewTake() {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    finishedRef.current = null;
    recorderRef.current = new Recorder(); // fresh instance before each take
    setElapsed(0);
    setMessage(null);
  }

  // Guests follow the host: poll session status and auto-start/stop so a joined
  // phone never needs to press its own button. Auto-start triggers on a NEW
  // take_id (not merely status=recording), so take 2/3 also sync correctly.
  useEffect(() => {
    if (isHost) return;
    let active = true;
    const poll = setInterval(async () => {
      try {
        const s = await api.getSessionStatus(session.id);
        if (!active) return;
        const idle =
          phaseRef.current === "ready" ||
          phaseRef.current === "uploaded" ||
          phaseRef.current === "upload_failed" ||
          phaseRef.current === "error";
        if (
          s.status === "recording" &&
          s.current_take_id &&
          s.current_take_id !== takeIdRef.current &&
          idle
        ) {
          // A brand-new take started: reset everything and begin recording it.
          resetForNewTake();
          takeIdRef.current = s.current_take_id;
          setMessage("Host started a new take \u2014 recording now.");
          await beginRecording();
        } else if (s.status === "ended" && phaseRef.current === "recording") {
          await onStop();
        }
      } catch {
        // Ignore transient poll errors; keep trying.
      }
    }, 1500);
    return () => {
      active = false;
      clearInterval(poll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isHost, session.id]);

  function startTimer() {
    setElapsed(0);
    timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
  }

  function stopTimer() {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }

  // Shared recording start used by both the host button and guest auto-start.
  async function beginRecording() {
    await recorderRef.current.start();
    setPhase("recording");
    startTimer();
  }

  async function onStart() {
    try {
      // Fresh take: clear any previous recorder/file/timer/error first.
      resetForNewTake();
      // Host arms first: start its own mic, then flip the session to "recording"
      // with a NEW take id so every joined phone begins a fresh capture. After a
      // short countdown — long enough for guests to start — the host plays one
      // audible beep that ALL mics record. The backend aligns every track to that
      // single sound, so sample-accurate sync no longer depends on poll latency.
      await recorderRef.current.start();
      startTimer();
      setPhase("armed");
      const updated = await api.startSession(session.id).catch(() => null);
      takeIdRef.current = updated?.current_take_id ?? null;
      for (let n = 3; n >= 1; n--) {
        setMessage(`Hold still \u2014 sync beep in ${n}\u2026`);
        await sleep(1000);
      }
      await playSyncBeep();
      setMessage("Recording. All phones are synced to the beep.");
      setPhase("recording");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not start recording");
      setPhase("error");
    }
  }

  async function onStop() {
    try {
      stopTimer();
      const finished = await recorderRef.current.stop();
      finishedRef.current = finished;
      await doUpload(finished);
      if (isHost) {
        await api.stopSession(session.id).catch(() => undefined);
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Recording failed");
      setPhase("error");
    }
  }

  async function doUpload(finished: FinishedRecording) {
    setPhase("uploading");
    setMessage("Uploading your audio\u2026");
    try {
      await uploadRecording(
        session.id,
        participant.id,
        takeIdRef.current,
        finished,
      );
      setPhase("uploaded");
      setMessage("Uploaded. Your part is safely stored.");
    } catch (e) {
      // Local recording is kept in finishedRef so the user can retry.
      setMessage(
        (e instanceof Error ? e.message : "Upload failed") +
          "\nYour recording is saved on this phone \u2014 tap Retry upload.",
      );
      setPhase("upload_failed");
    }
  }

  async function onRetryUpload() {
    if (finishedRef.current) {
      await doUpload(finishedRef.current);
    }
  }

  async function onProcess() {
    try {
      setMessage("Processing started. Track progress on the web dashboard.");
      await api.processSession(session.id);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not start processing");
    }
  }

  const mmss = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(
    elapsed % 60,
  ).padStart(2, "0")}`;

  return (
    <ScrollView contentContainerStyle={styles.screen}>
      <Text style={styles.title}>{session.title}</Text>
      <Text style={styles.subtitle}>
        {participant.speaker_name} · {role === "host" ? "Host" : "Speaker mic"}
      </Text>

      {/* Always-visible consent/recording indicator (privacy requirement). */}
      <View
        style={[
          styles.card,
          {
            alignItems: "center",
            borderColor:
              phase === "recording" || phase === "armed"
                ? colors.danger
                : colors.border,
          },
        ]}
      >
        <View
          style={{
            width: 14,
            height: 14,
            borderRadius: 7,
            backgroundColor:
              phase === "recording" || phase === "armed"
                ? colors.danger
                : colors.muted,
            marginBottom: 10,
          }}
        />
        <Text style={[styles.title, { fontSize: 40 }]}>{mmss}</Text>
        <Text style={styles.subtitle}>
          {phase === "armed"
            ? "● Arming — wait for the sync beep"
            : phase === "recording"
            ? "● Recording — visible to everyone present"
            : "Not recording"}
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>Session code</Text>
        <Text style={styles.code}>{session.code}</Text>
      </View>

      {message ? (
        <Text
          style={
            phase === "error" || phase === "upload_failed"
              ? styles.error
              : styles.subtitle
          }
        >
          {message}
        </Text>
      ) : null}

      {/* Host drives recording. Guests start/stop automatically with the host. */}
      {(phase === "ready" || phase === "error") && isHost ? (
        <Pressable style={styles.button} onPress={onStart}>
          <Text style={styles.buttonText}>Start recording (all phones)</Text>
        </Pressable>
      ) : null}

      {(phase === "ready" || phase === "error") && !isHost ? (
        <View style={[styles.card, { alignItems: "center" }]}>
          <Text style={styles.subtitle}>
            Waiting for the host to start… this phone will begin recording
            automatically.
          </Text>
        </View>
      ) : null}

      {phase === "recording" && isHost ? (
        <Pressable
          style={[styles.button, { backgroundColor: colors.danger }]}
          onPress={onStop}
        >
          <Text style={styles.buttonText}>Stop & upload (all phones)</Text>
        </Pressable>
      ) : null}

      {phase === "armed" ? (
        <View style={[styles.card, { alignItems: "center" }]}>
          <Text style={[styles.subtitle, { textAlign: "center" }]}>
            Arming all phones… a sync beep will play in a moment to lock
            everyone together.
          </Text>
        </View>
      ) : null}

      {phase === "recording" && !isHost ? (
        <Text style={[styles.subtitle, { textAlign: "center" }]}>
          Recording… will stop and upload automatically when the host stops.
        </Text>
      ) : null}

      {phase === "upload_failed" ? (
        <Pressable style={styles.button} onPress={onRetryUpload}>
          <Text style={styles.buttonText}>Retry upload</Text>
        </Pressable>
      ) : null}

      {(phase === "uploaded" || phase === "upload_failed") && isHost ? (
        <Pressable
          style={[styles.button, { backgroundColor: colors.success }]}
          onPress={onStart}
        >
          <Text style={styles.buttonText}>Record another take (all phones)</Text>
        </Pressable>
      ) : null}

      {(phase === "uploaded" || phase === "upload_failed") && !isHost ? (
        <View style={[styles.card, { alignItems: "center" }]}>
          <Text style={[styles.subtitle, { textAlign: "center" }]}>
            Waiting for the host to start the next take… this phone joins it
            automatically.
          </Text>
        </View>
      ) : null}

      {phase === "uploaded" && isHost ? (
        <Pressable style={styles.button} onPress={onProcess}>
          <Text style={styles.buttonText}>Mix & transcribe (host)</Text>
        </Pressable>
      ) : null}

      {phase === "uploaded" ? (
        <Pressable
          style={styles.buttonGhost}
          onPress={() => navigation.popToTop()}
        >
          <Text style={styles.buttonGhostText}>Done</Text>
        </Pressable>
      ) : null}
    </ScrollView>
  );
}
