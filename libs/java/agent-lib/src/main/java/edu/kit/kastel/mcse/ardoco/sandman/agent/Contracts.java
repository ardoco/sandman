package edu.kit.kastel.mcse.ardoco.sandman.agent;

import com.networknt.schema.Error;
import com.networknt.schema.SchemaLocation;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SchemaRegistryConfig;
import com.networknt.schema.SpecificationVersion;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Stream;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.dataformat.yaml.YAMLMapper;

/** Validation against the JSON Schemas in contracts/; $id URIs resolve to the local files, never the network. */
public final class Contracts {

    static final List<String> SCHEMA_FILES =
            List.of("common.schema.json", "skills.schema.json", "events.schema.json", "knowledge.schema.json");

    /** Instance does not match its contract; carries every validation message. */
    public static class ContractException extends RuntimeException {
        private final String ref;
        private final List<String> messages;

        public ContractException(String ref, List<String> messages) {
            super(ref + ": " + String.join("; ", messages));
            this.ref = ref;
            this.messages = List.copyOf(messages);
        }

        public String ref() {
            return ref;
        }

        public List<String> messages() {
            return messages;
        }
    }

    private final Map<String, String> base = new HashMap<>(); // file name -> $id
    private final SchemaRegistry registry;

    public Contracts(Path contractsDir) {
        Map<String, String> byId = new HashMap<>();
        List<String> files = new java.util.ArrayList<>(SCHEMA_FILES);
        try (Stream<Path> configs = Files.list(contractsDir.resolve("config"))) {
            configs.map(p -> "config/" + p.getFileName())
                    .filter(f -> f.endsWith(".schema.json"))
                    .sorted()
                    .forEach(files::add);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        for (String file : files) {
            try {
                String text = Files.readString(contractsDir.resolve(file));
                String id = JsonMapper.shared().readTree(text).get("$id").asString();
                base.put(file, id);
                byId.put(id, text);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
        // English messages regardless of the JVM locale (they end up in sandman.error)
        var config = SchemaRegistryConfig.builder().locale(Locale.ROOT).build();
        registry = SchemaRegistry.withDefaultDialect(
                SpecificationVersion.DRAFT_2020_12, b -> b.schemas(byId).schemaRegistryConfig(config));
    }

    /** Ref like 'skills.schema.json#/$defs/X' or a full $id URI (with optional fragment). */
    String uri(String ref) {
        int hash = ref.indexOf('#');
        String file = hash < 0 ? ref : ref.substring(0, hash);
        return base.getOrDefault(file, file) + (hash < 0 ? "" : ref.substring(hash));
    }

    /** Throws ContractException with all messages if the instance does not match ref. */
    public void validate(String ref, JsonNode instance) {
        List<Error> errors = registry.getSchema(SchemaLocation.of(uri(ref))).validate(instance);
        if (!errors.isEmpty()) {
            throw new ContractException(
                    ref, errors.stream().map(Error::toString).toList());
        }
    }

    /** YAML file validated against contracts/config/{schema}.schema.json (e.g. 'tlr-approaches'); empty file = {}. */
    public JsonNode loadConfig(String schema, Path file) {
        JsonNode config;
        try {
            config = new YAMLMapper().readTree(Files.readString(file));
        } catch (IOException e) {
            throw new Settings.ConfigException("cannot read " + file + ": " + e, e);
        }
        if (config == null || config.isMissingNode() || config.isNull()) {
            config = JsonMapper.shared().createObjectNode();
        }
        try {
            validate("config/" + schema + ".schema.json", config);
        } catch (ContractException e) {
            throw new Settings.ConfigException("invalid config " + file + ": " + String.join("; ", e.messages()), e);
        }
        return config;
    }

    public boolean isValid(String ref, JsonNode instance) {
        try {
            validate(ref, instance);
            return true;
        } catch (ContractException e) {
            return false;
        }
    }
}
