"use client";

import { useEffect, useRef, useState } from "react";
import { PipecatClient, type BotOutputData, type TransportState, type UserInputData } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { Headphones, Mic, PhoneIncoming, PhoneOff, PhoneOutgoing, Wrench } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { accessToken, VOICE_ENGINE_URL } from "@/lib/supabase";
import { formatPhone } from "@/lib/format";
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
  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const botLineId = useRef<string | null>(null);

  const live = state !== "idle" && state !== "disconnected" && state !== "error";

  useEffect(() => () => void clientRef.current?.disconnect(), []);

  function upsertLine(id: string, role: Line["role"], text: string, final: boolean, append = false) {
    setLines((prev) => {
      const i = prev.findIndex((l) => l.id === id);
      if (i === -1) return [...prev, { id, role, text, final }];
      const next = [...prev];
      next[i] = { ...next[i], text: append ? `${next[i].text} ${text}`.trim() : text, final };
      return next;
    });
  }

  async function start() {
    const token = await accessToken();
    if (!token) return toast.error("Your session expired. Sign in again.");
    setLines([]);
    setTools([]);
    botLineId.current = null;
    let userTurn = 0;

    const transport = new SmallWebRTCTransport({
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
        onError: (msg) => toast.error(`Voice engine error: ${JSON.stringify(msg.data ?? msg)}`),
        onDisconnected: () => setState("disconnected"),
      },
    });
    clientRef.current = client;
    try {
      await client.connect();
    } catch (e) {
      setState("error");
      toast.error(`Could not connect: ${(e as Error).message}`);
    }
  }

  async function stop() {
    await clientRef.current?.disconnect();
    clientRef.current = null;
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) void stop(); }}>
      <DialogTrigger asChild>
        <Button className="bg-emerald-600 hover:bg-emerald-700">
          <Headphones className="h-4 w-4" /> Test Agent in Browser ($0 WebRTC)
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-semibold">Browser call simulator</DialogTitle>
          <DialogDescription>Talk to the agent through your microphone. No phone number needed.</DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex rounded-lg border border-zinc-200 p-1">
            {(["inbound", "outbound"] as const).map((d) => (
              <button
                key={d}
                disabled={live}
                onClick={() => setDirection(d)}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors disabled:opacity-60",
                  direction === d ? "bg-zinc-900 font-semibold text-white" : "text-zinc-600 hover:bg-zinc-100",
                )}
              >
                {d === "inbound" ? <PhoneIncoming className="h-4 w-4" /> : <PhoneOutgoing className="h-4 w-4" />}
                {d === "inbound" ? "Simulate inbound call" : "Simulate speed-to-lead call"}
              </button>
            ))}
          </div>
          {direction === "outbound" && (
            <div className="min-w-56 flex-1 space-y-1">
              <Label className="text-xs text-zinc-500">Lead to call</Label>
              <Select value={contactId} onValueChange={setContactId} disabled={live}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NEW_TEST_LEAD}>Browser Test Lead (new)</SelectItem>
                  {contacts.map((c) => (
                    <SelectItem key={c.id} value={c.id}>{c.full_name || formatPhone(c.phone)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          {live ? (
            <Button variant="destructive" onClick={stop}><PhoneOff className="h-4 w-4" /> Hang up</Button>
          ) : (
            <Button onClick={start} className="bg-emerald-600 hover:bg-emerald-700"><Mic className="h-4 w-4" /> Start call</Button>
          )}
        </div>

        <p className="flex items-center gap-2 text-xs text-zinc-500">
          <span className={cn("h-2 w-2 rounded-full", state === "ready" ? "animate-pulse bg-emerald-600" : live ? "bg-amber-500" : "bg-zinc-300")} />
          {state === "idle" ? "Not connected" : state}
        </p>

        <div className="grid gap-4 sm:grid-cols-[1fr_220px]">
          <ScrollArea className="h-80 rounded-lg border border-zinc-200 bg-zinc-50 p-3">
            {lines.length === 0 && <p className="text-sm text-zinc-400">The live transcript appears here.</p>}
            <div className="space-y-2">
              {lines.map((l) => (
                <div key={l.id} className={cn("flex", l.role === "user" ? "justify-end" : "justify-start")}>
                  <p
                    className={cn(
                      "max-w-[85%] rounded-lg px-3 py-1.5 text-sm",
                      l.role === "user" ? "bg-zinc-900 text-white" : "bg-white text-zinc-800 shadow-xs",
                      !l.final && "opacity-60",
                    )}
                  >
                    {l.text}
                  </p>
                </div>
              ))}
            </div>
          </ScrollArea>
          <ScrollArea className="h-80 rounded-lg border border-zinc-200 p-3">
            <p className="mb-2 flex items-center gap-1 text-xs font-semibold text-zinc-600"><Wrench className="h-3.5 w-3.5" /> Tool calls</p>
            {tools.length === 0 && <p className="text-xs text-zinc-400">None yet.</p>}
            <ul className="space-y-2">
              {tools.map((t) => (
                <li key={t.id} className="rounded-md border border-zinc-200 p-2 text-xs">
                  <p className="flex items-center justify-between font-semibold">
                    {t.name}
                    <span className={cn(t.status === "running" ? "text-amber-600" : t.status === "done" ? "text-emerald-600" : "text-zinc-400")}>{t.status}</span>
                  </p>
                  {t.args != null && <pre className="mt-1 whitespace-pre-wrap break-all text-[10px] text-zinc-500">{JSON.stringify(t.args)}</pre>}
                  {t.result != null && <pre className="mt-1 whitespace-pre-wrap break-all text-[10px] text-zinc-700">{JSON.stringify(t.result)}</pre>}
                </li>
              ))}
            </ul>
          </ScrollArea>
        </div>
        <audio ref={audioRef} autoPlay className="hidden" />
      </DialogContent>
    </Dialog>
  );
}
