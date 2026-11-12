package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

/** ArTEMiS in ExArch (T3.8): the LLM derives the architecture from the document, ArTEMiS links its components, ArCoTL links them to code. */
public final class ArtemisExarchApproach extends ArtemisApproachBase {

    public static final String ID = "artemis-exarch";
    public static final String VERSION = ArtemisApproachBase.VERSION;

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public ArtemisExarchApproach() {
        this(null);
    }

    ArtemisExarchApproach(CodeModelClient codeModel) {
        super(ID, Flavor.EXARCH, codeModel);
    }
}
