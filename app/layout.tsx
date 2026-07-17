import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const base = new URL(`${protocol}://${host}`);
  const description = "Observe, measure, and tune multi-hop Precision Time Protocol clock cascades from one exacting control room.";

  return {
    metadataBase: base,
    title: "PTPBox — Precision Time Lab",
    description,
    openGraph: {
      type: "website",
      title: "PTPBox — Precision Time Lab",
      description,
      images: [{ url: new URL("/og.png", base).toString(), width: 1200, height: 630, alt: "PTPBox clock cascade with nanosecond telemetry traces" }],
    },
    twitter: {
      card: "summary_large_image",
      title: "PTPBox — Precision Time Lab",
      description,
      images: [new URL("/og.png", base).toString()],
    },
  };
}

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#080d12",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
