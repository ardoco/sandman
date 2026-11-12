package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

/** ArTEMiS SAD to SAM (T3.8): named components of each architecture document against the components of each model. */
public final class ArtemisApproach extends ArtemisApproachBase {

    public static final String ID = "artemis";
    public static final String VERSION = ArtemisApproachBase.VERSION;

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public ArtemisApproach() {
        this(null);
    }

    ArtemisApproach(CodeModelClient codeModel) {
        super(ID, Flavor.COMPONENT, codeModel);
    }
}
