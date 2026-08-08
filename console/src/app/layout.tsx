import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gaia — Ecological Intelligence Layer",
  description:
    "Validated, provenance-tracked ecological ground truth for wildfire substrate condition.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-base-950 text-base-200 antialiased">{children}</body>
    </html>
  );
}
