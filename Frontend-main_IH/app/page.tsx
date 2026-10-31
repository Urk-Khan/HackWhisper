import Link from "next/link";
import { fetchRows } from "@/lib/supabase";
import { CallLog, Lead, Meeting } from "@/lib/types";
import { CallOutcomeBadge } from "@/components/StatusBadge";
import HourlyChart from "@/components/HourlyChart";
import OutcomePieChart from "@/components/OutcomePieChart";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function formatDuration(seconds: number | null): string {
  if (!seconds && seconds !== 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default async function DashboardPage() {
  let calls: CallLog[] = [];
  let meetings: Meeting[] = [];
  let leads: Lead[] = [];
  let loadError: string | null = null;

  try {
    [calls, meetings, leads] = await Promise.all([
      fetchRows<CallLog>("call_logs", "select=*&order=created_at.desc&limit=200"),
      fetchRows<Meeting>("meetings", "select=*&order=created_at.desc&limit=200"),
      fetchRows<Lead>("leads", "select=*&order=created_at.desc&limit=200"),
    ]);
  } catch (err) {
    loadError = err instanceof Error ? err.message : "Unknown error";
  }

  const cutoff = Date.now() - 24 * 60 * 60 * 1000;
  const recentCalls = calls.filter((c) => new Date(c.created_at).getTime() > cutoff);
  const recentMeetings = meetings.filter((m) => new Date(m.created_at).getTime() > cutoff);
  const recentLeads = leads.filter((l) => new Date(l.created_at).getTime() > cutoff);
  const customerSupport = recentCalls.filter((c) => c.outcome === "dropped_call");

  // Rolling 24h histogram, bucketed by hour-old (23 = oldest, 0 = most recent), then reversed
  // for left-to-right = oldest-to-newest display.
  const buckets = new Array(24).fill(0);
  for (const call of recentCalls) {
    const hoursAgo = Math.floor((Date.now() - new Date(call.created_at).getTime()) / (60 * 60 * 1000));
    if (hoursAgo >= 0 && hoursAgo < 24) buckets[23 - hoursAgo] += 1;
  }

  return (
    <div>
      <header className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <p className="page-subtitle">Rolling last 24 hours of activity.</p>
      </header>

      {loadError && (
        <div className={styles.errorPanel}>
          Couldn&apos;t load data from Database: {loadError}. Check DATABASE_URL in environment variables.
        </div>
      )}

      <div className={styles.statRow}>
        <div className={styles.stat}>
          <div className={styles.statValue}>{recentCalls.length}</div>
          <div className={styles.statLabel}>Total calls</div>
        </div>
        <div className={styles.statDivider} />
        <div className={styles.stat}>
          <div className={styles.statValue} style={{ color: "var(--teal)" }}>
            {recentMeetings.length}
          </div>
          <div className={styles.statLabel}>Meetings booked</div>
        </div>
        <div className={styles.statDivider} />
        <div className={styles.stat}>
          <div className={styles.statValue} style={{ color: "var(--amber)" }}>
            {recentLeads.length}
          </div>
          <div className={styles.statLabel}>Leads captured</div>
        </div>
        <div className={styles.statDivider} />
        <div className={styles.stat}>
          <div className={styles.statValue} style={{ color: "var(--blue)" }}>
            {customerSupport.length}
          </div>
          <div className={styles.statLabel}>Customer support</div>
        </div>
      </div>

      <div className={styles.chartsRow}>
        <section className={`panel ${styles.chartPanel}`}>
          <h2 className={styles.sectionTitle}>Call volume</h2>
          <HourlyChart hours={buckets} axisLabels={["24h ago", "18h ago", "12h ago", "6h ago", "now"]} />
        </section>

        <section className={`panel ${styles.chartPanel}`}>
          <h2 className={styles.sectionTitle}>Call outcomes</h2>
          <OutcomePieChart calls={recentCalls.length > 0 ? recentCalls : calls} />
        </section>
      </div>

      <section className={styles.recentSection}>
        <div className={styles.recentHeader}>
          <h2 className={styles.sectionTitle}>Recent calls</h2>
          <Link href="/calls" className={styles.viewAll}>
            View all
          </Link>
        </div>
        <div className="panel">
          {calls.length === 0 ? (
            <div className="empty-state">
              <strong>No calls yet</strong>
              Once the voice agent takes its first call, it&apos;ll show up here.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Caller</th>
                  <th>Outcome</th>
                  <th>Duration</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {calls.slice(0, 8).map((call) => (
                  <tr key={call.id}>
                    <td className="mono">{call.caller_phone || "unknown"}</td>
                    <td>
                      <CallOutcomeBadge outcome={call.outcome} />
                    </td>
                    <td className="mono muted">{formatDuration(call.duration_seconds)}</td>
                    <td className="muted">{relativeTime(call.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
