package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

/** ArTEMiS SAD to code by class names (T3.8). */
public final class ArtemisClassApproach extends ArtemisApproachBase {

    public static final String ID = "artemis-class";
    public static final String VERSION = ArtemisApproachBase.VERSION;

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public ArtemisClassApproach() {
        this(null);
    }

    ArtemisClassApproach(CodeModelClient codeModel) {
        super(ID, Flavor.CLASS, codeModel);
    }
}
