package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

/** ArTEMiS SAD to code by file names (T3.8). */
public final class ArtemisDatafileApproach extends ArtemisApproachBase {

    public static final String ID = "artemis-datafile";
    public static final String VERSION = ArtemisApproachBase.VERSION;

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public ArtemisDatafileApproach() {
        this(null);
    }

    ArtemisDatafileApproach(CodeModelClient codeModel) {
        super(ID, Flavor.DATAFILE, codeModel);
    }
}
