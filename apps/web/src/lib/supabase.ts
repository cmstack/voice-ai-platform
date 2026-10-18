"use client";

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | undefined;

export function supabase(): SupabaseClient {
  client ??= createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
  return client;
}

export const VOICE_ENGINE_URL = process.env.NEXT_PUBLIC_VOICE_ENGINE_URL ?? "http://localhost:8000";

/** Call the voice engine with the signed-in user's access token. */
export async function voiceEngineFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { data } = await supabase().auth.getSession();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (data.session) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  return fetch(`${VOICE_ENGINE_URL}${path}`, { ...init, headers });
}

export async function accessToken(): Promise<string | null> {
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? null;
}
