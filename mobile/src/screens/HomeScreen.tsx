import React from "react";
import { Pressable, Text, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useAuth } from "../context/AuthContext";
import ResumeBanner from "../components/ResumeBanner";
import { API_BASE_URL, API_BASE_URL_IS_DEFAULT } from "../config";
import { colors, styles } from "../theme";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Home">;

export default function HomeScreen({ navigation }: Props) {
  const { signOut } = useAuth();

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>MultiMic Studio</Text>
      <Text style={styles.subtitle}>
        Use the phones you already own as a recording studio.
      </Text>

      <ResumeBanner
        onResume={(params) => navigation.navigate("Record", params)}
      />

      <Pressable
        style={styles.card}
        onPress={() => navigation.navigate("CreateSession")}
      >
        <Text style={[styles.title, { fontSize: 20 }]}>＋ Create a session</Text>
        <Text style={styles.subtitle}>
          You are the host. Share the code so others can join.
        </Text>
      </Pressable>

      <Pressable
        style={styles.card}
        onPress={() => navigation.navigate("JoinSession")}
      >
        <Text style={[styles.title, { fontSize: 20 }]}>↳ Join a session</Text>
        <Text style={styles.subtitle}>
          Enter the 6-character code from the host.
        </Text>
      </Pressable>

      <Pressable
        style={styles.card}
        onPress={() => navigation.navigate("P2PLive")}
      >
        <Text style={[styles.title, { fontSize: 20 }]}>📡 P2P Live (beta)</Text>
        <Text style={styles.subtitle}>
          Direct phone-to-phone live audio. No audio uploaded to the server.
        </Text>
      </Pressable>

      <Pressable style={styles.buttonGhost} onPress={signOut}>
        <Text style={styles.buttonGhostText}>Sign out</Text>
      </Pressable>

      <Text
        style={{ color: colors.muted, fontSize: 12, marginTop: 24, textAlign: "center" }}
      >
        Server: {API_BASE_URL}
        {API_BASE_URL_IS_DEFAULT ? " (local)" : ""}
      </Text>
    </View>
  );
}
