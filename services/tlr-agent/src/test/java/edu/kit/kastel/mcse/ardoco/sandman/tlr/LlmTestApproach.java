package edu.kit.kastel.mcse.ardoco.sandman.tlr;

import edu.kit.kastel.mcse.ardoco.sandman.agent.LlmClient;
import java.util.List;
import java.util.Set;

/** Test-only approach that asks the LLM once per run; the stub server decides the answer. */
public final class LlmTestApproach implements TraceLinkApproach {

    @Override
    public String id() {
        return "test-llm";
    }

    @Override
    public String version() {
        return "1";
    }

    @Override
    public boolean usesLlm() {
        return true;
    }

    @Override
    public Set<KindPair> supports() {
        return Set.of(new KindPair("test", "other"));
    }

    @Override
    public List<String> promptIds(TlrContext ctx) {
        return List.of("test-llm@abcd1234");
    }

    @Override
    public List<CandidateLink> recover(TlrInput in, TlrContext ctx) {
        ctx.llmClient().chat(ctx.llm().chatRoute(), List.of(new LlmClient.Message("user", "hi")), 0, 1);
        return List.of();
    }
}
