"use client";

import { useEffect, useRef, useState } from "react";
import { PipecatClient, type BotOutputData, type TransportState, type UserInputData } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport, WavMediaManager } from "@pipecat-ai/small-webrtc-transport";
import { AudioLines, Headphones, Mic, MicOff, PhoneIncoming, PhoneOff, PhoneOutgoing, Wrench } from "lucide-react";
import { LiveDot } from "@/components/kanban-board";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { accessToken, VOICE_ENGINE_URL } from "@/lib/supabase";
import { formatDuration, formatPhone } from "@/lib/format";
import type { Contact } from "@/lib/types";
import { cn } from "@/lib/utils";

type Direction = "inbound" | "outbound";
type Line = { id: string; role: "user" | "assistant"; text: string; final: boolean };
type ToolEvent = { id: string; name: string; status: "running" | "done" | "cancelled"; args?: unknown; result?: unknown };

const NEW_TEST_LEAD = "__new__";

export function BrowserCallSim({ contacts }: { contacts: Contact[] }) {
  const [open, setOpen] = useState(false);
  const [direction, setDirection] = useState<Direction>("inbound");
  const [contactId, setContactId] = useState<string>(NEW_TEST_LEAD);
  const [state, setState] = useState<TransportState | "idle">("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [tools, setTools] = useState<ToolEvent[]>([]);
  const [micBlocked, setMicBlocked] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const botLineId = useRef<string | null>(null);
  const transcriptEnd = useRef<HTMLDivElement>(null);

  const live = state !== "idle" && state !== "disconnected" && state !== "error";

  useEffect(() => () => void clientRef.current?.disconnect(), []);

  // Keep the newest line in view as the conversation grows.
  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [lines]);

  // Call timer while the agent is live.
  useEffect(() => {
    if (state !== "ready") return;
    const started = Date.now();
    setElapsed(0);
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(id);
  }, [state]);

  function upsertLine(id: string, role: Line["role"], text: string, final: boolean, append = false) {
    setLines((prev) => {
      const i = prev.findIndex((l) => l.id === id);
      if (i === -1) return [...prev, { id, role, text, final }];
      const current = prev[i];
      // Nova Sonic text can arrive twice for the same speech (as generated and as spoken); skip repeats.
      if (append && current.text.trim().endsWith(text.trim())) return prev;
      const updated = [...prev];
      updated[i] = { ...current, text: append ? `${current.text} ${text}`.trim() : text, final };
      return updated;
    });
  }

  async function start() {
    const token = await accessToken();
    if (!token) return toast.error("Your session expired. Sign in again.");
    setLines([]);
    setTools([]);
    setMicBlocked(false);
    botLineId.current = null;
    let userTurn = 0;

    const transport = new SmallWebRTCTransport({
      // The default DailyMediaManager downloads Daily's call-machine script from c.dailywebrtc.net at
      // runtime, which our CSP blocks (and which would run third-party code next to the admin session).
      // WavMediaManager captures the mic with plain getUserMedia; bot audio still arrives as a WebRTC
      // track played by the <audio> element below.
      mediaManager: new WavMediaManager(),
      webrtcRequestParams: {
        endpoint: `${VOICE_ENGINE_URL}/api/webrtc/offer`,
        headers: new Headers({ Authorization: `Bearer ${token}` }),
        requestData: { direction, ...(direction === "outbound" && contactId !== NEW_TEST_LEAD ? { contact_id: contactId } : {}) },
      },
    });
    const client = new PipecatClient({
      transport,
      enableMic: true,
      enableCam: false,
      callbacks: {
        onTransportStateChanged: setState,
        onTrackStarted: (track, participant) => {
          if (participant?.local || track.kind !== "audio" || !audioRef.current) return;
          audioRef.current.srcObject = new MediaStream([track]);
          void audioRef.current.play().catch(() => undefined);
        },
        onUserInput: (data: UserInputData) => {
          botLineId.current = null;
          upsertLine(`u-${userTurn}`, "user", data.text, data.final);
          if (data.final) userTurn += 1;
        },
        onBotOutput: (data: BotOutputData) => {
          botLineId.current ??= `b-${crypto.randomUUID()}`;
          upsertLine(botLineId.current, "assistant", data.text, true, true);
        },
        onBotStoppedSpeaking: () => {
          botLineId.current = null;
        },
        onLLMFunctionCallInProgress: (d) =>
          setTools((t) => [...t, { id: d.tool_call_id, name: d.function_name ?? "tool", status: "running", args: d.arguments }]),
        onLLMFunctionCallStopped: (d) =>
          setTools((t) => t.map((e) => (e.id === d.tool_call_id ? { ...e, status: d.cancelled ? "cancelled" : "done", result: d.result } : e))),
        onDeviceError: (err) => {
          if (err.type === "permissions" || err.type === "not-found" || err.type === "in-use") setMicBlocked(true);
        },
        onError: (msg) => {
          const data = msg.data as { message?: string; error?: string } | undefined;
          toast.error(`Voice engine error: ${data?.message ?? data?.error ?? "the call failed. Check the voice engine logs."}`);
        },
        onDisconnected: () => setState("disconnected"),
      },
    });
    clientRef.current = client;
    try {
      await client.connect();
    } catch (e) {
      setState("error");
      const err = e as Error;
      if (err.name === "NotAllowedError" || /permission|denied/i.test(err.message)) {
        setMicBlocked(true);
      } else {
        toast.error(`Could not connect: ${err.message}`);
      }
    }
  }

  async function stop() {
    await clientRef.current?.disconnect();
    clientRef.current = null;
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) void stop(); }}>
      <DialogTrigger asChild>
        <Button variant="brand" aria-label="Test Agent in Browser ($0 WebRTC)">
          <Headphones strokeWidth={1.75} />
          <span className="hidden md:inline">Test Agent in Browser ($0 WebRTC)</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="gap-5 sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="font-semibold">Browser call simulator</DialogTitle>
          <DialogDescription>Talk to the agent through your microphone. No phone number needed.</DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-end gap-3">
          <div role="group" aria-label="Call type" className="flex rounded-lg bg-muted p-1">
            {(["inbound", "outbound"] as const).map((d) => (
              <button
                key={d}
                type="button"
                aria-pressed={direction === d}
                disabled={live}
                onClick={() => setDirection(d)}
                className={cn(
                  "flex h-8 items-center gap-2 rounded-md px-3 text-sm outline-none transition-colors focus-visible:ring-3 focus-visible:ring-ring/40 disabled:opacity-60",
                  direction === d ? "bg-card font-semibold text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {d === "inbound" ? <PhoneIncoming className="size-4" strokeWidth={1.75} /> : <PhoneOutgoing className="size-4" strokeWidth={1.75} />}
                {d === "inbound" ? "Inbound call" : "Speed-to-lead call"}
              </button>
            ))}
          </div>
          {direction === "outbound" && (
            <div className="min-w-56 flex-1 space-y-2">
              <Label htmlFor="sim-lead" className="text-xs text-muted-foreground">Lead to call</Label>
              <Select value={contactId} onValueChange={setContactId} disabled={live}>
                <SelectTrigger id="sim-lead" className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NEW_TEST_LEAD}>Browser Test Lead (new)</SelectItem>
                  {contacts.map((c) => (
                    <SelectItem key={c.id} value={c.id}>{c.full_name || formatPhone(c.phone)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="ml-auto flex items-center gap-3">
            <CallStatus state={state} elapsed={elapsed} />
            {live ? (
              <Button variant="destructive" onClick={stop}><PhoneOff strokeWidth={1.75} /> Hang up</Button>
            ) : (
              <Button variant="brand" onClick={start}><Mic strokeWidth={1.75} /> Start call</Button>
            )}
          </div>
        </div>

        {micBlocked && (
          <div role="alert" className="flex items-start gap-3 rounded-lg bg-danger-soft p-3 text-sm text-danger-ink">
            <MicOff className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
            <p>
              <span className="font-semibold">Microphone blocked.</span> Allow microphone access for this site in your browser, then
              start the call again. Embedded preview browsers often block it; use Chrome or Safari.
            </p>
          </div>
        )}

        <div className="grid gap-3 md:grid-cols-[1fr_240px]">
          <ScrollArea className="h-64 rounded-lg border border-border bg-background md:h-80">
            {lines.length === 0 ? (
              <div className="flex h-64 flex-col items-center justify-center gap-2 px-6 text-center md:h-80">
                <AudioLines className="size-5 text-muted-foreground" strokeWidth={1.75} />
                <p className="text-sm font-semibold">Live transcript</p>
                <p className="max-w-[36ch] text-xs text-muted-foreground">
                  {direction === "inbound"
                    ? "Start the call and the agent greets you first. What you both say appears here."
                    : "Start the call and say hello. The agent opens once it hears you."}
                </p>
              </div>
            ) : (
              <div className="space-y-2 p-3" aria-live="polite">
                {lines.map((l) => (
                  <div key={l.id} className={cn("flex", l.role === "user" ? "justify-end" : "justify-start")}>
                    <p
                      className={cn(
                        "max-w-[85%] rounded-lg px-3 py-1.5 text-sm leading-relaxed",
                        l.role === "user" ? "bg-primary text-primary-foreground" : "border border-border bg-card text-foreground",
                        !l.final && "opacity-60",
                      )}
                    >
                      {l.text}
                    </p>
                  </div>
                ))}
                <div ref={transcriptEnd} />
              </div>
            )}
          </ScrollArea>
          <ScrollArea className="h-40 rounded-lg border border-border md:h-80">
            <div className="p-3">
              <p className="mb-3 flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                <Wrench className="size-3.5" strokeWidth={1.75} /> Tool calls
              </p>
              {tools.length === 0 && (
                <p className="text-xs leading-relaxed text-muted-foreground">
                  Calendar lookups, bookings and transfers show here with their inputs and results.
                </p>
              )}
              <ul className="space-y-2">
                {tools.map((t) => (
                  <li key={t.id} className="rounded-lg border border-border p-2 text-xs">
                    <p className="flex items-center justify-between gap-2 font-semibold">
                      <span className="truncate font-mono">{t.name}</span>
                      <Badge variant={t.status === "done" ? "brand" : "muted"} className="capitalize">
                        {t.status === "running" ? "Running" : t.status === "done" ? "Done" : "Cancelled"}
                      </Badge>
                    </p>
                    {t.args != null && <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-[11px] text-muted-foreground">{JSON.stringify(t.args)}</pre>}
                    {t.result != null && <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-[11px] text-foreground">{JSON.stringify(t.result)}</pre>}
                  </li>
                ))}
              </ul>
            </div>
          </ScrollArea>
        </div>
        <audio ref={audioRef} autoPlay className="hidden" />
      </DialogContent>
    </Dialog>
  );
}

const STATUS_TEXT: Partial<Record<TransportState | "idle", string>> = {
  idle: "Not connected",
  disconnected: "Call ended",
  error: "Could not connect",
  ready: "Live",
};

function CallStatus({ state, elapsed }: { state: TransportState | "idle"; elapsed: number }) {
  const text = STATUS_TEXT[state] ?? "Connecting…";
  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
      {state === "ready" && <LiveDot />}
      <span className={cn(state === "ready" && "font-semibold text-brand-ink", state === "error" && "text-danger-ink")}>{text}</span>
      {state === "ready" && <span className="font-mono tabular-nums">{formatDuration(elapsed)}</span>}
    </span>
  );
}
