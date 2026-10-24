"use client";

import { useEffect, useState } from "react";
import { CloudOff, Plus, RotateCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { TopNav } from "@/components/top-nav";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { supabase } from "@/lib/supabase";
import { NOVA_VOICES, type AgentConfig } from "@/lib/types";

const TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles", "Europe/London", "UTC"];

type Draft = Omit<AgentConfig, "objection_handlers" | "id" | "updated_at"> & { objection_handlers: [string, string][] };

export default function SettingsPage() {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setLoadError(null);
    supabase().from("agent_config").select("*").eq("id", 1).single().then(({ data, error }) => {
      if (error) return setLoadError(error.message);
      const cfg = data as AgentConfig;
      const initial = { ...cfg, objection_handlers: Object.entries(cfg.objection_handlers ?? {}) };
      setDraft(initial);
      setSaved(JSON.stringify(initial));
    });
  }, [attempt]);

  const dirty = draft !== null && JSON.stringify(draft) !== saved;

  // Warn before leaving the page with unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (loadError) {
    return (
      <div className="min-h-dvh">
        <TopNav />
        <main className="mx-auto mt-16 flex max-w-sm flex-col items-center gap-2 px-4 text-center" role="alert">
          <CloudOff className="size-5 text-danger-ink" strokeWidth={1.75} />
          <p className="text-sm font-semibold">Couldn&apos;t load the agent configuration</p>
          <p className="text-xs text-muted-foreground">{loadError}. Check that Supabase is running, then try again.</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => setAttempt((a) => a + 1)}>
            <RotateCw strokeWidth={1.75} /> Try again
          </Button>
        </main>
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="min-h-dvh">
        <TopNav />
        <main className="mx-auto max-w-3xl space-y-6 p-4 sm:p-8" aria-busy="true" aria-label="Loading agent configuration">
          <div className="h-7 w-64 rounded bg-muted motion-safe:animate-pulse" />
          {[180, 220, 160, 200].map((h, i) => (
            <div key={i} style={{ height: h }} className="rounded-lg border border-border bg-card motion-safe:animate-pulse" />
          ))}
        </main>
      </div>
    );
  }

  const start = Number(draft.business_hours_start);
  const end = Number(draft.business_hours_end);
  const hoursError =
    !Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > 24 || start >= end
      ? "Use whole hours from 0 to 24, with the end after the start."
      : null;
  const eventTypeError = !draft.cal_event_type_id || draft.cal_event_type_id < 1 ? "Enter your Cal.com event type ID." : null;
  const invalid = !!(hoursError || eventTypeError);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft({ ...draft, [key]: value });

  async function save() {
    if (!draft || invalid) return;
    setSaving(true);
    const { error } = await supabase()
      .from("agent_config")
      .update({
        agent_name: draft.agent_name,
        company_name: draft.company_name,
        nova_voice_id: draft.nova_voice_id,
        outbound_greeting: draft.outbound_greeting,
        inbound_greeting: draft.inbound_greeting,
        qualification_questions: draft.qualification_questions.map((q) => q.trim()).filter(Boolean),
        objection_handlers: Object.fromEntries(draft.objection_handlers.filter(([k, v]) => k.trim() && v.trim()).map(([k, v]) => [k.trim(), v.trim()])),
        human_transfer_phone: draft.human_transfer_phone || null,
        cal_event_type_id: Number(draft.cal_event_type_id),
        timezone: draft.timezone,
        business_hours_start: Number(draft.business_hours_start),
        business_hours_end: Number(draft.business_hours_end),
        record_calls: draft.record_calls,
        recording_disclosure: draft.recording_disclosure.trim() || "This call may be recorded.",
      })
      .eq("id", 1);
    setSaving(false);
    if (error) return toast.error(error.message);
    setSaved(JSON.stringify(draft));
    toast.success("Saved. New calls use this configuration.");
  }

  return (
    <div className="min-h-dvh">
      <TopNav />
      <main className="mx-auto max-w-3xl space-y-6 p-4 pb-28 sm:p-8 sm:pb-28">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agent & Prompt Studio</h1>
          <p className="mt-2 max-w-[65ch] text-sm leading-relaxed text-muted-foreground">
            How the agent sounds and what it says. Greetings can use{" "}
            <code className="rounded bg-muted px-1 font-mono text-[12px] text-foreground">{"{{full_name}}"}</code>,{" "}
            <code className="rounded bg-muted px-1 font-mono text-[12px] text-foreground">{"{{agent_name}}"}</code> and{" "}
            <code className="rounded bg-muted px-1 font-mono text-[12px] text-foreground">{"{{company_name}}"}</code>.
          </p>
        </div>

        <Card>
          <CardHeader><CardTitle className="font-semibold">Identity & voice</CardTitle></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label="Agent name">
              {(p) => (
                <Input {...p} value={draft.agent_name} onChange={(e) => set("agent_name", e.target.value)} />
              )}
            </Field>
            <Field label="Company name">
              {(p) => (
                <Input {...p} value={draft.company_name} onChange={(e) => set("company_name", e.target.value)} />
              )}
            </Field>
            <Field label="Nova Sonic voice" className="sm:col-span-2">
              {(p) => (
                <Select value={draft.nova_voice_id} onValueChange={(v) => set("nova_voice_id", v)}>
                  <SelectTrigger className="w-full" {...p}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {NOVA_VOICES.map((v) => <SelectItem key={v.id} value={v.id}>{v.label}</SelectItem>)}
                    {!NOVA_VOICES.some((v) => v.id === draft.nova_voice_id) && (
                      <SelectItem value={draft.nova_voice_id}>{draft.nova_voice_id}</SelectItem>
                    )}
                  </SelectContent>
                </Select>
              )}
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="font-semibold">Greetings</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <Field label="Outbound greeting (speed-to-lead)">
              {(p) => (
                <Textarea {...p} rows={3} value={draft.outbound_greeting} onChange={(e) => set("outbound_greeting", e.target.value)} />
              )}
            </Field>
            <Field label="Inbound greeting (receptionist)">
              {(p) => (
                <Textarea {...p} rows={2} value={draft.inbound_greeting} onChange={(e) => set("inbound_greeting", e.target.value)} />
              )}
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="font-semibold">Qualification questions</CardTitle>
            <CardDescription>The agent asks these naturally, one at a time.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {draft.qualification_questions.map((q, i) => (
              <div key={i} className="flex gap-2">
                <Input aria-label={`Question ${i + 1}`} value={q} onChange={(e) => set("qualification_questions", draft.qualification_questions.map((x, j) => (j === i ? e.target.value : x)))} />
                <Button variant="ghost" size="icon" aria-label={`Remove question ${i + 1}`} onClick={() => set("qualification_questions", draft.qualification_questions.filter((_, j) => j !== i))}>
                  <Trash2 strokeWidth={1.75} />
                </Button>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={() => set("qualification_questions", [...draft.qualification_questions, ""])}>
              <Plus strokeWidth={1.75} /> Add question
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="font-semibold">Objection handlers</CardTitle>
            <CardDescription>Objection key (e.g. too_expensive) and the agent&apos;s response.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {draft.objection_handlers.map(([k, v], i) => (
              <div key={i} className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[10rem_1fr_auto]">
                <Input placeholder="too_expensive" aria-label={`Objection ${i + 1} key`} value={k}
                  onChange={(e) => set("objection_handlers", draft.objection_handlers.map((p, j) => (j === i ? [e.target.value, p[1]] : p)))} />
                <Textarea className="order-last col-span-2 sm:order-none sm:col-span-1" rows={2} placeholder="What the agent says" aria-label={`Objection ${i + 1} response`} value={v}
                  onChange={(e) => set("objection_handlers", draft.objection_handlers.map((p, j) => (j === i ? [p[0], e.target.value] : p)))} />
                <Button variant="ghost" size="icon" aria-label={`Remove objection ${i + 1}`} onClick={() => set("objection_handlers", draft.objection_handlers.filter((_, j) => j !== i))}>
                  <Trash2 strokeWidth={1.75} />
                </Button>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={() => set("objection_handlers", [...draft.objection_handlers, ["", ""]])}>
              <Plus strokeWidth={1.75} /> Add objection
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="font-semibold">Calendar, transfer & calling hours</CardTitle></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label="Cal.com event type ID" error={eventTypeError}>
              {(p) => (
                <Input {...p} type="number" value={draft.cal_event_type_id} onChange={(e) => set("cal_event_type_id", Number(e.target.value))} />
              )}
            </Field>
            <Field label="Human transfer phone">
              {(p) => (
                <Input {...p} placeholder="+17135550100" value={draft.human_transfer_phone ?? ""} onChange={(e) => set("human_transfer_phone", e.target.value)} />
              )}
            </Field>
            <Field label="Timezone">
              {(p) => (
                <Select value={draft.timezone} onValueChange={(v) => set("timezone", v)}>
                  <SelectTrigger className="w-full" {...p}><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {[...new Set([draft.timezone, ...TIMEZONES])].map((tz) => <SelectItem key={tz} value={tz}>{tz}</SelectItem>)}
                  </SelectContent>
                </Select>
              )}
            </Field>
            <div className="grid grid-cols-2 items-start gap-2">
              <Field label="Redials from (hour)" invalid={!!hoursError}>
                {(p) => (
                  <Input {...p} aria-describedby={hoursError ? "hours-error" : undefined} type="number" min={0} max={23} value={draft.business_hours_start} onChange={(e) => set("business_hours_start", Number(e.target.value))} />
                )}
              </Field>
              <Field label="Until (hour)" invalid={!!hoursError}>
                {(p) => (
                  <Input {...p} aria-describedby={hoursError ? "hours-error" : undefined} type="number" min={1} max={24} value={draft.business_hours_end} onChange={(e) => set("business_hours_end", Number(e.target.value))} />
                )}
              </Field>
              {hoursError && <p id="hours-error" className="col-span-2 text-xs text-danger-ink">{hoursError}</p>}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="font-semibold">Call recording</CardTitle>
            <CardDescription>
              Off by default. Many places require every party to consent before a call is recorded, so when this is
              on the agent reads the notice below right after its opening line.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="flex items-center justify-between gap-4 rounded-lg border border-border p-3 text-sm">
              <span className="font-semibold" id="record-calls-label">Record outbound phone calls</span>
              <Switch aria-labelledby="record-calls-label" checked={draft.record_calls} onCheckedChange={(v) => set("record_calls", v)} />
            </label>
            <Field label="Recording notice">
              {(p) => (
                <Textarea {...p} rows={2} value={draft.recording_disclosure} disabled={!draft.record_calls}
                onChange={(e) => set("recording_disclosure", e.target.value)} />
              )}
            </Field>
          </CardContent>
        </Card>

        <div className="fixed inset-x-0 bottom-0 z-10 border-t border-border bg-card/90 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-card/75">
          <div className="mx-auto flex max-w-3xl items-center justify-end gap-3">
            <span className="text-xs text-muted-foreground" aria-live="polite">
              {invalid ? "Fix the highlighted fields to save" : dirty ? "Unsaved changes" : "All changes saved"}
            </span>
            <Button onClick={save} disabled={saving || !dirty || invalid}>
              {saving ? "Saving…" : "Save configuration"}
            </Button>
          </div>
        </div>
      </main>
    </div>
  );
}

