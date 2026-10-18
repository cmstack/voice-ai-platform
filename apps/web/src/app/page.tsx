"use client";

import { useState } from "react";
import { BrowserCallSim } from "@/components/browser-call-sim";
import { ContactDrawer } from "@/components/contact-drawer";
import { KanbanBoard } from "@/components/kanban-board";
import { NewLeadDialog } from "@/components/new-lead-dialog";
import { TopNav } from "@/components/top-nav";
import { useCrm } from "@/hooks/use-crm";
import { cn } from "@/lib/utils";

export default function PipelinePage() {
  const crm = useCrm();
  const [openId, setOpenId] = useState<string | null>(null);
  const openContact = openId ? crm.contactById.get(openId) ?? null : null;

  return (
    <div className="flex h-screen flex-col">
      <TopNav>
        <NewLeadDialog />
        <BrowserCallSim contacts={crm.contacts} />
      </TopNav>

      <section className="flex flex-wrap items-center gap-2 border-b border-zinc-200 bg-white px-4 py-2">
        <Kpi label="Total Leads" value={crm.kpis.total} />
        <Kpi label="Active Calls Now" value={crm.kpis.active} live={crm.kpis.active > 0} />
        <Kpi label="Appointments Booked" value={crm.kpis.appointments} accent />
        <Kpi label="Conversion Rate" value={`${crm.kpis.conversion}%`} />
      </section>

      <main className="min-h-0 flex-1 p-4">
        {crm.loading ? (
          <p className="text-sm text-zinc-500">Loading pipeline…</p>
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

function Kpi({ label, value, live, accent }: { label: string; value: number | string; live?: boolean; accent?: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-zinc-200 px-3 py-1 text-sm">
      {live && (
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-600" />
        </span>
      )}
      <span className="text-zinc-500">{label}</span>
      <span className={cn("font-semibold tabular-nums", accent && "text-emerald-700")}>{value}</span>
    </div>
  );
}
