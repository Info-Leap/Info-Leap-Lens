export type Message = {
  role: "user" | "model";
  content: string;
  // metadata on model messages
  skill?: string;
  sql?: string;
  reasoning?: string; // Pre-execution
  synthesis?: string; // Post-execution cross-check
  row_count?: number;
  complexity?: string;
  plan_reasoning?: string;
  plan_steps?: { step_id: number; skill: string; sub_question: string }[];
  validation_ok?: boolean;
  validation_warnings?: string;
  chart?: Record<string, unknown> | null;
  error?: string;
  latency_ms?: number;
  rating?: number; // 1 for good, -1 for bad, 0 for none
};

export type OxDataResponse = {
  text: string;
  chart: Record<string, unknown> | null;
  skill: string;
  sql: string;
  reasoning: string;
  synthesis: string;
  row_count: number;
  complexity: string;
  plan_reasoning: string;
  plan_steps: { step_id: number; skill: string; sub_question: string }[];
  validation_ok: boolean;
  validation_warnings: string;
  error: string;
  latency_ms: number;
};

let currentSessionId = "";

function getSessionId(): string {
  if (!currentSessionId) {
    currentSessionId = Math.random().toString(36).substring(2, 10);
  }
  return currentSessionId;
}

export function getCurrentSessionId(): string {
  return currentSessionId;
}

export function newSession(): void {
  currentSessionId = Math.random().toString(36).substring(2, 10);
}

export async function sendMessageToBackend(messages: Message[]): Promise<OxDataResponse> {
  // Strip metadata — only send role + content to backend
  const payload = messages.map(m => ({ role: m.role, content: m.content }));
  const session_id = getSessionId();

  const response = await fetch(`${import.meta.env.VITE_API_URL || ""}/api/chat`, {
    method: "POST",
    headers: { 
      "Content-Type": "application/json",
      "Bypass-Tunnel-Reminder": "true",
      "ngrok-skip-browser-warning": "69420"
    },
    body: JSON.stringify({ messages: payload, session_id }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error || "Failed to communicate with backend");
  }

  return response.json() as Promise<OxDataResponse>;
}

export async function sendFeedback(index: number, rating: number, comment: string = ""): Promise<void> {
  const session_id = getSessionId();
  await fetch(`${import.meta.env.VITE_API_URL || ""}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id, message_index: index, rating, comment }),
  });
}

// Skill display metadata
export const SKILL_META: Record<string, { icon: string; label: string; color: string }> = {
  awareness:   { icon: "👁️",  label: "Brand Awareness",  color: "bg-blue-100 text-blue-800" },
  nps:         { icon: "⭐",  label: "NPS",              color: "bg-purple-100 text-purple-800" },
  ownership:   { icon: "🏠",  label: "Ownership",        color: "bg-green-100 text-green-800" },
  room:        { icon: "💡",  label: "Room Appliances",  color: "bg-yellow-100 text-yellow-800" },
  purchase:    { icon: "🛒",  label: "Purchase",         color: "bg-orange-100 text-orange-800" },
  demographic: { icon: "👥",  label: "Demographics",     color: "bg-gray-100 text-gray-800" },
  general:     { icon: "🔍",  label: "General",          color: "bg-slate-100 text-slate-800" },
  summary:     { icon: "📝",  label: "Summary",          color: "bg-indigo-100 text-indigo-800" },
};
