import { fetchJSON } from "@/lib/api";

export interface IntakeInitialAnswers {
  scope: "whole" | "selected" | "unknown";
  scope_note: string;
  more_documents: "yes" | "no" | "unknown";
  quantity_source: "in_documents" | "separate" | "unknown";
  notes: string;
}

export interface IntakePreparation {
  handoff_id: string;
  order_id: string;
  snapshot_id: string;
  document_set_revision: number;
  editable: boolean;
  summary: {
    files_total: number;
    engineering_documents: number;
    service_files: number;
    cached_engineering_documents: number;
    documents_without_observation: number;
    unsupported_documents: number;
    service_file_names: string[];
    classification_pending?: boolean;
    classification_issues?: Array<{
      source_id: string;
      relative_path: string;
      reason: string;
      message: string;
    }>;
    failed_documents?: number;
    partial_documents?: number;
  };
  initial_answers: {
    revision: number;
    answers: IntakeInitialAnswers;
    receipt: null | {
      actor: string;
      source: string;
      recorded_at: number;
      decision_id: string;
    };
  };
}

export interface SaveIntakeAnswersRequest {
  snapshot_id: string;
  expected_revision: number;
  request_id: string;
  answers: IntakeInitialAnswers;
}

function preparationPath(handoffId: string): string {
  return `/api/calc/intake-handoffs/${encodeURIComponent(handoffId)}/preparation`;
}

export function getIntakePreparation(handoffId: string): Promise<IntakePreparation> {
  return fetchJSON<IntakePreparation>(preparationPath(handoffId));
}

export function saveIntakeAnswers(
  handoffId: string,
  request: SaveIntakeAnswersRequest,
): Promise<IntakePreparation> {
  return fetchJSON<IntakePreparation>(`${preparationPath(handoffId)}/answers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
