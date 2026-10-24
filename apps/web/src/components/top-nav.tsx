"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AudioLines, LogOut } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
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
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-card/90 px-3 backdrop-blur sm:px-4 supports-[backdrop-filter]:bg-card/75">
      <Link href="/" className="hidden items-center gap-2 rounded-lg sm:flex text-sm font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
        <span className="flex size-7 items-center justify-center rounded-lg bg-brand text-brand-foreground">
          <AudioLines className="size-4" strokeWidth={1.75} />
        </span>
        <span className="hidden sm:inline">Voice Agent CRM</span>
      </Link>
      <nav className="-ml-2 flex h-full items-stretch gap-1 sm:ml-0" aria-label="Main">
        {LINKS.map((l) => {
          const active = pathname === l.href;
          return (
            <Link
              key={l.href}
              href={l.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative flex items-center whitespace-nowrap px-2 text-sm text-muted-foreground sm:px-3 outline-none transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:ring-3 focus-visible:ring-inset focus-visible:ring-ring/40 rounded-md",
                active && "font-semibold text-foreground after:absolute after:inset-x-2 after:bottom-0 sm:after:inset-x-3 after:h-0.5 after:rounded-full after:bg-foreground",
              )}
            >
              {l.label}
            </Link>
          );
        })}
      </nav>
      <div className="ml-auto flex items-center gap-1 sm:gap-2">
        {children}
        <span className="mx-1 hidden h-5 w-px bg-border md:block" aria-hidden />
        <ThemeToggle />
        <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out" title="Sign out">
          <LogOut className="size-4" strokeWidth={1.75} />
        </Button>
      </div>
    </header>
  );
}
