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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
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
      const body = await res.json();
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
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
    <Dialog open={open} onOpenChange={setOpen}>
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
            <Input id="lead-phone" required inputMode="tel" autoComplete="tel" placeholder="+1 713 555 0199" aria-describedby="lead-phone-help" value={phone} onChange={(e) => setPhone(e.target.value)} />
            <p id="lead-phone-help" className="text-xs text-muted-foreground">International format, or a 10-digit US number.</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-name">Full name</Label>
            <Input id="lead-name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-email">Email</Label>
            <Input id="lead-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="lead-interest">Interest</Label>
            <Input id="lead-interest" placeholder="Consultation" value={interest} onChange={(e) => setInterest(e.target.value)} />
          </div>
          <label className="flex items-center justify-between gap-4 rounded-lg border border-border p-3 text-sm">
            <span className="flex flex-col gap-0.5">
              <span className="font-semibold">Call immediately</span>
              <span className="text-xs text-muted-foreground">The agent dials within seconds (speed-to-lead).</span>
            </span>
            <Switch checked={dial} onCheckedChange={setDial} />
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
