"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AudioLines } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("error") === "not_admin") {
      setError("This account doesn't have admin access. Ask the operator to grant it.");
    }
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await supabase().auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) return setError(error.message === "Invalid login credentials" ? "That email and password don't match." : error.message);
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="relative flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm">
        <span className="flex size-10 items-center justify-center rounded-lg bg-brand text-brand-foreground">
          <AudioLines className="size-5" strokeWidth={1.75} />
        </span>
        <h1 className="mt-6 text-2xl font-semibold tracking-tight">Sign in to Voice Agent CRM</h1>
        <p className="mt-2 text-sm text-muted-foreground">Your pipeline, live calls and bookings in one place.</p>

        <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!!error} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={!!error} aria-describedby={error ? "login-error" : undefined} />
            {error && <p id="login-error" role="alert" className="text-sm text-danger-ink">{error}</p>}
          </div>
          <Button type="submit" size="lg" className="w-full" disabled={busy || !email || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
    </main>
  );
}
