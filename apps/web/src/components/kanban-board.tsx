"use client";

import { DragDropContext, Draggable, Droppable, type DropResult } from "@hello-pangea/dnd";
import { Clock, Phone } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { formatMoney, formatPhone, relativeTime } from "@/lib/format";
import { STAGES, STAGE_LABELS, type Contact, type PipelineStage, type Sentiment } from "@/lib/types";

const SENTIMENT_STYLES: Record<Sentiment, string> = {
  positive: "border-emerald-200 bg-emerald-50 text-emerald-700",
  neutral: "border-zinc-200 bg-zinc-50 text-zinc-600",
  negative: "border-red-200 bg-red-50 text-red-700",
};

interface Props {
  contacts: Contact[];
  sentimentFor: (id: string) => Sentiment | null;
  recentlyMoved: Set<string>;
  onMove: (id: string, stage: PipelineStage) => Promise<void>;
  onOpen: (id: string) => void;
}

export function KanbanBoard({ contacts, sentimentFor, recentlyMoved, onMove, onOpen }: Props) {
  const byStage = new Map<PipelineStage, Contact[]>(STAGES.map((s) => [s, []]));
  for (const c of contacts) byStage.get(c.stage)?.push(c);
  for (const list of byStage.values()) list.sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  async function onDragEnd(result: DropResult) {
    const to = result.destination?.droppableId as PipelineStage | undefined;
    if (!to || to === result.source.droppableId) return;
    try {
      await onMove(result.draggableId, to);
    } catch (e) {
      toast.error(`Could not move card: ${(e as Error).message}`);
    }
  }

  return (
    <DragDropContext onDragEnd={onDragEnd}>
      <div className="flex h-full gap-4 overflow-x-auto pb-4">
        {STAGES.map((stage) => {
          const items = byStage.get(stage)!;
          return (
            <Droppable droppableId={stage} key={stage}>
              {(drop, snapshot) => (
                <section
                  ref={drop.innerRef}
                  {...drop.droppableProps}
                  className={cn(
                    "flex w-72 shrink-0 flex-col rounded-lg border border-zinc-200 bg-zinc-100/70 transition-colors",
                    snapshot.isDraggingOver && "border-zinc-300 bg-zinc-200/60",
                  )}
                >
                  <header className="flex items-center justify-between px-3 py-2">
                    <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-700">
                      {stage === "calling_in_progress" && items.length > 0 && <PulseDot />}
                      {STAGE_LABELS[stage]}
                    </h2>
                    <span className="rounded-md bg-white px-2 text-xs text-zinc-500">{items.length}</span>
                  </header>
                  <div className="flex min-h-24 flex-1 flex-col gap-2 overflow-y-auto px-2 pb-2">
                    {items.map((c, index) => (
                      <Draggable draggableId={c.id} index={index} key={c.id}>
                        {(drag, dragSnap) => (
                          <ContactCard
                            innerRef={drag.innerRef}
                            dragProps={{ ...drag.draggableProps, ...drag.dragHandleProps }}
                            contact={c}
                            sentiment={sentimentFor(c.id)}
                            highlight={recentlyMoved.has(c.id)}
                            dragging={dragSnap.isDragging}
                            onOpen={() => onOpen(c.id)}
                          />
                        )}
                      </Draggable>
                    ))}
                    {drop.placeholder}
                  </div>
                </section>
              )}
            </Droppable>
          );
        })}
      </div>
    </DragDropContext>
  );
}

function PulseDot() {
  return (
    <span className="relative flex h-2 w-2">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-600" />
    </span>
  );
}

function ContactCard({
  innerRef, dragProps, contact, sentiment, highlight, dragging, onOpen,
}: {
  innerRef: (el: HTMLElement | null) => void;
  dragProps: Record<string, unknown>;
  contact: Contact;
  sentiment: Sentiment | null;
  highlight: boolean;
  dragging: boolean;
  onOpen: () => void;
}) {
  const value = formatMoney(contact.deal_value);
  const seen = relativeTime(contact.last_contacted_at);
  return (
    <article
      ref={innerRef}
      {...dragProps}
      onClick={onOpen}
      className={cn(
        "animate-in fade-in slide-in-from-bottom-1 cursor-pointer rounded-md border border-zinc-200 bg-white p-3 shadow-xs transition-all duration-500 hover:border-zinc-300",
        dragging && "rotate-1 shadow-lg",
        highlight && "border-emerald-500 ring-2 ring-emerald-500/30",
        contact.stage === "appointment_booked" && "border-l-4 border-l-emerald-600",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-semibold">{contact.full_name || formatPhone(contact.phone)}</p>
        {value && <span className="shrink-0 text-xs font-semibold text-zinc-700">{value}</span>}
      </div>
      {contact.full_name && (
        <p className="mt-0.5 flex items-center gap-1 text-xs text-zinc-500">
          <Phone className="h-3 w-3" /> {formatPhone(contact.phone)}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {sentiment && <Badge variant="outline" className={cn("capitalize", SENTIMENT_STYLES[sentiment])}>{sentiment}</Badge>}
        {(contact.tags ?? []).map((t) => (
          <Badge key={t} variant="secondary" className="font-normal">{t}</Badge>
        ))}
      </div>
      {seen && (
        <p className="mt-2 flex items-center gap-1 text-xs text-zinc-400">
          <Clock className="h-3 w-3" /> {seen}
        </p>
      )}
    </article>
  );
}
