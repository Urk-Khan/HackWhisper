import { fetchRows } from "@/lib/supabase";
import { Lead } from "@/lib/types";
import LeadsTable from "@/components/LeadsTable";

export const dynamic = "force-dynamic";

export default async function LeadsPage() {
  let leads: Lead[] = [];
  let loadError: string | null = null;

  try {
    leads = await fetchRows<Lead>("leads", "select=*&order=created_at.desc&limit=300");
  } catch (err) {
    loadError = err instanceof Error ? err.message : "Unknown error";
  }

  return (
    <div>
      <header className="page-header">
        <h1 className="page-title">Leads</h1>
        <p className="page-subtitle">
          Callers who showed interest but didn&apos;t book. Update status as your team works each one.
        </p>
      </header>

      {loadError ? (
        <div className="panel empty-state">
          <strong>Couldn&apos;t load leads</strong>
          {loadError}
        </div>
      ) : (
        <LeadsTable leads={leads} />
      )}
    </div>
  );
}
