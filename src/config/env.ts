import { z } from "zod";

const EnvSchema = z.object({
  // Slack
  SLACK_BOT_TOKEN: z.string().min(1),
  SLACK_APP_TOKEN: z.string().min(1).optional(),
  SLACK_SIGNING_SECRET: z.string().min(1).optional(),
  PORT: z.coerce.number().int().positive().default(3000),

  // Ollama
  OLLAMA_BASE_URL: z.string().url().default("http://127.0.0.1:11434"),
  OLLAMA_API_KEY: z.string().min(1).optional(),
  OLLAMA_MODEL: z.string().min(1).default("llama3.2:latest"),

  // Web search (optional)
  TAVILY_API_KEY: z.string().min(1).optional(),
});

export type Env = z.infer<typeof EnvSchema>;

export function getEnv(raw: Record<string, string | undefined>): Env {
  const parsed = EnvSchema.safeParse(raw);
  if (!parsed.success) {
    const msg = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("\n");
    throw new Error(`Invalid environment:\n${msg}`);
  }
  return parsed.data;
}

