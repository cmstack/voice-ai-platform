"use client";

import { useEffect, useState } from "react";
import { format } from "date-fns";
import { ChevronDown, PhoneOff, Plus, Trash2 } from "lucide-react";
import { LiveDot } from "@/components/kanban-board";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDuration, formatPhone } from "@/lib/format";
import { supabase } from "@/lib/supabase";
import { STAGES, STAGE_LABELS, type CallLog, type CallOutcome, type Contact, type PipelineStage } from "@/lib/types";
import { cn } from "@/lib/utils";

const OUTCOME_VARIANT: Record<CallOutcome, "brand" | "outline" | "muted" | "danger"> = {
  booked: "brand",
  follow_up_requested: "outline",
  voicemail: "muted",
  transferred: "outline",
  not_interested: "danger",
};

interface Props {
  contact: Contact | null;
  callLogVersion: number;
  onClose: () => void;
}

export function ContactDrawer({ contact, callLogVersion, onClose }: Props) {
  const [calls, setCalls] = useState<CallLog[]>([]);
  const contactId = contact?.id;

  useEffect(() => {
    if (!contactId) return;
    let cancelled = false;
    supabase()
      .from("call_logs")
      .select("*")
      .eq("contact_id", contactId)
      .order("created_at", { ascending: false })
      .then(({ data }) => !cancelled && setCalls((data ?? []) as CallLog[]));
    return () => {
      cancelled = true;
    };
  }, [contactId, callLogVersion]);

  return (
    <Sheet open={!!contact} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 p-0 sm:max-w-[480px]">
        {contact && (
          <>
            <SheetHeader className="gap-2 border-b border-border p-4">
              <SheetTitle className="pr-8 text-base font-semibold">{contact.full_name || formatPhone(contact.phone)}</SheetTitle>
              <SheetDescription asChild>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-mono tabular-nums">{formatPhone(contact.phone)}</span>
                  <Badge variant={contact.stage === "appointment_booked" ? "brand" : "outline"}>{STAGE_LABELS[contact.stage]}</Badge>
                  {contact.retry_count > 0 && (
                    <span>{contact.retry_count} {contact.retry_count === 1 ? "retry" : "retries"}</span>
                  )}
                </div>
              </SheetDescription>
            </SheetHeader>
            <Tabs defaultValue="activity" className="flex min-h-0 flex-1 flex-col">
              <TabsList className="mx-4 mt-4 w-auto self-start">
                <TabsTrigger value="activity">Activity & Calls</TabsTrigger>
                <TabsTrigger value="details">Contact Details</TabsTrigger>
              </TabsList>
              <TabsContent value="activity" className="min-h-0 flex-1">
                <ScrollArea className="h-[calc(100vh-150px)] px-4 pb-6">
                  <CallList calls={calls} />
                </ScrollArea>
              </TabsContent>
              <TabsContent value="details" className="min-h-0 flex-1">
                <ScrollArea className="h-[calc(100vh-150px)] px-4 pb-6">
                  <DetailsForm key={contact.id + contact.updated_at} contact={contact} />
                </ScrollArea>
              </TabsContent>
            </Tabs>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function CallList({ calls }: { calls: CallLog[] }) {
  if (calls.length === 0) {
    return (
      <div className="mt-4 flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center">
        <PhoneOff className="size-5 text-muted-foreground" strokeWidth={1.75} />
        <p className="text-sm font-semibold">No calls yet</p>
        <p className="max-w-[32ch] text-xs text-muted-foreground">
          Calls with this lead appear here with a summary, transcript and recording.
        </p>
      </div>
    );
  }
  return (
    <ol className="space-y-4 py-2">
      {calls.map((call) => (
        <li key={call.id} className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex flex-wrap items-center gap-1.5">
              {call.outcome ? (
                <Badge variant={OUTCOME_VARIANT[call.outcome]} className="capitalize">{call.outcome.replaceAll("_", " ")}</Badge>
              ) : (
                <Badge variant="brand"><LiveDot /> In progress</Badge>
              )}
              <Badge variant="muted" className="capitalize">{call.direction === "webrtc" ? "Browser" : call.direction}</Badge>
              {call.sentiment && <span className="text-xs capitalize text-muted-foreground">{call.sentiment}</span>}
            </div>
            <div className="shrink-0 text-right text-xs text-muted-foreground">
              <p>{format(new Date(call.created_at), "MMM d, h:mm a")}</p>
              <p className="font-mono tabular-nums">{formatDuration(call.duration_seconds)}</p>
            </div>
          </div>
          {call.summary && <p className="mt-3 text-sm leading-relaxed text-foreground">{call.summary}</p>}
          {call.recording_url && (
            <audio controls preload="none" src={call.recording_url} className="mt-3 h-8 w-full" />
          )}
          {(call.transcript?.length ?? 0) > 0 && (
            <Collapsible className="mt-3">
              <CollapsibleTrigger className="group flex items-center gap-1 rounded text-xs font-semibold text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/40">
                <ChevronDown className="h-3.5 w-3.5 transition-transform group-data-[state=open]:rotate-180" />
                Transcript ({call.transcript!.length} turns)
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 space-y-2">
                {call.transcript!.map((line, i) => (
                  <div key={i} className={cn("flex flex-col", line.role === "user" ? "items-end" : "items-start")}>
                    <div
                      className={cn(
                        "max-w-[85%] rounded-lg px-3 py-1.5 text-sm leading-relaxed",
                        line.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted text-foreground",
                      )}
                    >
                      {line.content}
                    </div>
                    <span className="mt-0.5 flex gap-2 text-[11px] text-muted-foreground">
                      <span>{line.role === "user" ? "Caller" : "Agent"}</span>
                      {line.timestamp && <span className="font-mono tabular-nums">{format(new Date(line.timestamp), "h:mm:ss a")}</span>}
                    </span>
                  </div>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}
        </li>
      ))}
    </ol>
  );
}

function DetailsForm({ contact }: { contact: Contact }) {
  const [fullName, setFullName] = useState(contact.full_name ?? "");
  const [email, setEmail] = useState(contact.email ?? "");
  const [phone, setPhone] = useState(contact.phone);
  const [stage, setStage] = useState<PipelineStage>(contact.stage);
  const [fields, setFields] = useState<[string, string][]>(
    Object.entries(contact.custom_fields ?? {}).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]),
  );
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    const custom_fields = Object.fromEntries(fields.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v]));
    const { error } = await supabase()
      .from("contacts")
      .update({ full_name: fullName || null, email: email || null, phone, stage, custom_fields })
      .eq("id", contact.id);
    setSaving(false);
    if (error) toast.error(error.message);
    else toast.success("Contact saved");
  }

  return (
    <div className="space-y-4 py-2">
      <Field label="Full name"><Input value={fullName} onChange={(e) => setFullName(e.target.value)} /></Field>
      <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Phone"><Input value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
      <Field label="Stage">
        <Select value={stage} onValueChange={(v) => setStage(v as PipelineStage)}>
          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            {STAGES.map((s) => <SelectItem key={s} value={s}>{STAGE_LABELS[s]}</SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
      <div className="space-y-2">
        <Label>Custom fields</Label>
        {fields.map(([k, v], i) => (
          <div key={i} className="flex gap-2">
            <Input placeholder="key" value={k} onChange={(e) => setFields(fields.map((f, j) => (j === i ? [e.target.value, f[1]] : f)))} />
            <Input placeholder="value" value={v} onChange={(e) => setFields(fields.map((f, j) => (j === i ? [f[0], e.target.value] : f)))} />
            <Button variant="ghost" size="icon" aria-label="Remove field" onClick={() => setFields(fields.filter((_, j) => j !== i))}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={() => setFields([...fields, ["", ""]])}>
          <Plus className="h-4 w-4" /> Add field
        </Button>
      </div>
      <Button onClick={save} disabled={saving} className="w-full">{saving ? "Saving…" : "Save changes"}</Button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
