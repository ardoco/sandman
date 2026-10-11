package edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.classifier;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.FakeGateway;
import edu.kit.kastel.mcse.ardoco.sandman.tlr.lissa.knowledge.Element;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;

class ClassifierTest {

    private static final String UPSTREAM_SIMPLE_PROMPT =
            "Question: Here are two parts of software development artifacts.\n\n"
                    + "requirement: '''req text'''\n\n"
                    + "source code: '''code text'''\n"
                    + "Are they related?\n\n"
                    + "Answer with 'yes' or 'no'.\n";

    private final Element source = new Element("r1", "requirement", "req text", 0, null, true);
    private final Element target = new Element("c1", "source code", "code text", 0, null, true);

    private SimpleClassifier simple(FakeGateway gateway, int threads, AtomicBoolean cancelled) {
        return new SimpleClassifier(
                threads,
                cancelled::get,
                new LissaChat(gateway.client(), "chat-default", 0.0, 7),
                SimpleClassifier.DEFAULT_TEMPLATE);
    }

    @Test
    void simplePromptBytesEqualUpstream() {
        try (FakeGateway gateway = new FakeGateway()) {
            simple(gateway, 1, new AtomicBoolean()).classify(new ClassificationTask(source, target, false));
            var body = gateway.chatBodies.get(0);
            assertEquals(1, body.get("messages").size());
            assertEquals("user", body.get("messages").get(0).get("role").asString());
            assertEquals(
                    UPSTREAM_SIMPLE_PROMPT,
                    body.get("messages").get(0).get("content").asString());
            assertEquals(0.0, body.get("temperature").asDouble());
            assertEquals(7, body.get("seed").asInt());
        }
    }

    @Test
    void simpleAcceptsAnyAnswerContainingYesIgnoreCase() {
        try (FakeGateway gateway = new FakeGateway()) {
            var classifier = simple(gateway, 1, new AtomicBoolean());
            gateway.chatAnswer = b -> "Yes, they are.";
            assertTrue(classifier
                    .classify(new ClassificationTask(source, target, false))
                    .isPresent());
            gateway.chatAnswer = b -> "no";
            assertTrue(classifier
                    .classify(new ClassificationTask(source, target, false))
                    .isEmpty());
        }
    }

    @Test
    void simpleStripsThinkBlock() {
        try (FakeGateway gateway = new FakeGateway()) {
            var classifier = simple(gateway, 1, new AtomicBoolean());
            gateway.chatAnswer = b -> "<think>yes yes</think>\nno";
            assertTrue(classifier
                    .classify(new ClassificationTask(source, target, false))
                    .isEmpty());
            gateway.chatAnswer = b -> "<think>no</think> YES";
            assertTrue(classifier
                    .classify(new ClassificationTask(source, target, false))
                    .isPresent());
        }
    }

    @Test
    void reasoningSendsSystemAndUserMessageAndReadsTraceTag() {
        try (FakeGateway gateway = new FakeGateway()) {
            var classifier = new ReasoningClassifier(
                    1,
                    () -> false,
                    new LissaChat(gateway.client(), "chat-default", 0.0, null),
                    ReasoningClassifierPrompt.REASON_WITH_NAME.getPromptTemplate());
            gateway.chatAnswer = b -> "because. <trace>YES</trace>";
            assertTrue(classifier
                    .classify(new ClassificationTask(source, target, false))
                    .isPresent());
            var messages = gateway.chatBodies.get(0).get("messages");
            assertEquals("system", messages.get(0).get("role").asString());
            assertTrue(messages.get(1).get("content").asString().contains("(1) requirement: '''req text''' "));
            gateway.chatAnswer = b -> "yes without tag";
            assertTrue(classifier
                    .classify(new ClassificationTask(source, target, false))
                    .isEmpty());
        }
    }

    @Test
    void cancellationStopsNewCalls() {
        try (FakeGateway gateway = new FakeGateway()) {
            AtomicBoolean cancelled = new AtomicBoolean(true);
            var tasks = List.of(
                    new ClassificationTask(source, target, false), new ClassificationTask(source, target, false));
            assertThrows(
                    CancellationException.class,
                    () -> simple(gateway, 1, cancelled).classify(tasks));
            assertThrows(
                    CancellationException.class,
                    () -> simple(gateway, 4, cancelled).classify(tasks));
            assertEquals(0, gateway.chatBodies.size());
        }
    }

    @Test
    void workerFailureReachesCaller() {
        try (FakeGateway gateway = new FakeGateway()) {
            var classifier = new SimpleClassifier(
                    4,
                    () -> false,
                    new LissaChat(gateway.client(), "unknown-route", 0.0, null),
                    SimpleClassifier.DEFAULT_TEMPLATE);
            var tasks = List.of(new ClassificationTask(source, target, false));
            assertThrows(RuntimeException.class, () -> classifier.classify(tasks));
        }
    }
}
