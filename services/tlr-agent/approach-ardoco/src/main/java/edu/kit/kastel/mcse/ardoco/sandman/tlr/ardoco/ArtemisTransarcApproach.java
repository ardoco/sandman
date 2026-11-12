package edu.kit.kastel.mcse.ardoco.sandman.tlr.ardoco;

/** ArTEMiS in TransArC (T3.8): ArTEMiS replaces SWATTR for SAD to SAM; SAM to code and the transitive SAD to code stay. */
public final class ArtemisTransarcApproach extends ArtemisApproachBase {

    public static final String ID = "artemis-transarc";
    public static final String VERSION = ArtemisApproachBase.VERSION;

    /** ServiceLoader entry: settings from the environment, read when the first run needs them. */
    public ArtemisTransarcApproach() {
        this(null);
    }

    ArtemisTransarcApproach(CodeModelClient codeModel) {
        super(ID, Flavor.TRANSARC, codeModel);
    }
}
