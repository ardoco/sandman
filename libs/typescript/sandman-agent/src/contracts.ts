import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { Ajv2020 } from "ajv/dist/2020.js";
import type { ValidateFunction } from "ajv";

const SCHEMA_FILES = ["common.schema.json", "skills.schema.json", "events.schema.json", "knowledge.schema.json"];

export class ContractError extends Error {
  constructor(
    readonly ref: string,
    readonly messages: string[],
  ) {
    super(`${ref}: ${messages.join("; ")}`);
  }
}

export class Contracts {
  private readonly ajv: Ajv2020;
  private readonly base = new Map<string, string>();
  private readonly validators = new Map<string, ValidateFunction>();

  constructor(contractsDir: string) {
    // validateFormats false: the Python kit does not check formats either (parity)
    this.ajv = new Ajv2020({ allErrors: true, strict: false, validateFormats: false });
    const configDir = join(contractsDir, "config");
    const names = [
      ...SCHEMA_FILES,
      ...readdirSync(configDir)
        .filter((f) => f.endsWith(".schema.json"))
        .sort()
        .map((f) => `config/${f}`),
    ];
    for (const name of names) {
      const schema = JSON.parse(readFileSync(join(contractsDir, name), "utf-8")) as { $id: string };
      this.base.set(name, schema.$id);
      this.ajv.addSchema(schema);
    }
  }

  private uri(ref: string): string {
    const i = ref.indexOf("#");
    const file = i < 0 ? ref : ref.slice(0, i);
    return (this.base.get(file) ?? file) + (i < 0 ? "" : ref.slice(i));
  }

  validate(ref: string, instance: unknown): void {
    const uri = this.uri(ref);
    const validator = this.validators.get(uri) ?? this.ajv.compile({ $ref: uri });
    this.validators.set(uri, validator);
    if (!validator(instance)) {
      throw new ContractError(
        ref,
        (validator.errors ?? []).map((e) => `${e.instancePath || "<root>"}: ${e.message ?? "invalid"}`),
      );
    }
  }

  isValid(ref: string, instance: unknown): boolean {
    try {
      this.validate(ref, instance);
      return true;
    } catch (e) {
      if (e instanceof ContractError) return false;
      throw e;
    }
  }
}
