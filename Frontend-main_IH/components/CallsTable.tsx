"use client";

import { Fragment, useMemo, useState } from "react";
import { CallLog } from "@/lib/types";
import { CallOutcomeBadge } from "@/components/StatusBadge";
import styles from "./CallsTable.module.css";

const OUTCOMES = [
  { value: "", label: "All outcomes" },
  { value: "meeting_booked", label: "Meeting booked" },
  { value: "warm_lead", label: "Warm lead" },
  { value: "info_inquiry", label: "Info inquiry" },
  { value: "transferred", label: "Transferred" },
  { value: "dropped_call", label: "Customer support" },
];

function formatDuration(seconds: number | null): string {
  if (!seconds && seconds !== 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function CallsTable({ calls }: { calls: CallLog[] }) {
  const [search, setSearch] = useState("");
  const [outcome, setOutcome] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const filtered = useMemo(() => {
    return calls.filter((call) => {
      if (outcome && call.outcome !== outcome) return false;
      if (search && !(call.caller_phone || "").includes(search.trim())) return false;
      return true;
    });
  }, [calls, search, outcome]);

  if (calls.length === 0) {
    return (
      <div className="panel empty-state">
        <strong>No calls yet</strong>
        Once the voice agent takes its first call, it&apos;ll show up here.
      </div>
    );
  }

  return (
    <div>
      <div className={styles.filters}>
        <input
          className={styles.search}
          placeholder="Search by phone number"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={styles.select} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          {OUTCOMES.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="panel">
        {filtered.length === 0 ? (
          <div className="empty-state">
            <strong>No matching calls</strong>
            Try a different phone number or outcome filter.
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Caller</th>
                <th>Outcome</th>
                <th>Duration</th>
                <th>Time</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((call) => {
                const expanded = expandedId === call.id;
                return (
                  <Fragment key={call.id}>
                    <tr
                      className={call.transcript ? styles.clickableRow : undefined}
                      onClick={() => call.transcript && setExpandedId(expanded ? null : call.id)}
                    >
                      <td className="mono">{call.caller_phone || "unknown"}</td>
                      <td>
                        <CallOutcomeBadge outcome={call.outcome} />
                      </td>
                      <td className="mono muted">{formatDuration(call.duration_seconds)}</td>
                      <td className="muted">{formatTimestamp(call.created_at)}</td>
                      <td className={styles.expandCell}>
                        {call.transcript ? (expanded ? "Hide transcript" : "View transcript") : ""}
                      </td>
                    </tr>
                    {expanded && call.transcript && (
                      <tr>
                        <td colSpan={5} className={styles.transcriptCell}>
                          <pre className={styles.transcript}>{call.transcript}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
