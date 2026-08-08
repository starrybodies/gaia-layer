import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Gaia — Ecological Intelligence Layer",
  description:
    "Validated, provenance-tracked ecological ground truth for wildfire substrate " +
    "condition. Every number carries its confidence, its validation status, and a chain " +
    "back to the satellite scenes it came from.",
  openGraph: {
    title: "Gaia — Ecological Intelligence Layer",
    description:
      "Agent-native ecological ground truth. No number without provenance.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrains.variable}`}>
      <body className="bg-void text-text min-h-screen antialiased">{children}</body>
    </html>
  );
}
