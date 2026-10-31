"use client";

import { useState, useTransition } from "react";
import { Lead, LeadStatus } from "@/lib/types";
import { updateLeadStatus } from "@/app/leads/actions";
import { LeadStatusBadge } from "@/components/StatusBadge";
import styles from "./LeadsTable.module.css";

const STATUSES: LeadStatus[] = ["warm", "contacted", "converted", "cold"];

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function LeadsTable({ leads }: { leads: Lead[] }) {
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [isPending, startTransition] = useTransition();
  const [localStatuses, setLocalStatuses] = useState<Record<number, LeadStatus>>({});

  function handleChange(leadId: number, status: string) {
    setPendingId(leadId);
    setLocalStatuses((prev) => ({ ...prev, [leadId]: status as LeadStatus }));
    startTransition(async () => {
      await updateLeadStatus(leadId, status);
      setPendingId(null);
    });
  }

  if (leads.length === 0) {
    return (
      <div className="panel empty-state">
        <strong>No leads yet</strong>
        Callers who show interest without booking will show up here.
      </div>
    );
  }

  return (
    <div className="panel">
      <table className="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Phone</th>
            <th>Interested in</th>
            <th>Reason</th>
            <th>Status</th>
            <th>Captured</th>
          </tr>
        </thead>
        <tbody>
          {leads.map((lead) => {
            const currentStatus = localStatuses[lead.id] ?? lead.status ?? "warm";
            return (
              <tr key={lead.id}>
                <td>{lead.name || "—"}</td>
                <td className="mono">{lead.phone || "—"}</td>
                <td className="muted">{lead.interested_service || "—"}</td>
                <td className="muted">{lead.reason || "—"}</td>
                <td>
                  <div className={styles.statusCell}>
                    <LeadStatusBadge status={currentStatus} />
                    <select
                      className={styles.select}
                      value={currentStatus}
                      disabled={isPending && pendingId === lead.id}
                      onChange={(e) => handleChange(lead.id, e.target.value)}
                    >
                      {STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </div>
                </td>
                <td className="muted">{formatTimestamp(lead.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
