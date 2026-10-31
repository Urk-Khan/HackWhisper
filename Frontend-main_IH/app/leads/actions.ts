"use server";

import { revalidatePath } from "next/cache";
import { patchRow } from "@/lib/supabase";
import { LeadStatus } from "@/lib/types";

const VALID_STATUSES: LeadStatus[] = ["warm", "contacted", "converted", "cold"];

export async function updateLeadStatus(leadId: number, status: string) {
  if (!VALID_STATUSES.includes(status as LeadStatus)) {
    throw new Error(`Invalid lead status: ${status}`);
  }
  await patchRow("leads", leadId, { status });
  revalidatePath("/leads");
}
