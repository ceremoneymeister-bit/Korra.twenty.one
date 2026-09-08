import { fetchJSON } from "@/lib/api";

export interface AnalysisPlan {
  plan_id: string;
  handoff_id: string;
  snapshot_id: string;
  answers_revision: number;
  limits: { max_files: number; max_pages_per_pdf: number; max_total_pdf_pages: number; max_xlsx_cells: number; max_source_context_bytes?: number; max_model_image_bytes?: number };
  source_ids: string[];
  service_files: number;
  cached_documents: number;
  documents_to_read: number;
  known_pages: number;
  pages_unknown: number;
  can_start: boolean;
  blockers: string[];
  intended_result: string;
}

export interface ProposedQuantity {
  value: string | null;
  unit: string | null;
  basis: "explicit_source" | "operator_statement" | null;
  evidence_ids: string[];
}

export interface ProposedFact {
  fact_id: string;
  subject_id: string;
  field_key: string;
  raw_text: string | null;
  normalized_value: string | boolean | null;
  unit: string | null;
  evidence_ids: string[];
  status: "extracted" | "needs_review" | "conflicting";
  unknown_reason: string | null;
}

export interface AnalysisProposal {
  positions: Array<{
    position_id: string; product_id: string;
    designation_fact_id?: string | null; variant_fact_id?: string | null;
    role: "make" | "buy" | "customer_supplied" | "excluded" | "unknown";
    quantity: ProposedQuantity;
    requirement_fact_ids?: string[];
  }>;
  facts: ProposedFact[];
  evidence: Array<{
    evidence_id: string;
    locator: { kind: "pdf"; page: number } | { kind: "xlsx"; sheet: string; cell: string } | { kind: "cad"; layout: string; entity_handle: string };
  }>;
  relations: Array<{
    relation_id: string; kind: string;
    from: { id: string; kind: string }; to: { id: string; kind: string };
    quantity_per_parent?: ProposedQuantity;
  }>;
  issues: Array<{ issue_id?: string; code: string; question: string; blocks: "composition_acceptance" | "calculation" | "quote" | "none" }>;
  quantity: ProposedQuantity;
}

export interface AnalysisSource {
  source_id: string;
  relative_path: string;
  status: string;
  page_count: number | null;
  inspect_job_id?: string | null;
  render_jobs?: Record<string, string>;
  proposal: AnalysisProposal | null;
}

export interface AnalysisJob {
  job_id: string;
  handoff_id: string;
  plan_id: string;
  status: "queued" | "running" | "completed" | "partial" | "blocked" | "cancelled" | "stale";
  stage: "inventory" | "rendering" | "analysis" | "review";
  summary: { sources_total: number; sources_complete: number; sources_failed: number };
  rows: AnalysisSource[];
  issues: Array<{ source_id: string; relative_path: string; code: string; next_action: string }>;
  cancellable: boolean;
  retryable: boolean;
}

export interface IntakeAnalysis {
  plan: AnalysisPlan | null;
  job: AnalysisJob | null;
}

function path(handoffId: string): string {
  return `/api/calc/intake-handoffs/${encodeURIComponent(handoffId)}/analysis`;
}

export function getIntakeAnalysis(handoffId: string): Promise<IntakeAnalysis> {
  return fetchJSON<IntakeAnalysis>(path(handoffId));
}

export function planIntakeAnalysis(handoffId: string): Promise<AnalysisPlan> {
  return fetchJSON<AnalysisPlan>(`${path(handoffId)}/plan`, { method: "POST" });
}

export function actOnIntakeAnalysis(
  handoffId: string,
  action: "start" | "retry" | "cancel",
  payload: { plan_id?: string; job_id?: string; request_id?: string },
): Promise<AnalysisJob> {
  return fetchJSON<AnalysisJob>(`${path(handoffId)}/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}
