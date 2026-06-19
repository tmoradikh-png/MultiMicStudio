import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Pressable,
  SafeAreaView,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";
import {
  MediaStream,
  RTCIceCandidate,
  RTCPeerConnection,
  RTCSessionDescription,
  mediaDevices,
} from "react-native-webrtc";
import { signalingUrl } from "../config";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "P2PLive">;

// Proof-of-concept: TRUE peer-to-peer live audio between two phones using native
// WebRTC (react-native-webrtc). The backend is used ONLY to relay SDP/ICE through
// /live/ws/{room} — the audio itself flows phone-to-phone and never touches the
// server. Requires an Expo Dev Build (Expo Go cannot load the native module).
//
//   • "Mic"     phone = publisher: captures the mic and sends the offer.
//   • "Speaker" phone = listener:  answers and plays the remote audio out of its
//                                  current output (Bluetooth / wired / speaker).

type Role = "mic" | "speaker";
type Phase = "idle" | "connecting" | "live" | "error";

// ICE servers: a public STUN is enough on the same Wi-Fi/LAN. Cross-network (4G)
// needs a TURN server added on the backend (settings.live_ice_servers).
const ICE_SERVERS = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };

export default function P2PLiveScreen({ route, navigation }: Props) {
  const [role, setRole] = useState<Role>(route.params?.role ?? "speaker");
  const [room, setRoom] = useState(route.params?.room ?? "");
  const [name, setName] = useState(route.params?.name ?? "");
  const [phase, setPhase] = useState<Phase>("idle");
  const [connState, setConnState] = useState("new");
  const [iceState, setIceState] = useState("new");
  const [rttMs, setRttMs] = useState<number | null>(null);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const remoteStreamRef = useRef<MediaStream | null>(null);
  const statsTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const offerSentRef = useRef(false);
  // ICE candidates can arrive before the remote description is set; buffer them.
  const pendingIce = useRef<RTCIceCandidate[]>([]);
  const remoteSetRef = useRef(false);

  const sendSignal = useCallback((payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "signal", ...payload }));
    }
  }, []);

  const drainPendingIce = useCallback(async () => {
    const pc = pcRef.current;
    if (!pc) return;
    while (pendingIce.current.length) {
      const c = pendingIce.current.shift();
      if (c) {
        try {
          await pc.addIceCandidate(c);
        } catch {
          // best-effort; a stale candidate is non-fatal
        }
      }
    }
  }, []);

  const cleanup = useCallback(() => {
    if (statsTimer.current) {
      clearInterval(statsTimer.current);
      statsTimer.current = null;
    }
    if (pcRef.current) {
      try {
        pcRef.current.close();
      } catch {
        /* ignore */
      }
      pcRef.current = null;
    }
    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach((t) => t.stop());
      localStreamRef.current = null;
    }
    remoteStreamRef.current = null;
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
    offerSentRef.current = false;
    remoteSetRef.current = false;
    pendingIce.current = [];
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const startStats = useCallback(() => {
    if (statsTimer.current) clearInterval(statsTimer.current);
    statsTimer.current = setInterval(async () => {
      const pc = pcRef.current;
      if (!pc) return;
      try {
        const stats = await pc.getStats();
        let nextRtt: number | null = null;
        let nextLevel: number | null = null;
        stats.forEach((report: any) => {
          if (
            report.type === "candidate-pair" &&
            report.state === "succeeded" &&
            typeof report.currentRoundTripTime === "number"
          ) {
            nextRtt = Math.round(report.currentRoundTripTime * 1000);
          }
          if (typeof report.audioLevel === "number") {
            nextLevel = report.audioLevel;
          }
        });
        if (nextRtt != null) setRttMs(nextRtt);
        if (nextLevel != null) setLevel(nextLevel);
      } catch {
        /* getStats not fatal */
      }
    }, 1000);
  }, []);

  const buildPeerConnection = useCallback(() => {
    const pc = new RTCPeerConnection(ICE_SERVERS);

    (pc as any).onicecandidate = (event: any) => {
      if (event.candidate) {
        sendSignal({ kind: "ice", candidate: event.candidate });
      }
    };
    (pc as any).onconnectionstatechange = () => {
      const s = (pc as any).connectionState ?? "unknown";
      setConnState(s);
      if (s === "connected") setPhase("live");
      if (s === "failed" || s === "closed") {
        setError("Peer connection lost.");
        setPhase("error");
      }
    };
    (pc as any).oniceconnectionstatechange = () => {
      setIceState((pc as any).iceConnectionState ?? "unknown");
    };
    (pc as any).ontrack = (event: any) => {
      // Remote audio arrives here on the Speaker phone; attaching the stream is
      // enough for react-native-webrtc to play it out of the current audio route.
      const [stream] = event.streams;
      if (stream) remoteStreamRef.current = stream;
    };
    return pc;
  }, [sendSignal]);

  const maybeSendOffer = useCallback(async () => {
    // Mic side creates the offer once a Speaker is actually present, so the offer
    // is never relayed into an empty room and lost.
    if (role !== "mic" || offerSentRef.current) return;
    const pc = pcRef.current;
    if (!pc) return;
    offerSentRef.current = true;
    try {
      const offer = await pc.createOffer({});
      await pc.setLocalDescription(offer);
      sendSignal({ kind: "offer", sdp: pc.localDescription });
    } catch (e: any) {
      offerSentRef.current = false;
      setError(`Failed to create offer: ${e?.message ?? e}`);
      setPhase("error");
    }
  }, [role, sendSignal]);

  const handleSignal = useCallback(
    async (msg: any) => {
      const pc = pcRef.current;
      if (!pc) return;
      try {
        if (msg.kind === "offer" && role === "speaker") {
          await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
          remoteSetRef.current = true;
          await drainPendingIce();
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          sendSignal({ kind: "answer", sdp: pc.localDescription, to: msg.from });
        } else if (msg.kind === "answer" && role === "mic") {
          await pc.setRemoteDescription(new RTCSessionDescription(msg.sdp));
          remoteSetRef.current = true;
          await drainPendingIce();
        } else if (msg.kind === "ice" && msg.candidate) {
          const candidate = new RTCIceCandidate(msg.candidate);
          if (remoteSetRef.current) {
            await pc.addIceCandidate(candidate);
          } else {
            pendingIce.current.push(candidate);
          }
        }
      } catch (e: any) {
        setError(`Signaling error: ${e?.message ?? e}`);
      }
    },
    [role, drainPendingIce, sendSignal],
  );

  const connect = useCallback(async () => {
    if (room.trim().length < 3) {
      setError("Enter a room code (min 3 characters).");
      return;
    }
    setError(null);
    setPhase("connecting");
    cleanup();

    try {
      const pc = buildPeerConnection();
      pcRef.current = pc;

      // Mic phone captures audio and adds it to the connection BEFORE offering.
      if (role === "mic") {
        // Turn ON WebRTC's built-in, on-device DSP: noise suppression, automatic
        // gain control, acoustic echo cancellation and a high-pass filter. This is
        // the first edge-AI/DSP layer and runs entirely on the phone — no server.
        // Heavier ML noise suppression (RNNoise/TFLite) can be layered later where
        // these built-ins are not enough.
        const stream = await mediaDevices.getUserMedia({
          audio: {
            noiseSuppression: true,
            echoCancellation: true,
            autoGainControl: true,
          } as any,
          video: false,
        });
        localStreamRef.current = stream as MediaStream;
        stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      }

      const wsRole = role === "mic" ? "publisher" : "listener";
      const ws = new WebSocket(signalingUrl(room, wsRole, name));
      wsRef.current = ws;

      ws.onmessage = (event: any) => {
        let data: any;
        try {
          data = JSON.parse(event.data);
        } catch {
          return; // ignore non-JSON (no audio is ever sent over this socket)
        }
        if (data.type === "signal") {
          void handleSignal(data);
        } else if (data.type === "roster") {
          // When the opposite role appears, the Mic side kicks off negotiation.
          const peers =
            role === "mic" ? data.listeners ?? [] : data.publishers ?? [];
          if (peers.length > 0) void maybeSendOffer();
        }
      };
      ws.onerror = () => {
        setError("Signaling connection failed. Check the room/server.");
        setPhase("error");
      };
      ws.onclose = () => {
        if (phase !== "error") setPhase("idle");
      };

      startStats();
    } catch (e: any) {
      setError(`Could not start: ${e?.message ?? e}`);
      setPhase("error");
      cleanup();
    }
  }, [
    room,
    name,
    role,
    cleanup,
    buildPeerConnection,
    handleSignal,
    maybeSendOffer,
    startStats,
    phase,
  ]);

  const stop = useCallback(() => {
    cleanup();
    setPhase("idle");
    setConnState("new");
    setIceState("new");
    setRttMs(null);
    setLevel(0);
  }, [cleanup]);

  const busy = phase === "connecting" || phase === "live";
  const levelPct = Math.min(100, Math.round(level * 140));

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView
        contentContainerStyle={{ padding: 20, gap: 16, flexGrow: 1 }}
        keyboardShouldPersistTaps="handled"
      >
        <Text style={styles.title}>P2P Live (WebRTC)</Text>
        <Text style={{ color: colors.muted }}>
          Direct phone-to-phone audio. The server only relays connection setup —
          no audio is uploaded.
        </Text>

        <View style={{ flexDirection: "row", gap: 10 }}>
          {(["mic", "speaker"] as Role[]).map((r) => (
            <Pressable
              key={r}
              disabled={busy}
              onPress={() => setRole(r)}
              style={{
                flex: 1,
                paddingVertical: 14,
                borderRadius: 12,
                alignItems: "center",
                backgroundColor: role === r ? colors.primary : colors.card,
                opacity: busy ? 0.5 : 1,
              }}
            >
              <Text style={{ color: colors.text, fontWeight: "700" }}>
                {r === "mic" ? "Use as Mic" : "Use as Speaker"}
              </Text>
            </Pressable>
          ))}
        </View>

        <Text style={{ color: colors.muted }}>Room code</Text>
        <TextInput
          value={room}
          editable={!busy}
          onChangeText={setRoom}
          autoCapitalize="characters"
          placeholder="e.g. ABCD"
          placeholderTextColor={colors.muted}
          style={styles.input}
        />

        <Text style={{ color: colors.muted }}>Display name</Text>
        <TextInput
          value={name}
          editable={!busy}
          onChangeText={setName}
          placeholder={role === "mic" ? "Mic 1" : "Speaker"}
          placeholderTextColor={colors.muted}
          style={styles.input}
        />

        {!busy ? (
          <Pressable style={styles.button} onPress={connect}>
            <Text style={styles.buttonText}>Connect</Text>
          </Pressable>
        ) : (
          <Pressable
            style={[styles.button, { backgroundColor: colors.danger }]}
            onPress={stop}
          >
            <Text style={styles.buttonText}>Stop</Text>
          </Pressable>
        )}

        <View style={{ gap: 6, marginTop: 8 }}>
          <Row label="Phase" value={phase} />
          <Row label="Connection" value={connState} />
          <Row label="ICE" value={iceState} />
          <Row label="Media RTT" value={rttMs == null ? "—" : `${rttMs} ms`} />
          <View>
            <Text style={{ color: colors.muted }}>Mic level</Text>
            <View
              style={{
                height: 10,
                borderRadius: 5,
                backgroundColor: colors.card,
                overflow: "hidden",
                marginTop: 4,
              }}
            >
              <View
                style={{
                  width: `${levelPct}%`,
                  height: "100%",
                  backgroundColor: colors.primary,
                }}
              />
            </View>
          </View>
        </View>

        {error ? (
          <Text style={{ color: colors.danger, marginTop: 8 }}>{error}</Text>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
      <Text style={{ color: colors.muted }}>{label}</Text>
      <Text style={{ color: colors.text, fontWeight: "600" }}>{value}</Text>
    </View>
  );
}
