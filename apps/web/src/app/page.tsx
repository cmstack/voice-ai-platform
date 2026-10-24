"use client";

import { useState } from "react";
import { CloudOff, RotateCw } from "lucide-react";
import { BrowserCallSim } from "@/components/browser-call-sim";
import { ContactDrawer } from "@/components/contact-drawer";
import { BoardSkeleton, KanbanBoard, LiveDot } from "@/components/kanban-board";
import { NewLeadDialog } from "@/components/new-lead-dialog";
import { TopNav } from "@/components/top-nav";
import { Button } from "@/components/ui/button";
import { useCrm } from "@/hooks/use-crm";
import { cn } from "@/lib/utils";

export default function PipelinePage() {
  const crm = useCrm();
  const [openId, setOpenId] = useState<string | null>(null);
  const openContact = openId ? crm.contactById.get(openId) ?? null : null;
  const kpi = (v: number | string) => (crm.loadError ? "—" : v);

  return (
    <div className="flex h-dvh flex-col">
      <TopNav>
        <NewLeadDialog />
        <BrowserCallSim contacts={crm.contacts} />
      </TopNav>

      <section aria-label="Pipeline metrics" className="grid grid-cols-2 gap-px border-b border-border bg-border sm:flex sm:gap-0 sm:bg-card sm:px-2">
        <Stat label="Total leads" value={kpi(crm.kpis.total)} loading={crm.loading} />
        <Stat label="Active calls" value={kpi(crm.kpis.active)} loading={crm.loading} live={crm.kpis.active > 0} />
        <Stat label="Appointments booked" value={kpi(crm.kpis.appointments)} loading={crm.loading} accent />
        <Stat label="Conversion" value={kpi(`${crm.kpis.conversion}%`)} loading={crm.loading} />
      </section>

      <main className="min-h-0 flex-1 p-4">
        {crm.loading ? (
          <BoardSkeleton />
        ) : crm.loadError ? (
          <div role="alert" className="mx-auto mt-16 flex max-w-sm flex-col items-center gap-2 text-center">
            <CloudOff className="size-5 text-danger-ink" strokeWidth={1.75} />
            <p className="text-sm font-semibold">Couldn&apos;t load the pipeline</p>
            <p className="text-xs text-muted-foreground">{crm.loadError}. Check that Supabase is running, then try again.</p>
            <Button variant="outline" size="sm" className="mt-2" onClick={crm.retry}>
              <RotateCw strokeWidth={1.75} /> Try again
            </Button>
          </div>
        ) : (
          <KanbanBoard
            contacts={crm.contacts}
            sentimentFor={crm.sentimentFor}
            recentlyMoved={crm.recentlyMoved}
            onMove={crm.moveContact}
            onOpen={setOpenId}
          />
        )}
      </main>

      <ContactDrawer contact={openContact} callLogVersion={crm.callLogVersion} onClose={() => setOpenId(null)} />
    </div>
  );
}

function Stat({
  label, value, live, accent, loading,
}: { label: string; value: number | string; live?: boolean; accent?: boolean; loading: boolean }) {
  return (
    <div className="flex flex-col justify-center gap-0.5 bg-card px-4 py-2 sm:min-w-36 sm:border-r sm:border-border sm:last:border-r-0">
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        {live && <LiveDot />}
        {label}
      </span>
      {loading ? (
        <span className="my-1 h-5 w-10 rounded bg-muted motion-safe:animate-pulse" />
      ) : (
        <span className={cn("font-mono text-xl font-semibold tabular-nums leading-7", accent && "text-brand-ink")}>
          {value}
        </span>
      )}
    </div>
  );
}
