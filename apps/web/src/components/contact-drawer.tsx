"use client";

import { useEffect, useState } from "react";
import { format } from "date-fns";
import { ChevronDown, Plus, Trash2 } from "lucide-react";
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

const OUTCOME_STYLES: Record<CallOutcome, string> = {
  booked: "bg-emerald-600 text-white",
  follow_up_requested: "bg-amber-100 text-amber-800",
  voicemail: "bg-zinc-200 text-zinc-700",
  transferred: "bg-sky-100 text-sky-800",
  not_interested: "bg-red-100 text-red-700",
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
            <SheetHeader className="border-b border-zinc-200 p-4">
              <SheetTitle className="font-semibold">{contact.full_name || formatPhone(contact.phone)}</SheetTitle>
              <SheetDescription>
                {formatPhone(contact.phone)} · {STAGE_LABELS[contact.stage]}
                {contact.retry_count > 0 && ` · ${contact.retry_count} retr${contact.retry_count === 1 ? "y" : "ies"}`}
              </SheetDescription>
            </SheetHeader>
            <Tabs defaultValue="activity" className="flex min-h-0 flex-1 flex-col">
              <TabsList className="mx-4 mt-4">
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
  if (calls.length === 0) return <p className="py-8 text-center text-sm text-zinc-500">No calls yet.</p>;
  return (
    <ol className="space-y-4 py-2">
      {calls.map((call) => (
        <li key={call.id} className="rounded-lg border border-zinc-200 bg-white p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="capitalize">{call.direction}</Badge>
              {call.outcome ? (
                <Badge className={cn("border-0 capitalize", OUTCOME_STYLES[call.outcome])}>{call.outcome.replaceAll("_", " ")}</Badge>
              ) : (
                <Badge className="border-0 bg-emerald-50 text-emerald-700">in progress</Badge>
              )}
              {call.sentiment && <span className="text-xs capitalize text-zinc-500">{call.sentiment}</span>}
            </div>
            <span className="text-xs text-zinc-500">
              {format(new Date(call.created_at), "MMM d, h:mm a")} · {formatDuration(call.duration_seconds)}
            </span>
          </div>
          {call.summary && <p className="mt-3 rounded-md bg-zinc-50 p-2 text-sm text-zinc-700">{call.summary}</p>}
          {call.recording_url && (
            <audio controls preload="none" src={call.recording_url} className="mt-3 h-8 w-full" />
          )}
          {(call.transcript?.length ?? 0) > 0 && (
            <Collapsible className="mt-3">
              <CollapsibleTrigger className="group flex items-center gap-1 text-xs font-semibold text-zinc-600 hover:text-zinc-900">
                <ChevronDown className="h-3.5 w-3.5 transition-transform group-data-[state=open]:rotate-180" />
                Transcript ({call.transcript!.length} turns)
              </CollapsibleTrigger>
              <CollapsibleContent className="mt-2 space-y-2">
                {call.transcript!.map((line, i) => (
                  <div key={i} className={cn("flex flex-col", line.role === "user" ? "items-end" : "items-start")}>
                    <div
                      className={cn(
                        "max-w-[85%] rounded-lg px-3 py-1.5 text-sm",
                        line.role === "user" ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-800",
                      )}
                    >
                      {line.content}
                    </div>
                    <span className="mt-0.5 text-[10px] text-zinc-400">
                      {line.role === "user" ? "Caller" : "Agent"}
                      {line.timestamp && ` · ${format(new Date(line.timestamp), "h:mm:ss a")}`}
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
