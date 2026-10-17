package edu.kit.kastel.mcse.ardoco.sandman.agent.a2a;

import java.util.List;
import org.a2aproject.sdk.server.tasks.AgentEmitter;
import org.a2aproject.sdk.spec.TextPart;

/** Per-task context handed to a skill. */
public final class SkillContext {

    private final String taskId;
    private final String messageId;
    private final String workflowId;
    private final String caller;
    private final String actor;
    private final AgentEmitter emitter;
    private static final long MIN_PROGRESS_NANOS = 2_000_000_000L;
    private volatile boolean cancelled;
    private long lastProgress;
    private boolean progressed;

    SkillContext(
            String taskId, String messageId, String workflowId, String caller, String actor, AgentEmitter emitter) {
        this.taskId = taskId;
        this.messageId = messageId;
        this.workflowId = workflowId;
        this.caller = caller;
        this.actor = actor;
        this.emitter = emitter;
    }

    public String taskId() {
        return taskId;
    }

    /** messageId of the A2A request; causationid of the completion event. */
    public String messageId() {
        return messageId;
    }

    /** sandman.workflowId metadata, or null. */
    public String workflowId() {
        return workflowId;
    }

    /** Authenticated caller (service name, or "owner" in plugin mode). */
    public String caller() {
        return caller;
    }

    /** sandman.actor metadata if sent by the orchestrator, else null. */
    public String actor() {
        return actor;
    }

    /** True once CancelTask was received; long-running skills should poll it and stop. */
    public boolean cancelled() {
        return cancelled;
    }

    void cancel() {
        cancelled = true;
    }

    /** Reports the current activity as a TASK_STATE_WORKING status message; at most one per 2 s, others are dropped. */
    public synchronized void progress(String text) {
        long now = System.nanoTime();
        if (progressed && now - lastProgress < MIN_PROGRESS_NANOS) {
            return;
        }
        progressed = true;
        lastProgress = now;
        emitter.startWork(emitter.newAgentMessage(List.of(new TextPart(text)), null));
    }
}
