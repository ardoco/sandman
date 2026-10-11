package edu.kit.kastel.mcse.ardoco.magika;

/**
 * Test only, and not part of the test jar. The class initializer of ArDoCo's {@code CodeExtractor} creates one of
 * these, so the real {@code Ardocode} runner (the reference of BenchmarkEquivalenceTest) cannot start without Magika
 * and onnxruntime. Reading an ACM never calls it. The production code of this module does not use that runner.
 */
public class FileTypePredictor {

    public FileTypePredictor() {}
}
