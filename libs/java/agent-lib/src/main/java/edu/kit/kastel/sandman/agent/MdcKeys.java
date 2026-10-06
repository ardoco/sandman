package edu.kit.kastel.sandman.agent;

/** SLF4J MDC keys of the JSON log fields (Stage 2 §1.7); the JSON format is configured by the applications. */
public final class MdcKeys {

    public static final String WORKFLOW_ID = "workflowId";
    public static final String TASK_ID = "taskId";
    public static final String RUN_ID = "runId";
    public static final String PROJECT_ID = "projectId";
    public static final String ACTOR = "actor";

    private MdcKeys() {}
}
