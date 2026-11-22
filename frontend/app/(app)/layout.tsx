"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BarChart3, LayoutDashboard, Menu, Settings, ShieldCheck } from "lucide-react";
import { api, clearAuth, ensureSession, getStoredUser } from "@/lib/api";
import type { User } from "@/types";
import { Badge } from "@/components/legacy-ui";

const NAV = [
  { href: "/dashboard", label: "Search", icon: LayoutDashboard },
  { href: "/history", label: "History", icon: BarChart3 },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  // No sign-in screen: make sure this browser holds a valid session, and
  // re-check it against the API so a stale/revoked one is replaced instead of
  // being shown in the header.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await ensureSession();
      try {
        const me = await api.get<User>("/auth/me");
        if (cancelled) return;
        localStorage.setItem("ll.user", JSON.stringify(me));
        setUser(me);
      } catch {
        if (cancelled) return;
        clearAuth();
        await ensureSession();
        const fresh = getStoredUser();
        if (!cancelled && fresh) setUser(fresh);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b bg-card/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          <Link href="/dashboard" className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary font-bold text-primary-foreground">
              L
            </div>
            <span className="hidden font-bold sm:block">LocalLead Intelligence</span>
          </Link>

          <nav className="ml-6 hidden items-center gap-1 md:flex">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="btn-ghost text-muted-foreground hover:text-foreground"
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </Link>
            ))}
            {user?.role === "ADMIN" && (
              <Link href="/admin" className="btn-ghost text-muted-foreground hover:text-foreground">
                <ShieldCheck className="h-4 w-4" />
                Admin
              </Link>
            )}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {user && (
              <div className="hidden text-right sm:block">
                <p className="text-sm font-medium leading-tight">
                  {user.full_name || user.email}
                </p>
                <p className="text-xs text-muted-foreground">{user.email}</p>
              </div>
            )}
            {user && (
              <Badge tone={user.role === "ADMIN" ? "info" : "neutral"}>{user.role}</Badge>
            )}
            <button
              className="btn-ghost md:hidden"
              onClick={() => setMenuOpen((v) => !v)}
              aria-label="Toggle navigation"
            >
              <Menu className="h-5 w-5" />
            </button>
          </div>
        </div>
        {menuOpen && (
          <nav className="border-t p-2 md:hidden">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="btn-ghost w-full justify-start"
                onClick={() => setMenuOpen(false)}
              >
                <item.icon className="h-4 w-4" /> {item.label}
              </Link>
            ))}
            {user?.role === "ADMIN" && (
              <Link href="/admin" className="btn-ghost w-full justify-start" onClick={() => setMenuOpen(false)}>
                <ShieldCheck className="h-4 w-4" /> Admin
              </Link>
            )}
          </nav>
        )}
      </header>
      <main className="mx-auto max-w-7xl p-4 md:p-6">{children}</main>
    </div>
  );
}
