"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { voiceEngineFetch } from "@/lib/supabase";

export function NewLeadDialog() {
  const [open, setOpen] = useState(false);
  const [phone, setPhone] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [interest, setInterest] = useState("");
  const [dial, setDial] = useState(true);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [phoneError, setPhoneError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setPhoneError(null);
    try {
      const res = await voiceEngineFetch("/api/leads", {
        method: "POST",
        body: JSON.stringify({
          phone,
          full_name: fullName || null,
          email: email || null,
          custom_fields: interest ? { interest } : {},
          trigger_call_immediately: dial,
          consent,
          consent_source: consent ? "confirmed by operator in dashboard" : null,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (isPhoneError(body.detail)) {
          setPhoneError("That doesn't look like a phone number. Use international format, or a 10-digit US number.");
          return;
        }
        throw new Error(errorMessage(body.detail, res.status));
      }
      if (body.blocked_reason) {
        const when = body.next_attempt_at ? ` Next attempt: ${new Date(body.next_attempt_at).toLocaleString()}.` : "";
        toast.warning(`Lead created but not dialed: ${body.blocked_reason}.${when}`);
      } else {
        toast.success(
          body.dialed ? "Lead created. Dialing now…"
            : dial ? "Lead created. Telephony is off, so use the browser simulator to call."
            : "Lead created",
        );
      }
      setOpen(false);
      setPhone(""); setFullName(""); setEmail(""); setInterest(""); setConsent(false);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setPhoneError(null); }}>
      <DialogTrigger asChild>
        <Button variant="outline" aria-label="New Lead / Dial"><Plus strokeWidth={1.75} /><span className="hidden md:inline">New Lead / Dial</span></Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle className="font-semibold">New lead</DialogTitle>
            <DialogDescription>Adds a card to the pipeline. Only the phone number is required.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="lead-phone">Phone</Label>
            <Input id="lead-phone" required inputMode="tel" autoComplete="tel" placeholder="+1 713 555 0199" aria-describedby="lead-phone-help"
              aria-invalid={phoneError ? true : undefined} value={phone} onChange={(e) => { setPhone(e.target.value); setPhoneError(null); }} />
            <p id="lead-phone-help" className={phoneError ? "text-xs text-danger-ink" : "text-xs text-muted-foreground"}>
              {phoneError ?? "International format, or a 10-digit US number."}
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-name">Full name</Label>
            <Input id="lead-name" autoComplete="off" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-email">Email</Label>
            <Input id="lead-email" type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-interest">Interest</Label>
            <Input id="lead-interest" placeholder="Consultation" value={interest} onChange={(e) => setInterest(e.target.value)} />
          </div>
          <label className="flex items-center justify-between gap-4 rounded-lg border border-border p-3 text-sm">
            <span className="flex flex-col gap-0.5">
              <span className="font-semibold" id="lead-dial-label">Call immediately</span>
              <span className="text-xs text-muted-foreground" id="lead-dial-help">The agent dials within seconds (speed-to-lead).</span>
            </span>
            <Switch checked={dial} onCheckedChange={setDial} aria-labelledby="lead-dial-label" aria-describedby="lead-dial-help" />
          </label>
          <label className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-[var(--brand)]"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              aria-describedby="lead-consent-help"
            />
            <span className="flex flex-col gap-0.5">
              <span className="font-semibold">The lead agreed to be called</span>
              <span id="lead-consent-help" className="text-xs text-muted-foreground">
                Required before the agent will dial a real phone number. It is saved with the contact.
              </span>
            </span>
          </label>
          <DialogFooter>
            <Button type="submit" variant={dial ? "brand" : "default"} disabled={busy}>{busy ? "Saving…" : dial ? "Create & dial" : "Create lead"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function isPhoneError(detail: unknown): boolean {
  return Array.isArray(detail) && detail.some((d: { loc?: unknown[] }) => d.loc?.at(-1) === "phone");
}

/** Turns a FastAPI error body (a string, or a list of validation errors) into one readable sentence. */
function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const field = first.loc?.at(-1);
    const msg = (first.msg ?? "Check the form and try again.").replace(/^Value error, /, "");
    return field ? `${String(field).replaceAll("_", " ")}: ${msg}` : msg;
  }
  if (status === 429) return "Too many requests. Wait a minute and try again.";
  return `The voice engine returned an error (${status}). Try again.`;
}
