"use client";

import { DragDropContext, Draggable, Droppable, type DropResult } from "@hello-pangea/dnd";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { formatMoney, formatPhone, relativeTime } from "@/lib/format";
import { STAGES, STAGE_LABELS, type Contact, type PipelineStage, type Sentiment } from "@/lib/types";

const SENTIMENT_VARIANT: Record<Sentiment, "brand" | "muted" | "danger"> = {
  positive: "brand",
  neutral: "muted",
  negative: "danger",
};

const EMPTY_HINT: Record<PipelineStage, string> = {
  new_lead: "New form leads land here.",
  calling_in_progress: "Live calls show up here.",
  no_answer_retry: "Missed calls wait here for a redial.",
  follow_up_requested: "Callbacks the caller asked for.",
  appointment_booked: "Bookings made by the agent.",
  transferred_to_human: "Calls handed to your team.",
  not_interested: "Closed out leads.",
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
      toast.error(`Could not move the card: ${(e as Error).message}`);
    }
  }

  return (
    <DragDropContext onDragEnd={onDragEnd}>
      <div className="flex h-full snap-x snap-mandatory gap-3 overflow-x-auto pb-2 sm:snap-none">
        {STAGES.map((stage) => {
          const items = byStage.get(stage)!;
          const live = stage === "calling_in_progress" && items.length > 0;
          return (
            <Droppable droppableId={stage} key={stage}>
              {(drop, snapshot) => (
                <section
                  ref={drop.innerRef}
                  {...drop.droppableProps}
                  aria-label={`${STAGE_LABELS[stage]}, ${items.length} ${items.length === 1 ? "lead" : "leads"}`}
                  className={cn(
                    "flex w-[85vw] shrink-0 snap-start flex-col rounded-lg border border-transparent bg-column transition-colors sm:w-72",
                    snapshot.isDraggingOver && "border-brand/40 bg-brand-soft/40",
                  )}
                >
                  <header className="flex h-10 items-center justify-between gap-2 px-3">
                    <h2
                      className={cn(
                        "flex items-center gap-2 truncate text-[13px] font-semibold",
                        stage === "appointment_booked" ? "text-brand-ink" : "text-foreground",
                      )}
                    >
                      {live && <LiveDot />}
                      {STAGE_LABELS[stage]}
                    </h2>
                    <span className="rounded-full bg-card px-2 font-mono text-[11px] tabular-nums text-muted-foreground">
                      {items.length}
                    </span>
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
                    {items.length === 0 && !snapshot.isDraggingOver && (
                      <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
                        {EMPTY_HINT[stage]}
                      </p>
                    )}
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

/** Semantic live indicator: only rendered while a call is actually in progress. */
export function LiveDot() {
  return (
    <span className="relative flex size-2" aria-hidden>
      <span className="absolute inline-flex size-full rounded-full bg-brand opacity-60 motion-safe:animate-ping" />
      <span className="relative inline-flex size-2 rounded-full bg-brand" />
    </span>
  );
}

export function BoardSkeleton() {
  return (
    <div className="flex h-full gap-3 overflow-hidden" aria-busy="true" aria-label="Loading pipeline">
      {STAGES.map((stage, i) => (
        <div key={stage} className="flex w-72 shrink-0 flex-col gap-2 rounded-lg bg-column p-2">
          <div className="mx-1 my-2 h-3 w-28 rounded bg-muted motion-safe:animate-pulse" />
          {Array.from({ length: (i % 3) + 1 }).map((_, j) => (
            <div key={j} className="h-[92px] rounded-lg border border-border bg-card motion-safe:animate-pulse" />
          ))}
        </div>
      ))}
    </div>
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
  const name = contact.full_name || formatPhone(contact.phone);
  return (
    <article
      ref={innerRef}
      {...dragProps}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen();
      }}
      aria-label={`${name}. Press Enter to open, Space to drag.`}
      className={cn(
        "group cursor-pointer rounded-lg border border-border bg-card p-3 text-left outline-none transition-[border-color,box-shadow,transform] duration-300",
        "hover:border-foreground/20 focus-visible:ring-3 focus-visible:ring-ring/40",
        "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-1",
        contact.stage === "appointment_booked" && "shadow-[inset_3px_0_0_var(--brand)]",
        highlight && "border-brand ring-3 ring-brand/25",
        dragging && "shadow-lg shadow-foreground/10 motion-safe:rotate-1",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-semibold">{name}</p>
        {value && <span className="shrink-0 font-mono text-xs tabular-nums text-foreground">{value}</span>}
      </div>
      <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate font-mono tabular-nums">{contact.full_name ? formatPhone(contact.phone) : "Unknown caller"}</span>
        {seen && <span className="shrink-0">{seen}</span>}
      </div>
      {(sentiment || (contact.tags?.length ?? 0) > 0) && (
        <div className="mt-3 flex flex-wrap items-center gap-1">
          {sentiment && <Badge variant={SENTIMENT_VARIANT[sentiment]} className="capitalize">{sentiment}</Badge>}
          {(contact.tags ?? []).map((t) => (
            <Badge key={t} variant="outline" className="font-normal text-muted-foreground">{t}</Badge>
          ))}
        </div>
      )}
    </article>
  );
}
