// Thin API client. Same-origin in dev via Vite proxy (/api -> :8000).

export interface GameInfo {
  game_id: string;
  edition: string;
  display_name: string;
  franchise: string;
  platforms: string[];
  supported_sources: string[];
  metric_keys: string[];
}

export interface CreateMatchResponse {
  match_id: string;
  status: string;
  frames_endpoint: string;
  complete_endpoint: string;
  progress_endpoint: string;
}

export async function listGames(): Promise<GameInfo[]> {
  const r = await fetch("/api/games");
  if (!r.ok) throw new Error("failed to list games");
  return r.json();
}

export async function createMatch(body: {
  game_id: string;
  edition: string;
  source_type: string;
  capture: Record<string, unknown>;
}): Promise<CreateMatchResponse> {
  const r = await fetch("/api/matches", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error("failed to create match");
  return r.json();
}

export async function uploadFrame(
  matchId: string,
  index: number,
  timestampMs: number,
  blob: Blob
): Promise<void> {
  const fd = new FormData();
  fd.append("index", String(index));
  fd.append("timestamp_ms", String(timestampMs));
  fd.append("file", blob, `${index}.jpg`);
  const r = await fetch(`/api/matches/${matchId}/frames`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`frame ${index} upload failed`);
}

export async function uploadSource(matchId: string, file: File): Promise<void> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  const r = await fetch(`/api/matches/${matchId}/source`, { method: "POST", body: fd });
  if (!r.ok) throw new Error("video upload failed");
}

export async function completeMatch(matchId: string): Promise<void> {
  const r = await fetch(`/api/matches/${matchId}/complete`, { method: "POST" });
  if (!r.ok) throw new Error("failed to complete match");
}

export async function getMatch(matchId: string): Promise<any> {
  const r = await fetch(`/api/matches/${matchId}`);
  if (!r.ok) throw new Error("failed to fetch match");
  return r.json();
}

export async function getTrends(gameId: string, edition: string): Promise<any[]> {
  const r = await fetch(`/api/matches/trends/${gameId}/${edition}`);
  if (!r.ok) return [];
  return r.json();
}

export async function getUsage(): Promise<any | null> {
  const r = await fetch("/api/usage");
  return r.ok ? r.json() : null;
}

export function frameUrl(matchId: string, key: string): string {
  return `/api/matches/${matchId}/frame?key=${encodeURIComponent(key)}`;
}

export function clipUrl(matchId: string, key: string): string {
  return `/api/matches/${matchId}/clip?key=${encodeURIComponent(key)}`;
}

// A frame_ref is a real object key (image we can serve) vs a bare frame index.
export function isFrameKey(ref: string): boolean {
  return typeof ref === "string" && ref.includes("/frames/");
}

// Simple concurrency-limited runner for frame uploads.
export async function runPool<T>(items: T[], limit: number, worker: (item: T) => Promise<void>) {
  let i = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (i < items.length) {
      const idx = i++;
      await worker(items[idx]);
    }
  });
  await Promise.all(runners);
}
