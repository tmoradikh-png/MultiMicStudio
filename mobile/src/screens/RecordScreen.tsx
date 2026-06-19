import React, { useEffect, useRef, useState } from "react";
import { Pressable, SafeAreaView, ScrollView, Text, View } from "react-native";
import { Audio } from "expo-av";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import {
  api,
  clearActiveSession,
  describeError,
  getToken,
  setActiveSession,
  type OutputItem,
  type Participant,
  type QualityReport,
} from "../api/client";
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
  const [statusSyncError, setStatusSyncError] = useState<string | null>(null);
  // Upload progress (0..1) + which retry attempt is running, for the upload bar.
  const [uploadPct, setUploadPct] = useState(0);
  const [uploadAttempt, setUploadAttempt] = useState<{ n: number; max: number } | null>(
    null,
  );
  // Host-only: live list of phones that have joined this session.
  const [members, setMembers] = useState<Participant[]>(session.participants ?? []);
  // Host-only result state: after "Mix & transcribe" the app polls the backend and
  // shows the plain-language quality report and the playable preset mixes in-app
  // (no web dashboard needed).
  const [resultPhase, setResultPhase] =
    useState<"idle" | "processing" | "ready" | "failed">("idle");
  const [report, setReport] = useState<QualityReport | null>(null);
  const [outputs, setOutputs] = useState<OutputItem[]>([]);
  const [playingRole, setPlayingRole] = useState<string | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);
  const resultPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isHost = role === "host";

  // Mirror `phase` into a ref so the polling interval reads the latest value.
  const phaseRef = useRef<Phase>("ready");
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  // Stop result polling + unload any playing preview when leaving the screen.
  useEffect(() => {
    return () => {
      if (resultPollRef.current) clearInterval(resultPollRef.current);
      soundRef.current?.unloadAsync().catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // Remember this as the active session so an app restart can resume straight
  // back into it (reusing the same guest token / participant — no duplicate).
  // A phone is a "guest" for resume purposes when it has no account token: the
  // host (account) re-fetches the session; a no-account guest reconnects by code.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const hasAccount = !!(await getToken());
      if (cancelled) return;
      await setActiveSession({
        sessionId: session.id,
        code: session.code,
        participantId: participant.id,
        speakerName: participant.speaker_name,
        role: isHost ? "host" : "speaker_mic",
        isGuest: !hasAccount,
      });
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [session.id, session.code, participant.id, participant.speaker_name, isHost]);

  // Host sees who has joined: poll the full session (owner-only) for the live
  // participant list so the host knows everyone is connected before starting.
  useEffect(() => {
    if (!isHost) return;
    let active = true;
    const poll = setInterval(async () => {
      try {
        const s = await api.getSession(session.id);
        if (active) setMembers(s.participants);
      } catch {
        // Ignore transient errors; keep the last known list.
      }
    }, 2500);
    return () => {
      active = false;
      clearInterval(poll);
    };
  }, [isHost, session.id]);

  // Fully reset per-take state so a second recording in the same login never
  // reuses an old recorder, file uri, timestamp, duration, phase or error.
  function resetForNewTake() {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    finishedRef.current = null;
    recorderRef.current = new Recorder(); // fresh instance before each take
    setElapsed(0);
    setMessage(null);
    setUploadPct(0);
    setUploadAttempt(null);
  }

  // Guests follow the host: poll session status and auto-start/stop so a joined
  // phone never needs to press its own button. Auto-start triggers on a NEW
  // take_id (not merely status=recording), so take 2/3 also sync correctly.
  useEffect(() => {
    if (isHost) return;
    let active = true;
    let failCount = 0;
    const pollOnce = async () => {
      try {
        const s = await api.getSessionStatus(session.id);
        if (!active) return;
        failCount = 0;
        setStatusSyncError(null);
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
          setMessage("Host started a new take - recording now.");
          await beginRecording();
        } else if (s.status === "ended" && phaseRef.current === "recording") {
          await onStop();
        }
      } catch {
        failCount += 1;
        if (failCount >= 3) {
          setStatusSyncError(
            "This phone cannot sync with host status right now. Check network, then tap Start this phone now while host is recording.",
          );
        }
      }
    };
    // Run immediately so joiners do not wait for the first interval tick.
    pollOnce().catch(() => undefined);
    const poll = setInterval(async () => {
      await pollOnce();
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
      setMessage(describeError(e));
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
      setMessage(describeError(e));
      setPhase("error");
    }
  }

  async function doUpload(finished: FinishedRecording) {
    setPhase("uploading");
    setUploadPct(0);
    setMessage("Uploading your audio\u2026");
    try {
      await uploadRecording(
        session.id,
        participant.id,
        takeIdRef.current,
        finished,
        {
          onProgress: setUploadPct,
          onAttempt: (n, max) =>
            setUploadAttempt(n > 1 ? { n, max } : null),
        },
      );
      setUploadAttempt(null);
      setPhase("uploaded");
      setMessage("Uploaded. Your part is safely stored.");
    } catch (e) {
      // Local recording is kept in finishedRef so the user can retry. Retrying
      // reuses the same participant + guest token, so the mix never duplicates.
      setMessage(
        describeError(e) +
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

  // Fallback for a guest phone if host-status polling is blocked by network/token.
  // Starts this device only when the host session is currently recording.
  async function onGuestStartNow() {
    if (isHost) return;
    try {
      const s = await api.getSessionStatus(session.id);
      if (s.status !== "recording" || !s.current_take_id) {
        setMessage("Host has not started recording yet.");
        return;
      }
      const idle =
        phaseRef.current === "ready" ||
        phaseRef.current === "uploaded" ||
        phaseRef.current === "upload_failed" ||
        phaseRef.current === "error";
      if (!idle) return;
      resetForNewTake();
      takeIdRef.current = s.current_take_id;
      await beginRecording();
      setStatusSyncError(null);
      setMessage("Recording started on this phone.");
    } catch (e) {
      setMessage(describeError(e));
    }
  }

  async function onProcess() {
    try {
      setResultPhase("processing");
      setReport(null);
      setOutputs([]);
      setMessage("Mixing and analyzing your take… this can take a moment.");
      await api.processSession(session.id);
      startResultPolling();
    } catch (e) {
      setResultPhase("failed");
      setMessage(describeError(e));
    }
  }

  // Poll the backend until processing finishes, then load the quality report and
  // the playable preset mixes so the host sees everything inside the app.
  function startResultPolling() {
    if (resultPollRef.current) clearInterval(resultPollRef.current);
    let tries = 0;
    resultPollRef.current = setInterval(async () => {
      tries += 1;
      try {
        const qr = await api.getQualityReport(session.id);
        if (qr.processing_status === "done") {
          if (resultPollRef.current) clearInterval(resultPollRef.current);
          resultPollRef.current = null;
          try {
            const out = await api.getOutputs(session.id);
            setOutputs(out.outputs.filter((o) => o.available && o.url));
          } catch {
            // outputs are best-effort; report still shows
          }
          setReport(qr.report);
          setResultPhase("ready");
          setMessage(null);
        } else if (qr.processing_status === "failed") {
          if (resultPollRef.current) clearInterval(resultPollRef.current);
          resultPollRef.current = null;
          setResultPhase("failed");
          setMessage("Processing failed on the server. Please try again.");
        }
      } catch {
        // transient; keep polling
      }
      if (tries > 60 && resultPollRef.current) {
        clearInterval(resultPollRef.current);
        resultPollRef.current = null;
        setResultPhase("failed");
        setMessage("Still processing — check back in a moment.");
      }
    }, 4000);
  }

  // Play (or stop) one of the result mixes through the phone speaker.
  async function onPlayOutput(item: OutputItem) {
    try {
      if (!item.url) return;
      // Tapping the currently-playing item stops it.
      if (playingRole === item.role) {
        await stopPlayback();
        return;
      }
      await stopPlayback();
      await Audio.setAudioModeAsync({ playsInSilentModeIOS: true });
      const { sound } = await Audio.Sound.createAsync(
        { uri: item.url },
        { shouldPlay: true },
      );
      soundRef.current = sound;
      setPlayingRole(item.role);
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          stopPlayback().catch(() => undefined);
        }
      });
    } catch (e) {
      setMessage(describeError(e));
    }
  }

  async function stopPlayback() {
    const s = soundRef.current;
    soundRef.current = null;
    setPlayingRole(null);
    if (s) {
      try {
        await s.stopAsync();
      } catch {
        // ignore
      }
      await s.unloadAsync().catch(() => undefined);
    }
  }

  function onDone() {
    // Leaving the session for good: forget the active-session recovery marker.
    clearActiveSession().catch(() => undefined);
    navigation.popToTop();
  }

  const mmss = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(
    elapsed % 60,
  ).padStart(2, "0")}`;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
    <ScrollView
      style={{ flex: 1 }}
      contentContainerStyle={{
        backgroundColor: colors.bg,
        padding: 20,
        paddingBottom: 56,
        flexGrow: 1,
      }}
      keyboardShouldPersistTaps="handled"
    >
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

      {/* Host sees the phones that have joined, live. */}
      {isHost ? (
        <View style={styles.card}>
          <Text style={styles.label}>
            Connected phones ({members.length})
          </Text>
          {members.map((m) => (
            <Text key={m.id} style={[styles.subtitle, { marginBottom: 4 }]}>
              {m.role === "host" ? "★ " : "• "}
              {m.speaker_name}
              {m.role === "host" ? " (you, host)" : ""}
            </Text>
          ))}
          <Text style={[styles.subtitle, { marginBottom: 0, fontSize: 13 }]}>
            Others join with the code above. Start when everyone is in.
          </Text>
        </View>
      ) : null}

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

      {statusSyncError ? <Text style={styles.error}>{statusSyncError}</Text> : null}

      {/* Upload progress + retry indicator. */}
      {phase === "uploading" ? (
        <View style={styles.card}>
          <Text style={styles.label}>
            {uploadAttempt
              ? `Retrying upload (${uploadAttempt.n}/${uploadAttempt.max})…`
              : `Uploading… ${Math.round(uploadPct * 100)}%`}
          </Text>
          <View
            style={{
              height: 10,
              borderRadius: 5,
              backgroundColor: colors.border,
              overflow: "hidden",
              marginTop: 8,
            }}
          >
            <View
              style={{
                height: 10,
                width: `${Math.max(4, Math.round(uploadPct * 100))}%`,
                backgroundColor: colors.primary,
              }}
            />
          </View>
        </View>
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
          <Pressable style={styles.buttonGhost} onPress={onGuestStartNow}>
            <Text style={styles.buttonGhostText}>Start this phone now (fallback)</Text>
          </Pressable>
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
        <Pressable
          style={[styles.button, resultPhase === "processing" && { opacity: 0.5 }]}
          onPress={onProcess}
          disabled={resultPhase === "processing"}
        >
          <Text style={styles.buttonText}>
            {resultPhase === "processing"
              ? "Mixing…"
              : resultPhase === "ready"
                ? "Re-run mix & transcribe"
                : "Mix & transcribe (host)"}
          </Text>
        </Pressable>
      ) : null}

      {isHost && resultPhase === "ready" ? (
        <ResultPanel
          report={report}
          outputs={outputs}
          playingRole={playingRole}
          onPlay={onPlayOutput}
        />
      ) : null}

      {phase === "uploaded" ? (
        <Pressable
          style={styles.buttonGhost}
          onPress={onDone}
        >
          <Text style={styles.buttonGhostText}>Done</Text>
        </Pressable>
      ) : null}
    </ScrollView>
    </SafeAreaView>
  );
}

// Reads a quality field's value into a colour (good / ok / poor) for the report.
function tone(field: string, value: string): string {
  const good = new Set(["Excellent", "Strong", "Low", "No"]);
  const ok = new Set(["Good", "Medium"]);
  if (good.has(value)) return colors.success;
  if (ok.has(value)) return colors.primary;
  return colors.danger;
}

// Host result panel: plain-language quality report + tap-to-play preset mixes,
// all inside the app (the same data the web dashboard shows).
function ResultPanel({
  report,
  outputs,
  playingRole,
  onPlay,
}: {
  report: QualityReport | null;
  outputs: OutputItem[];
  playingRole: string | null;
  onPlay: (item: OutputItem) => void;
}) {
  const rows: { label: string; value: string }[] = report
    ? [
        { label: "Timing / sync", value: report.sync },
        { label: "Stereo width", value: report.stereo_width },
        { label: "Background noise", value: report.noise },
        { label: "Clipping", value: report.clipping },
        { label: "Duplicate sound", value: report.duplicate },
      ]
    : [];

  return (
    <View style={{ marginTop: 8 }}>
      <Text style={[styles.title, { fontSize: 20, marginTop: 8 }]}>Your mix is ready</Text>

      {report ? (
        <View style={styles.card}>
          <View
            style={{
              flexDirection: "row",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 10,
            }}
          >
            <Text style={{ color: colors.muted, fontSize: 14 }}>Quality score</Text>
            <Text
              style={{
                color:
                  report.score >= 75
                    ? colors.success
                    : report.score >= 50
                      ? colors.primary
                      : colors.danger,
                fontSize: 28,
                fontWeight: "800",
              }}
            >
              {report.score}
              <Text style={{ color: colors.muted, fontSize: 14 }}>/100</Text>
            </Text>
          </View>
          {rows.map((r) => (
            <View
              key={r.label}
              style={{
                flexDirection: "row",
                justifyContent: "space-between",
                paddingVertical: 4,
              }}
            >
              <Text style={{ color: colors.muted, fontSize: 14 }}>{r.label}</Text>
              <Text style={{ color: tone(r.label, r.value), fontWeight: "700" }}>
                {r.value}
              </Text>
            </View>
          ))}
        </View>
      ) : (
        <View style={styles.card}>
          <Text style={{ color: colors.muted }}>
            The mix is ready. (Quality report not available for this take.)
          </Text>
        </View>
      )}

      {outputs.length > 0 ? (
        <>
          <Text style={styles.label}>Listen (tap to play / stop)</Text>
          {outputs.map((o) => (
            <Pressable
              key={o.role}
              style={[
                styles.card,
                {
                  flexDirection: "row",
                  alignItems: "center",
                  marginBottom: 10,
                  borderColor:
                    playingRole === o.role ? colors.primary : colors.border,
                },
              ]}
              onPress={() => onPlay(o)}
            >
              <Text style={{ fontSize: 22, marginRight: 12 }}>
                {playingRole === o.role ? "⏸" : "▶"}
              </Text>
              <Text style={{ color: colors.text, fontSize: 16, fontWeight: "600" }}>
                {o.label}
              </Text>
            </Pressable>
          ))}
        </>
      ) : (
        <View style={styles.card}>
          <Text style={{ color: colors.text, fontSize: 15, fontWeight: "600" }}>
            No playable files are showing yet
          </Text>
          <Text style={{ color: colors.muted, marginTop: 6, lineHeight: 20 }}>
            The mix finished, but the playable output list is still empty on this
            screen. If this stays empty, leave this page and re-open the session after
            processing finishes.
          </Text>
        </View>
      )}
    </View>
  );
}
