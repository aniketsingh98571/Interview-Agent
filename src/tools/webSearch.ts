import { z } from "zod";

export const WebSearchInputSchema = z.object({
  query: z.string().min(1),
});

export type WebSearchInput = z.infer<typeof WebSearchInputSchema>;

type WebSearchResult = {
  ok: true;
  query: string;
  summary: string;
  source: "tavily" | "duckduckgo";
};

async function duckDuckGoInstantAnswer(query: string): Promise<string> {
  const url = new URL("https://api.duckduckgo.com/");
  url.searchParams.set("q", query);
  url.searchParams.set("format", "json");
  url.searchParams.set("no_html", "1");
  url.searchParams.set("skip_disambig", "1");

  const resp = await fetch(url, {
    method: "GET",
    headers: { "User-Agent": "InterviewFeedbackAgent/1.0" },
  });
  if (!resp.ok) throw new Error(`DuckDuckGo web search failed: HTTP ${resp.status}`);
  const data = (await resp.json()) as any;

  const parts: string[] = [];
  const abs = String(data?.AbstractText ?? "").trim();
  if (abs) parts.push(abs);
  const ans = String(data?.Answer ?? "").trim();
  if (ans && ans !== abs) parts.push(ans);
  const related = Array.isArray(data?.RelatedTopics) ? data.RelatedTopics : [];
  for (const t of related) {
    if (t && typeof t === "object") {
      const tx = String((t as any).Text ?? "").trim();
      if (tx) parts.push(tx);
    }
    if (parts.join("\n\n").length > 3500) break;
  }
  const out = parts.join("\n\n").replace(/\s+/g, " ").trim();
  return out || "No instant answer from web search for this query. Try rephrasing.";
}

async function tavilySearch(apiKey: string, query: string): Promise<string> {
  const resp = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      query,
      max_results: 5,
      include_answer: true,
      include_raw_content: false,
    }),
  });
  if (!resp.ok) throw new Error(`Tavily web search failed: HTTP ${resp.status}`);
  const data = (await resp.json()) as any;
  const answer = String(data?.answer ?? "").trim();
  const results = Array.isArray(data?.results) ? data.results : [];
  const lines: string[] = [];
  if (answer) lines.push(answer);
  for (const r of results) {
    const title = String(r?.title ?? "").trim();
    const content = String(r?.content ?? "").trim();
    const u = String(r?.url ?? "").trim();
    const s = [title, content, u].filter(Boolean).join(" — ");
    if (s) lines.push(s);
  }
  const out = lines.join("\n\n").slice(0, 3500);
  return out || "No results.";
}

export async function webSearch(input: WebSearchInput, env: { TAVILY_API_KEY?: string }): Promise<WebSearchResult> {
  const query = input.query.trim();
  if (env.TAVILY_API_KEY) {
    const summary = await tavilySearch(env.TAVILY_API_KEY, query);
    return { ok: true, query, summary, source: "tavily" };
  }
  const summary = await duckDuckGoInstantAnswer(query);
  return { ok: true, query, summary, source: "duckduckgo" };
}

