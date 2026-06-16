import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MultiMic Studio — Dashboard",
  description: "Manage, play, and transcribe your multi-phone recordings.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
