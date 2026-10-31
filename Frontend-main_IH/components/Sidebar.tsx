"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import styles from "./Sidebar.module.css";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/calls", label: "Calls" },
  { href: "/bookings", label: "Bookings" },
  { href: "/leads", label: "Leads" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className={styles.sidebar}>
      <Link href="/" className={styles.brand} aria-label="Innoventix Hub">
        <Image
          src="/innoventix-logo.gif"
          alt="Innoventix Hub"
          width={180}
          height={68}
          priority
          unoptimized
          className={styles.brandLogo}
        />
      </Link>

      <nav className={styles.nav}>
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={active ? `${styles.link} ${styles.linkActive}` : styles.link}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      <form action="/api/logout" method="post" className={styles.logoutForm}>
        <button type="submit" className={styles.logout}>
          Sign out
        </button>
      </form>
    </aside>
  );
}
