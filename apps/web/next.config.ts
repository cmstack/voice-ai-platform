import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

function origin(url: string | undefined): string[] {
  if (!url) return [];
  try {
    const u = new URL(url);
    const ws = u.protocol === "https:" ? `wss://${u.host}` : `ws://${u.host}`;
    return [u.origin, ws]; // HTTP API + WebSocket (Supabase Realtime)
  } catch {
    return [];
  }
}

const connect = ["'self'", ...origin(process.env.NEXT_PUBLIC_SUPABASE_URL), ...origin(process.env.NEXT_PUBLIC_VOICE_ENGINE_URL)];

// Next.js injects inline bootstrap scripts, so script-src needs 'unsafe-inline' without a nonce setup;
// dev mode (React Refresh) also needs 'unsafe-eval'. blob: is for the simulator's AudioWorklet (the
// Pipecat WavRecorder loads its worklet from a same-origin blob URL). Framing is fully blocked.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' blob:${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "media-src 'self' blob:",
  `connect-src ${connect.join(" ")}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "microphone=(self), camera=(), geolocation=(), payment=()" },
  ...(isDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
