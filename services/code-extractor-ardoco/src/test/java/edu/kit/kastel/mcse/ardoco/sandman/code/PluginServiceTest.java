package edu.kit.kastel.mcse.ardoco.sandman.code;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.agent.Settings;
import edu.kit.kastel.mcse.ardoco.sandman.agent.a2a.A2aClient;
import io.quarkus.test.common.http.TestHTTPResource;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.enterprise.inject.Produces;
import jakarta.inject.Singleton;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** The service as the extraction-agent sees it: Agent Card, plugin token, both skills, contract-valid results. */
@QuarkusTest
class PluginServiceTest {

    @Produces
    @Singleton
    static Settings settings() {
        return Fixtures.settings(Fixtures.mappingFixture());
    }

    @TestHTTPResource("/")
    URI url;

    A2aClient client;

    @AfterEach
    void close() {
        if (client != null) {
            client.close();
        }
    }

    A2aClient.TaskSnapshot call(String token, String skill, JsonNode input) throws Exception {
        client = new A2aClient(token);
        A2aClient.TaskSnapshot task = client.submit(
                url.toString(), skill, input, null, null, UUID.randomUUID().toString());
        for (int i = 0; i < 3000 && !task.terminal(); i++) {
            Thread.sleep(20);
            task = client.get(url.toString(), task.taskId());
        }
        assertTrue(task.terminal(), "task did not finish: " + task);
        return task;
    }

    @Test
    void agentCardNamesThePluginAndBothSkills() throws Exception {
        HttpResponse<String> response = HttpClient.newHttpClient()
                .send(
                        HttpRequest.newBuilder(url.resolve("/.well-known/agent-card.json"))
                                .build(),
                        HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
        JsonNode card = JsonMapper.shared().readTree(response.body());
        assertEquals("code-extractor-ardoco", card.get("name").asString());
        assertEquals("2.1.0+c1", card.get("version").asString());
        assertTrue(response.body().contains("sandman.plugin.extract"));
        assertTrue(response.body().contains("sandman.plugin.code-model"));
        HttpResponse<String> health = HttpClient.newHttpClient()
                .send(HttpRequest.newBuilder(url.resolve("/healthz")).build(), HttpResponse.BodyHandlers.ofString());
        assertEquals(200, health.statusCode());
    }

    @Test
    void callsWithoutTheInboundTokenAreRejected() {
        ObjectNode input = Fixtures.extractInput(Fixtures.mappingFixture(), null);
        assertThrows(Exception.class, () -> call("wrong-token", "sandman.plugin.extract", input));
    }

    @Test
    void extractReturnsContractValidElements() throws Exception {
        var task =
                call("inbound-token", "sandman.plugin.extract", Fixtures.extractInput(Fixtures.mappingFixture(), null));
        assertEquals("TASK_STATE_COMPLETED", task.state());
        Fixtures.validateExtract(task.result());
        assertTrue(task.result().get("elements").size() > 20);
    }

    @Test
    void codeModelReturnsTheAcm() throws Exception {
        var task = call(
                "inbound-token",
                "sandman.plugin.code-model",
                CodeModelSkillTest.codeModelInput(Fixtures.mappingFixture()));
        assertEquals("TASK_STATE_COMPLETED", task.state());
        Fixtures.CONTRACTS.validate("skills.schema.json#/$defs/PluginCodeModelOutput", task.result());
        assertTrue(task.result().get("acm").has("codeItemRepository"));
    }

    @Test
    void aStoragePathOutsideTheStoreFailsAsNotRetryableInvalidInput() throws Exception {
        ObjectNode input = Fixtures.extractInput(Fixtures.mappingFixture(), null);
        ((ObjectNode) input.get("artifacts").get(0)).put("storagePath", "/etc/passwd");
        var task = call("inbound-token", "sandman.plugin.extract", input);
        assertEquals("TASK_STATE_FAILED", task.state());
        assertEquals("INVALID_INPUT", task.error().get("code").asString());
        assertFalse(task.error().get("retryable").asBoolean());
    }
}
