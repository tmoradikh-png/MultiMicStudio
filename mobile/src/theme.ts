// Shared theme + style constants for a clean, consistent dark UI.
import { StyleSheet } from "react-native";

export const colors = {
  bg: "#0b1020",
  card: "#161c33",
  border: "#27304f",
  text: "#eef1fb",
  muted: "#9aa3c7",
  primary: "#5b8cff",
  danger: "#ff5b6e",
  success: "#3ddc97",
};

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, padding: 20 },
  center: { justifyContent: "center" },
  title: { color: colors.text, fontSize: 26, fontWeight: "700", marginBottom: 6 },
  subtitle: { color: colors.muted, fontSize: 15, marginBottom: 24 },
  label: { color: colors.muted, fontSize: 13, marginBottom: 6, marginTop: 12 },
  input: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: colors.text,
    fontSize: 16,
  },
  button: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 15,
    alignItems: "center",
    marginTop: 20,
  },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  buttonGhost: {
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 12,
  },
  buttonGhostText: { color: colors.primary, fontSize: 15, fontWeight: "600" },
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 18,
    marginBottom: 14,
  },
  code: {
    color: colors.text,
    fontSize: 36,
    fontWeight: "800",
    letterSpacing: 6,
    textAlign: "center",
  },
  error: { color: colors.danger, marginTop: 12, fontSize: 14 },
});
