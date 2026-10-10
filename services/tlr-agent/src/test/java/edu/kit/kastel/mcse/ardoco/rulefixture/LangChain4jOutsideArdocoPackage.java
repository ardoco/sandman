package edu.kit.kastel.mcse.ardoco.rulefixture;

import dev.langchain4j.model.chat.ChatModel;

/** Negative fixture of ModuleRulesTest; its package is outside the packages the real rule checks. */
public abstract class LangChain4jOutsideArdocoPackage {

    protected ChatModel model;
}
