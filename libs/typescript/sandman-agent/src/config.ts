import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const DEFAULT_CONTRACTS_DIR = fileURLToPath(new URL("../../../../contracts", import.meta.url));

export class ConfigError extends Error {}

export const LLM_MODES = ["live", "record", "replay"] as const;
export type LlmMode = (typeof LLM_MODES)[number];
type Env = Record<string, string | undefined>;

export function llmMode(env: Env = process.env): LlmMode {
  const mode = env.SANDMAN_LLM_MODE || "live";
  if (!(LLM_MODES as readonly string[]).includes(mode)) {
    throw new ConfigError(`invalid SANDMAN_LLM_MODE '${mode}', expected one of ${LLM_MODES.join(", ")}`);
  }
  return mode as LlmMode;
}

export interface Settings {
  serviceName: string;
  contractsDir: string;
  llmBaseUrl?: string;
  llmKey?: string;
  llmLocalKey?: string;
  kgUri?: string;
  kgUser?: string;
  kgPassword?: string;
  natsUrl?: string;
  serviceToken?: string;
  serviceVerify: Record<string, string>;
  pluginInboundToken?: string;
}

function readFile(name: string, path: string): string {
  try {
    return readFileSync(path, "utf-8");
  } catch (e) {
    throw new ConfigError(`cannot read ${name}=${path}: ${(e as Error).message}`);
  }
}

export function settingsFromEnv(env: Env = process.env): Settings {
  const secret = (name: string): string | undefined => {
    const file = env[`${name}_FILE`];
    return (file === undefined ? env[name] : readFile(`${name}_FILE`, file).trim()) || undefined;
  };
  const plain = (name: string): string | undefined => env[name] || undefined;
  const serviceName = env.SANDMAN_SERVICE_NAME;
  if (!serviceName) throw new ConfigError("SANDMAN_SERVICE_NAME is required");
  const verifyFile = plain("SANDMAN_SERVICE_VERIFY_FILE");
  let serviceVerify: Record<string, string> = {};
  if (verifyFile) {
    try {
      serviceVerify = JSON.parse(readFile("SANDMAN_SERVICE_VERIFY_FILE", verifyFile)) as Record<string, string>;
    } catch (e) {
      if (e instanceof ConfigError) throw e;
      throw new ConfigError(`cannot read SANDMAN_SERVICE_VERIFY_FILE=${verifyFile}: ${(e as Error).message}`);
    }
  }
  return {
    serviceName,
    contractsDir: plain("SANDMAN_CONTRACTS_DIR") ?? DEFAULT_CONTRACTS_DIR,
    llmBaseUrl: plain("SANDMAN_LLM_BASE_URL"),
    llmKey: secret("SANDMAN_LLM_KEY"),
    llmLocalKey: secret("SANDMAN_LLM_LOCAL_KEY"),
    kgUri: plain("SANDMAN_KG_URI"),
    kgUser: plain("SANDMAN_KG_USER"),
    kgPassword: secret("SANDMAN_KG_PASSWORD"),
    natsUrl: plain("SANDMAN_NATS_URL"),
    serviceToken: secret("SANDMAN_SERVICE_TOKEN"),
    serviceVerify,
    pluginInboundToken: secret("SANDMAN_PLUGIN_INBOUND_TOKEN"),
  };
}

export function requireSettings(settings: Settings, ...names: (keyof Settings)[]): void {
  const missing = names.filter((n) => {
    const v = settings[n];
    return v === undefined || v === "" || (typeof v === "object" && Object.keys(v).length === 0);
  });
  if (missing.length > 0) {
    const envNames = missing.map((n) => "SANDMAN_" + n.replace(/[A-Z]/g, (c) => `_${c}`).toUpperCase());
    throw new ConfigError(`${settings.serviceName}: missing configuration ${envNames.join(", ")}`);
  }
}
