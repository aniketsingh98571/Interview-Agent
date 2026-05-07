import { createOllama } from "ollama-ai-provider-v2";

function requiredEnv(name: string): string {
  const v = process.env[name];
  if (!v || !v.trim()) throw new Error(`Missing required env var: ${name}`);
  return v.trim();
}

export function getOllamaModel(modelOverride?: string) {
  const baseURL = requiredEnv("OLLAMA_BASE_URL").replace(/\/+$/g, "");
  const providerBaseURL = baseURL.endsWith("/api") ? baseURL : `${baseURL}/api`;
  const apiKey = process.env.OLLAMA_API_KEY?.trim();
  const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined;

  const ollama = createOllama({ baseURL: providerBaseURL, headers });
  return ollama(modelOverride ?? requiredEnv("OLLAMA_MODEL"));
}

