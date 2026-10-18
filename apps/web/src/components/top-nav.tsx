"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AudioLines, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { supabase } from "@/lib/supabase";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Pipeline" },
  { href: "/settings", label: "Agent Studio" },
];

export function TopNav({ children }: { children?: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  async function signOut() {
    await supabase().auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-zinc-200 bg-white px-4">
      <Link href="/" className="flex items-center gap-2 font-semibold">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-emerald-600 text-white">
          <AudioLines className="h-4 w-4" />
        </span>
        Voice Agent CRM
      </Link>
      <nav className="flex gap-1">
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100",
              pathname === l.href && "bg-zinc-100 font-semibold text-zinc-900",
            )}
          >
            {l.label}
          </Link>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-2">
        {children}
        <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
